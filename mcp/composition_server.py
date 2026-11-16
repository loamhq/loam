#!/usr/bin/env python3
"""Wren's craft path as a shared MCP server: image, PDF, fonts, page render.

Ported from ``chassis.image_gen`` and ``chassis.pdf_gen``. Two deliberate
changes from the chassis version, both ratified 2026-08-25:

* **No per-agent allowlist.** On the chassis these were the only scoped tool
  grants in the system, gated to one persona for craft ownership rather than for
  safety. Here every agent gets all four.
* **Write targets are not fenced** (since 2026-11-08, "the authority
  move"; until then they were held to the agent's own workspace, the
  commons and the vault). A target's filename is still checked for the
  characters Android cannot store.

``render_typst_pages`` is the one that closes the loop -- it is how an agent
*sees* what it composed. Composing without preview is working blind.

The verbatim stdout/stderr passthrough on the Typst tools is the contract,
not a nicety: it is what lets an agent read its own compile diagnostics and
fix its own source. The parsed ``diagnostics`` overlay is a convenience; a
parse miss never affects the raw text.

Configured by environment: LOAM_AGENT, LOAM_AGENT_WORKSPACE, LOAM_VAULT,
LOAM_AI_LABS, OPENAI_API_KEY, GEMINI_API_KEY, and LOAM_IMAGE_BACKEND
(``openai`` by default, ``gemini`` to revert).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import sys as _sys
_sys.path.append(str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402  constants only; nothing here holds state

from mcp.server import MCPServer

# Secrets live outside every git tree and outside the agent's reach. Not in
# .mcp.json, which is committed to ai-labs; not read from the chassis's
# /etc/chassis/secrets.env, because a runtime built to let the chassis go
# cold should not depend on a chassis-owned path.
SECRETS_FILE = Path(
    os.environ.get("LOAM_SECRETS_FILE") or lc.SECRETS_ENV
)


def _load_secrets() -> None:
    """Populate missing env vars from the secrets file. Never overrides."""
    try:
        text = SECRETS_FILE.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_secrets()

AGENT = os.environ.get("LOAM_AGENT", "unknown")
WORKSPACE = Path(os.environ.get("LOAM_AGENT_WORKSPACE", "/nonexistent")).resolve()
VAULT = Path(os.environ.get("LOAM_VAULT") or lc.VAULT).resolve()
AI_LABS = Path(os.environ.get("LOAM_AI_LABS") or lc.WORKSPACES).resolve()
COMMONS = AI_LABS / "commons"
CONVERSATIONS = VAULT / "15_CONVERSATIONS"

# Which image backend `generate_image` routes to. `openai` since 2026-09-03;
# `gemini` is the revert, and the Gemini code below is kept live rather than
# commented out so the flip is a config change and not a rebuild.
IMAGE_BACKEND = (os.environ.get("LOAM_IMAGE_BACKEND") or "openai").strip().lower()

OPENAI_IMAGE_MODEL = os.environ.get("LOAM_OPENAI_IMAGE_MODEL") or "gpt-image-2"
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE") or "https://api.openai.com/v1"

GEMINI_IMAGE_MODEL = "gemini-3-pro-image-preview"
GEMINI_IMAGE_SIZE = "2K"
DEFAULT_ASPECT_RATIO = "1:1"
SUPPORTED_ASPECT_RATIOS = (
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
)

SUPPORTED_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
_MIME_TO_FORMAT = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
# What the OpenAI Image API calls each of our extensions.
_EXT_TO_OPENAI_FORMAT = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}
_EXT_TO_MIME = {".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp"}

# gpt-image-2, verified against the live API 2026-09-02 by probing with
# deliberately invalid values and reading what it said back:
#   quality        low | medium | high | auto
#   output_format  png | webp | jpeg
#   background     transparent | opaque | auto
#   moderation     auto | low
#   n              <= 10
#   size           WxH, both edges divisible by 16, longest edge <= 3840,
#                  aspect ratio <= 3:1
# `input_fidelity` and `response_format` are rejected as unknown parameters
# on this model -- fidelity is automatic and output is always b64_json.
OPENAI_QUALITIES = ("low", "medium", "high", "auto")
OPENAI_BACKGROUNDS = ("transparent", "opaque", "auto")
OPENAI_MODERATIONS = ("auto", "low")
DEFAULT_QUALITY = "high"
DEFAULT_SIZE = "1024x1024"
MAX_N = 10
SIZE_MULTIPLE = 16
SIZE_MAX_EDGE = 3840
SIZE_MAX_RATIO = 3.0
_SIZE_RE = re.compile(r"^\s*(\d{1,5})\s*[xX×]\s*(\d{1,5})\s*$")
# There is also a minimum *pixel budget* -- 512x512 is refused, 1024x1024 is
# not -- and the API's own words for it are "the current minimum pixel
# budget", i.e. a number it reserves the right to move. It is deliberately
# NOT mirrored here: a client-side copy of a server-side limit becomes a lie
# the first time the server changes it. The API's message is relayed verbatim.

# $ per token. Three rates, not two: the API meters text input, image input
# and output separately and reports the split in `usage`, so a flat
# input rate would misprice every edit. Verified against OpenAI's pricing
# page 2026-09-02.
PRICE_TEXT_INPUT = 5.00 / 1_000_000
PRICE_IMAGE_INPUT = 8.00 / 1_000_000
PRICE_CACHED_INPUT = 2.00 / 1_000_000
PRICE_OUTPUT = 30.00 / 1_000_000

# Every call, one line, next to the wake logs. Image generation is the only
# metered API line in an otherwise subscription-shaped system, and the price
# per image now spans ~35x by quality, so "what did images cost this month"
# has to be answerable without a dashboard. The raw usage block is logged
# alongside the computed cost so the arithmetic can be re-derived if the
# rates move.
IMAGE_LOG = Path(
    os.environ.get("LOAM_IMAGE_LOG") or lc.LOGS / "image-gen.jsonl"
)

# Nano Banana Pro lands in the 25-30s band at 2K; slow periods and
# high-detail prompts push past it. A 30s cap timed out every call on
# 2026-06-05 -- keep the ceiling generous and bound only a hung edition.
# gpt-image-2 at `high` and 4K is slower still, and `n` multiplies it.
GENERATION_TIMEOUT_S = 600.0

TYPST_BIN_FALLBACK = "/usr/local/bin/typst"
COMPILE_TIMEOUT_S = 120.0
FONTS_TIMEOUT_S = 5.0
RENDER_TIMEOUT_S = 60.0
MIN_DPI, MAX_DPI, DEFAULT_DPI = 50, 600, 150

# Illegal on Android at the filesystem layer. A vault file carrying one of
# these is permanently invisible on the owner's phone, so it is refused at
# write time rather than discovered later.
ANDROID_ILLEGAL = set('?:*"<>|')

_SEVERITY_RE = re.compile(r"^(error|warning): (.*)$")
_LOCATION_RE = re.compile(r"┌─\s+(.+):(\d+):(\d+)")
_RENDER_LEAF_RE = re.compile(r"^page-(\d+)\.png$")
_PAGES_RE = re.compile(r"^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$")

mcp = MCPServer("loam-composition")


# ---------- boundary ----------


def _error(kind: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": kind, "detail": message}


def _readable(path: str) -> Path | None:
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    if resolved.is_relative_to(VAULT) or resolved.is_relative_to(AI_LABS):
        return resolved
    return None


def _writable(path: str) -> tuple[Path | None, str | None]:
    """Resolve a write target. No path boundary since 2026-11-08 ("the
    authority move"); the Android filename check stays, because a file with
    one of those characters never reaches the phone."""
    try:
        resolved = Path(os.path.abspath(path)).resolve()
    except OSError:
        return None, f"could not resolve {path!r}"

    bad = sorted(set(resolved.name) & ANDROID_ILLEGAL)
    if bad:
        return None, (
            f"filename {resolved.name!r} contains {''.join(bad)!r}, which "
            f"Android cannot store — the file would be permanently invisible "
            f"on the phone. Rename it."
        )

    return resolved, None


# ---------- image ----------


def _validate_size(size: str) -> tuple[tuple[int, int] | None, str | None]:
    """Free-form ``WIDTHxHEIGHT`` against the API's stable constraints.

    Deliberately not a preset list. A wide Quire banner, a phone-tall
    workout card and a square emblem are all the same call with a different
    number, and curating a menu of "supported sizes" would be this server
    inventing a limit the model does not have.
    """
    m = _SIZE_RE.match(size or "")
    if not m:
        return None, (
            f"size must be WIDTHxHEIGHT, e.g. '1536x1024'; got {size!r}"
        )
    w, h = int(m.group(1)), int(m.group(2))
    if w == 0 or h == 0:
        return None, f"size {size!r} has a zero edge"
    if w % SIZE_MULTIPLE or h % SIZE_MULTIPLE:
        return None, (
            f"size {size!r}: both edges must be multiples of {SIZE_MULTIPLE}. "
            f"Nearest legal: {max(SIZE_MULTIPLE, round(w / SIZE_MULTIPLE) * SIZE_MULTIPLE)}"
            f"x{max(SIZE_MULTIPLE, round(h / SIZE_MULTIPLE) * SIZE_MULTIPLE)}"
        )
    if max(w, h) > SIZE_MAX_EDGE:
        return None, (
            f"size {size!r}: the longest edge must be at most {SIZE_MAX_EDGE}px"
        )
    ratio = max(w, h) / min(w, h)
    if ratio > SIZE_MAX_RATIO + 1e-9:
        return None, (
            f"size {size!r} is {ratio:.2f}:1; the widest supported aspect "
            f"ratio is {SIZE_MAX_RATIO:.0f}:1"
        )
    return (w, h), None


def _nearest_aspect_ratio(w: int, h: int) -> str:
    """Closest Gemini aspect ratio to a free-form size. Revert path only."""
    target = w / h
    best, best_err = DEFAULT_ASPECT_RATIO, float("inf")
    for label in SUPPORTED_ASPECT_RATIOS:
        a, b = label.split(":")
        err = abs((int(a) / int(b)) - target)
        if err < best_err:
            best, best_err = label, err
    return best


def _unique(path: Path) -> Path:
    """The first free name at or after ``path``.

    Outputs are new files, never overwrites: a source that took a prompt and
    a dollar to make is not something a later call gets to silently destroy,
    and a chain of files is its own provenance. The suffix stays in the same
    directory as the caller's target, which is what keeps the boundary check
    on ``target_path`` sufficient for every path actually written.
    """
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"could not find a free filename beside {path}")


def _cost_usd(usage: dict[str, Any] | None) -> float | None:
    """Dollars for one API call, from the usage block it returned."""
    if not isinstance(usage, dict):
        return None
    details = usage.get("input_tokens_details") or {}
    text_in = int(details.get("text_tokens") or 0)
    image_in = int(details.get("image_tokens") or 0)
    cached = int(details.get("cached_tokens") or 0)
    output = int(usage.get("output_tokens") or 0)
    # Cached input, when reported, is a discounted slice of the image input
    # rather than an addition to it. Guarded so an unexpected shape cannot
    # produce a negative charge.
    billed_image_in = max(image_in - cached, 0) if 0 < cached <= image_in else image_in
    cached_billed = cached if 0 < cached <= image_in else 0
    return round(
        text_in * PRICE_TEXT_INPUT
        + billed_image_in * PRICE_IMAGE_INPUT
        + cached_billed * PRICE_CACHED_INPUT
        + output * PRICE_OUTPUT,
        6,
    )


def _log_image_call(record: dict[str, Any]) -> None:
    """One JSONL line per call. Never fails the call it is recording."""
    try:
        IMAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with IMAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _check_mask(mask_path: Path, first_source: Path) -> str | None:
    """The API's two mask rules, checked here so the failure is readable.

    A mask that is the wrong size or has no alpha comes back from the API as
    a generic 400 that does not say which of the two it was. Both are cheap
    to check locally and the message is the whole value.
    """
    try:
        from PIL import Image
        with Image.open(mask_path) as m:
            mode, msize = m.mode, m.size
        with Image.open(first_source) as s:
            ssize = s.size
    except Exception as exc:
        return f"could not read mask or source image: {exc}"
    if "A" not in mode:
        return (
            f"mask {mask_path.name} is mode {mode!r} and has no alpha channel. "
            f"A mask marks the region to repaint with TRANSPARENT pixels, so "
            f"it must be an RGBA PNG."
        )
    if msize != ssize:
        return (
            f"mask is {msize[0]}x{msize[1]} but the first source image is "
            f"{ssize[0]}x{ssize[1]}; they must match exactly."
        )
    return None


def _openai_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    return key.strip() if key and key.strip() else None


async def _openai_image(
    *,
    prompt: str,
    sources: list[Path],
    mask: Path | None,
    size: str,
    quality: str,
    n: int,
    out_format: str,
    background: str,
    output_compression: int | None,
    moderation: str | None,
) -> tuple[list[bytes] | None, dict[str, Any] | None, str | None, str | None]:
    """``(images, usage, error_kind, error_detail)``.

    Routes on ``sources``: none goes to /images/generations, any goes to
    /images/edits. That split is OpenAI's implementation detail, not a
    distinction the agent should have to hold.
    """
    import httpx

    key = _openai_key()
    if key is None:
        return None, None, "environment", (
            "OPENAI_API_KEY is not set for this server. It is read from "
            f"{SECRETS_FILE} at startup."
        )

    headers = {"Authorization": f"Bearer {key}"}
    common = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "n": str(n),
        "output_format": out_format,
        "background": background,
    }
    if output_compression is not None:
        common["output_compression"] = str(output_compression)
    if moderation is not None:
        common["moderation"] = moderation

    opened: list[Any] = []
    try:
        async with httpx.AsyncClient(timeout=GENERATION_TIMEOUT_S) as client:
            if sources:
                files = []
                for src in sources:
                    fh = src.open("rb")
                    opened.append(fh)
                    # The declared mimetype has to be the real one. A generic
                    # application/octet-stream is refused outright ("Supported
                    # file formats are 'image/jpeg', 'image/png', and
                    # 'image/webp'"), which is why every mode that takes a
                    # source image failed until this map existed.
                    files.append(
                        ("image[]", (src.name, fh, _EXT_TO_MIME[src.suffix.lower()]))
                    )
                if mask is not None:
                    fh = mask.open("rb")
                    opened.append(fh)
                    files.append(("mask", (mask.name, fh, "image/png")))
                resp = await client.post(
                    f"{OPENAI_API_BASE}/images/edits",
                    headers=headers,
                    data={k: v for k, v in common.items()},
                    files=files,
                )
            else:
                payload = dict(common)
                payload["n"] = n
                if output_compression is not None:
                    payload["output_compression"] = output_compression
                resp = await client.post(
                    f"{OPENAI_API_BASE}/images/generations",
                    headers=headers,
                    json=payload,
                )
    except (asyncio.TimeoutError, TimeoutError):
        return None, None, "timeout", (
            f"image generation exceeded {GENERATION_TIMEOUT_S:.0f}s"
        )
    except Exception as exc:
        return None, None, "api_error", f"{type(exc).__name__}: {exc}"
    finally:
        for fh in opened:
            with contextlib.suppress(Exception):
                fh.close()

    try:
        body = resp.json()
    except Exception:
        return None, None, "api_error", (
            f"HTTP {resp.status_code}: {resp.text[:800]}"
        )

    if resp.status_code >= 400 or "error" in body:
        err = body.get("error") or {}
        message = err.get("message") or resp.text[:800]
        # The API's own words, verbatim: it is the authority on its limits
        # and its refusals, and paraphrasing either would be this server
        # guessing on the agent's behalf.
        kind = "content_policy" if err.get("code") in (
            "moderation_blocked", "content_policy_violation",
        ) else ("validation" if resp.status_code == 400 else "api_error")
        return None, None, kind, f"{message} (HTTP {resp.status_code})"

    usage = body.get("usage")
    images: list[bytes] = []
    for item in body.get("data") or []:
        blob = item.get("b64_json")
        if blob:
            images.append(base64.b64decode(blob))
    if not images:
        return None, usage, "api_error", "the API returned no image data"
    return images, usage, None, None


def _extract_image(response: Any):
    """``(bytes, mime_type, refusal)``. A blocked prompt or generation
    surfaces as a refusal string rather than an exception. Gemini path."""
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        reason = feedback.block_reason
        msg = getattr(feedback, "block_reason_message", None) or str(reason)
        return None, None, f"prompt blocked ({_enum_name(reason)}): {msg}"
    for cand in getattr(response, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []) if content else []:
            blob = getattr(part, "inline_data", None)
            if blob is not None and getattr(blob, "data", None):
                return blob.data, getattr(blob, "mime_type", None), None
        finish = getattr(cand, "finish_reason", None)
        if finish is not None and _enum_name(finish) not in (
            "STOP", "FINISH_REASON_UNSPECIFIED",
        ):
            fmsg = getattr(cand, "finish_message", None) or _enum_name(finish)
            return None, None, f"generation stopped ({_enum_name(finish)}): {fmsg}"
    return None, None, "the model returned no image"


_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client()
    return _client


def _enum_name(value: Any) -> str:
    return getattr(value, "name", None) or str(value)


def _write_image(data: bytes, mime_type: str | None, target: Path, fmt: str) -> None:
    if _MIME_TO_FORMAT.get((mime_type or "").lower()) == fmt:
        target.write_bytes(data)
        return
    from PIL import Image
    with Image.open(io.BytesIO(data)) as img:
        if fmt == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(target, format=fmt)


async def _gemini_image(prompt: str, aspect_ratio: str):
    """``(bytes, mime_type, error_kind, error_detail)``. The revert path."""
    try:
        client = _get_client()
    except Exception as exc:
        return None, None, "api_error", f"image client unavailable: {exc}"

    from google.genai import types

    config = types.GenerateContentConfig(
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(
            aspect_ratio=aspect_ratio, image_size=GEMINI_IMAGE_SIZE
        ),
    )
    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=GEMINI_IMAGE_MODEL, contents=prompt, config=config
            ),
            timeout=GENERATION_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, TimeoutError):
        return None, None, "timeout", (
            f"image generation exceeded {GENERATION_TIMEOUT_S:.0f}s"
        )
    except Exception as exc:
        return None, None, "api_error", f"{type(exc).__name__}: {exc}"

    data, mime_type, refusal = _extract_image(response)
    if data is None:
        return None, None, "content_policy", refusal or "the model returned no image"
    return data, mime_type, None, None


@mcp.tool(
    name="generate_image",
    description=(
        "Make an image and write it to a file in your namespace or the "
        "vault. One tool for every mode — whether you are starting from "
        "nothing or from pictures you already have is just a parameter.\n"
        "\n"
        "  • From scratch — prompt alone.\n"
        "      prompt='a cold blue dawn over flooded rice terraces, "
        "high-contrast woodblock print'\n"
        "  • Edit an image — pass it in `source_images` and say what to "
        "change.\n"
        "      source_images=['/…/wren/covers/quire-04.png'], "
        "prompt='make the sky storm-dark and add rain'\n"
        "  • In the style of — pass a reference and describe the NEW "
        "subject.\n"
        "      source_images=['/…/wren/covers/quire-04.png'], "
        "prompt='a lighthouse at night, in the style of the reference'\n"
        "  • Combine several — pass them all; the prompt says how they "
        "relate.\n"
        "      source_images=['/…/a.png', '/…/b.png'], prompt='put the "
        "figure from the first into the room from the second'\n"
        "  • Region edit — add `mask`, a PNG the same size as the first "
        "source whose TRANSPARENT areas are the parts to repaint.\n"
        "      source_images=['/…/poster.png'], mask='/…/poster-mask.png', "
        "prompt='replace the masked sign with a brass plaque'\n"
        "\n"
        "The loop that makes this worth using: generate, `Read` the file to "
        "actually look at what you got, then call again with it as a source "
        "to revise. You can read any image in the vault or ai-labs, "
        "including another agent's, so 'the cover Wren made' is just a "
        "path.\n"
        "\n"
        "`size` is free-form 'WIDTHxHEIGHT' — make the shape the work "
        "wants (wide banner, tall card, square emblem). Both edges must be "
        "multiples of 16, the longest at most 3840, the aspect ratio at "
        "most 3:1. `quality` defaults to 'high', which is the point of "
        "this tool; 'medium' and 'low' exist for throwaway roughs. `n` "
        "generates several candidates in one call so you can pick. "
        "`background='transparent'` gives a cut-out PNG or WEBP for "
        "compositing.\n"
        "\n"
        "Outputs never overwrite: if `target_path` is taken the file lands "
        "beside it as `name-2.png`, and the paths actually written come "
        "back in the result. Your sources are never modified. The result "
        "also carries what the call cost in dollars — worth reading, since "
        "a high-quality image is roughly 35x the price of a low one."
    ),
)
async def generate_image(
    prompt: str,
    target_path: str,
    source_images: list[str] | None = None,
    mask: str | None = None,
    size: str = DEFAULT_SIZE,
    quality: str = DEFAULT_QUALITY,
    n: int = 1,
    background: str = "auto",
    output_compression: int | None = None,
    moderation: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()

    if not prompt or not prompt.strip():
        return _error("validation", "prompt must be a non-empty string")
    if not target_path or not Path(target_path).is_absolute():
        return _error("validation", f"target_path must be absolute; got {target_path!r}")
    suffix = Path(target_path).suffix.lower()
    fmt = SUPPORTED_EXTENSIONS.get(suffix)
    if fmt is None:
        return _error(
            "validation",
            f"target_path must end in one of {sorted(SUPPORTED_EXTENSIONS)}; "
            f"got {Path(target_path).suffix or '(none)'!r}",
        )

    dims, why = _validate_size(size)
    if dims is None:
        return _error("validation", why or f"bad size {size!r}")

    if quality not in OPENAI_QUALITIES:
        return _error(
            "validation",
            f"unsupported quality {quality!r}; use one of {list(OPENAI_QUALITIES)}",
        )
    if background not in OPENAI_BACKGROUNDS:
        return _error(
            "validation",
            f"unsupported background {background!r}; "
            f"use one of {list(OPENAI_BACKGROUNDS)}",
        )
    if background == "transparent" and suffix in (".jpg", ".jpeg"):
        return _error(
            "validation",
            "background='transparent' needs a format with an alpha channel; "
            "use a .png or .webp target_path.",
        )
    if moderation is not None and moderation not in OPENAI_MODERATIONS:
        return _error(
            "validation",
            f"unsupported moderation {moderation!r}; "
            f"use one of {list(OPENAI_MODERATIONS)}",
        )
    if not isinstance(n, int) or isinstance(n, bool) or not (1 <= n <= MAX_N):
        return _error("validation", f"n must be an integer in 1-{MAX_N}; got {n!r}")
    if output_compression is not None and (
        not isinstance(output_compression, int)
        or isinstance(output_compression, bool)
        or not (0 <= output_compression <= 100)
    ):
        return _error(
            "validation",
            f"output_compression must be an integer in 0-100; got {output_compression!r}",
        )

    # Sources are reads. Reads are open across the whole vault and all of
    # ai-labs by design -- an agent reaching for a sibling's cover art as a
    # style reference is the capability working, not a breach -- so this
    # checks readability, not ownership.
    sources: list[Path] = []
    for raw in source_images or []:
        if not isinstance(raw, str) or not raw.strip():
            return _error("validation", "each entry in source_images must be a path")
        if not Path(raw).is_absolute():
            return _error("validation", f"source_images paths must be absolute; got {raw!r}")
        resolved_src = _readable(raw)
        if resolved_src is None:
            return _error(
                "boundary", f"source image {raw!r} is outside the vault and ai-labs."
            )
        if not resolved_src.is_file() or not os.access(resolved_src, os.R_OK):
            return _error(
                "validation", f"source image {raw!r} does not exist or is not readable."
            )
        if resolved_src.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return _error(
                "validation",
                f"source image {raw!r} must be one of {sorted(SUPPORTED_EXTENSIONS)}",
            )
        sources.append(resolved_src)

    mask_path: Path | None = None
    if mask is not None:
        if not sources:
            return _error(
                "validation",
                "mask marks a region of an existing image, so it only means "
                "something alongside source_images. Pass the image to edit "
                "in source_images.",
            )
        if not Path(mask).is_absolute():
            return _error("validation", f"mask must be an absolute path; got {mask!r}")
        mask_path = _readable(mask)
        if mask_path is None:
            return _error("boundary", f"mask {mask!r} is outside the vault and ai-labs.")
        if not mask_path.is_file() or not os.access(mask_path, os.R_OK):
            return _error("validation", f"mask {mask!r} does not exist or is not readable.")
        if mask_path.suffix.lower() != ".png":
            return _error(
                "validation",
                f"mask must be a .png — it is the alpha channel that marks "
                f"the region; got {mask_path.suffix or '(none)'!r}",
            )
        problem = _check_mask(mask_path, sources[0])
        if problem:
            return _error("validation", problem)

    # Every file this tool writes lands in target_path's own directory (see
    # _unique), so checking target_path's name covers all of them.
    resolved, why = _writable(target_path)
    if resolved is None:
        return _error("boundary", why or "target is out of bounds")

    for src in sources:
        if src == resolved:
            return _error(
                "validation",
                f"target_path is the source image {src}. Outputs never "
                f"overwrite their sources — give the result a new name.",
            )

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _error("write_error", f"could not create parent directory: {exc}")

    mode = "generate" if not sources else ("edit_masked" if mask_path else "edit")
    log: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "agent": AGENT,
        "backend": IMAGE_BACKEND,
        "mode": mode,
        "quality": quality,
        "size": size,
        "n": n,
        "sources": [str(p) for p in sources],
        "mask": str(mask_path) if mask_path else None,
        "target_path": str(resolved),
    }

    if IMAGE_BACKEND == "gemini":
        # The revert. Gemini has no edits endpoint and no mask, so the modes
        # that only exist on OpenAI say so plainly rather than silently
        # producing something unrelated to the sources.
        if sources or mask_path:
            return _error(
                "unsupported",
                "the gemini backend generates from a prompt only — it has no "
                "edit, style-reference or mask path. Unset LOAM_IMAGE_BACKEND "
                "to use gpt-image-2.",
            )
        if n != 1:
            return _error("unsupported", "the gemini backend generates one image per call")
        aspect_ratio = _nearest_aspect_ratio(*dims)
        data, mime_type, kind, detail = await _gemini_image(prompt, aspect_ratio)
        if data is None:
            log.update({"ok": False, "error": kind, "detail": detail})
            _log_image_call(log)
            return _error(kind or "api_error", detail or "no image")
        out_path = _unique(resolved)
        try:
            _write_image(data, mime_type, out_path, fmt)
        except Exception as exc:
            log.update({"ok": False, "error": "write_error", "detail": str(exc)})
            _log_image_call(log)
            return _error("write_error", f"could not write {out_path}: {exc}")
        log.update({
            "ok": True, "model": GEMINI_IMAGE_MODEL, "paths": [str(out_path)],
            "aspect_ratio": aspect_ratio, "cost_usd": None,
            "elapsed_s": round(time.monotonic() - started, 2),
        })
        _log_image_call(log)
        return {
            "ok": True,
            "paths": [str(out_path)],
            "path": str(out_path),
            "backend": "gemini",
            "model": GEMINI_IMAGE_MODEL,
            "aspect_ratio": aspect_ratio,
            "image_size": GEMINI_IMAGE_SIZE,
            "cost_usd": None,
            "cost_note": "the gemini backend does not report token usage",
            "bytes": [out_path.stat().st_size],
            "elapsed_s": round(time.monotonic() - started, 2),
        }

    images, usage, kind, detail = await _openai_image(
        prompt=prompt,
        sources=sources,
        mask=mask_path,
        size=size,
        quality=quality,
        n=n,
        out_format=_EXT_TO_OPENAI_FORMAT[suffix],
        background=background,
        output_compression=output_compression,
        moderation=moderation,
    )
    if images is None:
        log.update({
            "ok": False, "model": OPENAI_IMAGE_MODEL, "error": kind,
            "detail": detail, "usage": usage, "cost_usd": _cost_usd(usage),
            "elapsed_s": round(time.monotonic() - started, 2),
        })
        _log_image_call(log)
        return _error(kind or "api_error", detail or "no image")

    written: list[Path] = []
    try:
        for data in images:
            out_path = _unique(resolved)
            out_path.write_bytes(data)
            written.append(out_path)
    except Exception as exc:
        log.update({
            "ok": False, "model": OPENAI_IMAGE_MODEL, "error": "write_error",
            "detail": str(exc), "usage": usage, "cost_usd": _cost_usd(usage),
            "paths": [str(p) for p in written],
        })
        _log_image_call(log)
        return _error("write_error", f"could not write image: {exc}")

    cost = _cost_usd(usage)
    log.update({
        "ok": True, "model": OPENAI_IMAGE_MODEL,
        "paths": [str(p) for p in written], "usage": usage, "cost_usd": cost,
        "elapsed_s": round(time.monotonic() - started, 2),
    })
    _log_image_call(log)

    return {
        "ok": True,
        "paths": [str(p) for p in written],
        "path": str(written[0]),
        "backend": "openai",
        "model": OPENAI_IMAGE_MODEL,
        "mode": mode,
        "size": size,
        "quality": quality,
        "cost_usd": cost,
        "usage": usage,
        "bytes": [p.stat().st_size for p in written],
        "elapsed_s": round(time.monotonic() - started, 2),
    }


# ---------- typst ----------


def _typst_bin() -> str:
    return shutil.which("typst") or TYPST_BIN_FALLBACK


def _font_path_args() -> list[str]:
    """``--font-path <workspace>/fonts`` iff it exists.

    Per-agent by construction: any agent that earns a ``fonts/`` directory
    gets it included with no code change.
    """
    font_dir = WORKSPACE / "fonts"
    return ["--font-path", str(font_dir)] if font_dir.is_dir() else []


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


async def _run_typst(args: list[str], timeout: float):
    proc = await asyncio.create_subprocess_exec(
        _typst_bin(), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return None, "", "", True
    return proc.returncode, _decode(out), _decode(err), False


def _parse_diagnostics(stderr: str) -> list[dict[str, Any]]:
    """Convenience overlay on the verbatim text; a parse miss changes nothing."""
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in stderr.splitlines():
        sev = _SEVERITY_RE.match(line.strip())
        if sev:
            current = {
                "severity": sev.group(1), "message": sev.group(2),
                "file": None, "line": None, "column": None,
            }
            records.append(current)
            continue
        loc = _LOCATION_RE.search(line)
        if loc and current is not None:
            current["file"] = loc.group(1)
            current["line"] = int(loc.group(2))
            current["column"] = int(loc.group(3))
            current = None
    return records


@mcp.tool(
    name="generate_pdf",
    description=(
        "Compile a Typst source file to PDF. Provide an absolute path to a "
        "`.typ` source file, an absolute target path ending in `.pdf`, and "
        "optionally a root directory (defaults to the source's parent — set "
        "this wider if your document imports templates or reads files from "
        "outside the source's folder). Fonts from your workspace's `fonts/` "
        "directory are included automatically. Returns the path to the "
        "written PDF on success, or a structured error carrying the full "
        "Typst stdout/stderr so you can read the diagnostics, fix your "
        "source, and recompile."
    ),
)
async def generate_pdf(
    source_path: str, target_path: str, root: str | None = None
) -> dict[str, Any]:
    started = time.monotonic()
    if not source_path or not Path(source_path).is_absolute():
        return _error("validation", f"source_path must be absolute; got {source_path!r}")
    source = _readable(source_path)
    if source is None:
        return _error("boundary", f"source_path {source_path!r} is outside the vault and ai-labs.")
    if not source.is_file() or not os.access(source, os.R_OK):
        return _error("validation", f"source_path {source_path!r} does not exist or is not readable.")
    if source.suffix.lower() != ".typ":
        return _error("validation", f"source_path must end in '.typ'; got {source.suffix or '(none)'!r}")
    if not target_path or not Path(target_path).is_absolute():
        return _error("validation", f"target_path must be absolute; got {target_path!r}")
    if Path(target_path).suffix.lower() != ".pdf":
        return _error("validation", f"target_path must end in '.pdf'; got {Path(target_path).suffix or '(none)'!r}")
    target, why = _writable(target_path)
    if target is None:
        return _error("boundary", why or "target is out of bounds")

    if root:
        if not Path(root).is_absolute():
            return _error("validation", f"root must be an absolute path; got {root!r}")
        root_resolved = _readable(root)
        if root_resolved is None or not root_resolved.is_dir():
            return _error("validation", f"root {root!r} is not a readable directory in the vault or ai-labs.")
    else:
        root_resolved = source.parent

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _error("write_error", f"could not create target directory: {exc}")

    args = ["compile", str(source), str(target), "--root", str(root_resolved), *_font_path_args()]
    try:
        code, out, err, timed_out = await _run_typst(args, timeout=COMPILE_TIMEOUT_S)
    except FileNotFoundError:
        return _error("environment", f"typst executable not found (looked for {_typst_bin()!r})")
    if timed_out:
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        return _error("timeout", f"typst compile timed out after {COMPILE_TIMEOUT_S:.0f}s.")

    ok = code == 0 and target.is_file()
    return {
        "ok": ok,
        "path": str(target) if ok else None,
        "exit_code": code,
        "stdout": out,
        "stderr": err,
        "diagnostics": _parse_diagnostics(err),
        "elapsed_s": round(time.monotonic() - started, 2),
    }


@mcp.tool(
    name="typst_fonts",
    description=(
        "List the fonts Typst can see right now — bundled fonts, system "
        "fonts, and any fonts in your workspace's `fonts/` directory. "
        "Returns one family name per line. Useful before writing a `.typ` "
        "that picks a specific typeface."
    ),
)
async def typst_fonts() -> dict[str, Any]:
    try:
        code, out, err, timed_out = await _run_typst(["fonts", *_font_path_args()], timeout=FONTS_TIMEOUT_S)
    except FileNotFoundError:
        return _error("environment", f"typst executable not found (looked for {_typst_bin()!r})")
    if timed_out:
        return _error("timeout", f"typst fonts timed out after {FONTS_TIMEOUT_S:.0f}s.")
    families = [line.strip() for line in out.splitlines() if line.strip()]
    return {"ok": code == 0, "exit_code": code, "families": families,
            "count": len(families), "stdout": out, "stderr": err}


def _validate_pages(pages: str) -> str | None:
    if not _PAGES_RE.match(pages):
        return (
            f"pages {pages!r} is not valid Typst page-range syntax; "
            f'use e.g. "1", "1-3", "2,5,7-9"'
        )
    return None


@mcp.tool(
    name="render_typst_pages",
    description=(
        "Render a Typst source file to per-page PNG images. Provide an "
        "absolute path to a `.typ` source file and an absolute output "
        "directory; per-page images are written as `page-001.png`, "
        "`page-002.png`, ... (zero-padded to 3 digits, using physical page "
        "numbers from the document). Optional `dpi` sets pixels-per-inch "
        "(default 150, range 50-600). Optional `pages` selects a subset "
        'using Typst page-range syntax (e.g. "1", "1-3", "2,5,7-9"); omit '
        "to render every page. Returns the list of written page paths on "
        "success, or a structured error carrying the full Typst "
        "stdout/stderr. This is how you see what you composed."
    ),
)
async def render_typst_pages(
    source_path: str, output_dir: str, dpi: int = DEFAULT_DPI, pages: str | None = None
) -> dict[str, Any]:
    started = time.monotonic()
    if not source_path or not Path(source_path).is_absolute():
        return _error("validation", f"source_path must be absolute; got {source_path!r}")
    source = _readable(source_path)
    if source is None:
        return _error("boundary", f"source_path {source_path!r} is outside the vault and ai-labs.")
    if not source.is_file() or not os.access(source, os.R_OK):
        return _error("validation", f"source_path {source_path!r} does not exist or is not readable.")
    if source.suffix.lower() != ".typ":
        return _error("validation", f"source_path must end in '.typ'; got {source.suffix or '(none)'!r}")
    if not output_dir or not Path(output_dir).is_absolute():
        return _error("validation", f"output_dir must be absolute; got {output_dir!r}")
    out_dir, why = _writable(output_dir)
    if out_dir is None:
        return _error("boundary", why or "output_dir is out of bounds")
    if not isinstance(dpi, int) or isinstance(dpi, bool) or not (MIN_DPI <= dpi <= MAX_DPI):
        return _error("validation", f"dpi must be an integer in {MIN_DPI}-{MAX_DPI}; got {dpi!r}")
    if pages is not None:
        problem = _validate_pages(pages)
        if problem:
            return _error("validation", problem)

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _error("write_error", f"could not create output directory: {exc}")

    # Render into a hidden temp subdir so a failed render leaves output_dir
    # untouched, then rename in with canonical zero-padding.
    tmp_dir = out_dir / ".render-tmp"
    with contextlib.suppress(OSError):
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    args = ["compile", str(source), str(tmp_dir / "page-{p}.png"),
            "--root", str(source.parent), "--ppi", str(dpi), *_font_path_args()]
    if pages is not None:
        args.extend(["--pages", pages])
    try:
        code, out, err, timed_out = await _run_typst(args, timeout=RENDER_TIMEOUT_S)
    except FileNotFoundError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return _error("environment", f"typst executable not found (looked for {_typst_bin()!r})")
    if timed_out:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return _error("timeout", f"typst render timed out after {RENDER_TIMEOUT_S:.0f}s.")

    written: list[str] = []
    if code == 0:
        for child in sorted(tmp_dir.iterdir()):
            m = _RENDER_LEAF_RE.match(child.name)
            if m is None:
                continue
            dest = out_dir / f"page-{int(m.group(1)):03d}.png"
            child.replace(dest)
            written.append(str(dest))
        written.sort()
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        "ok": code == 0 and bool(written),
        "pages": written,
        "page_count": len(written),
        "dpi": dpi,
        "exit_code": code,
        "stdout": out,
        "stderr": err,
        "diagnostics": _parse_diagnostics(err),
        "elapsed_s": round(time.monotonic() - started, 2),
    }


if __name__ == "__main__":
    mcp.run()
