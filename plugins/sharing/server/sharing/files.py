"""File-type classification, magic-byte validation, and active-content sandboxing.

Every byte served on ``/s/<token>`` or ``/x/sharing/withme/<wid>/`` passes
through here so shared HTML, SVG, PDF and arbitrary uploads can never execute
script in Loam's or the share page's origin, read cookies or storage, or fetch
Loam routes.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

# Strict Content-Security-Policy headers per delivery mode:
# - HTML_FRAME_CSP: sandbox WITHOUT allow-same-origin -> opaque `null` origin
#   both inside <iframe sandbox="allow-scripts allow-forms"> AND if opened directly!
#   connect-src 'none' blocks all fetch/XHR/WS/beacon; default-src 'none' blocks
#   external resources while allowing interactive single-file mockups (inline
#   JS/CSS, data:/blob: images/fonts/media).
HTML_FRAME_CSP = (
    "sandbox allow-scripts allow-forms; default-src 'none'; "
    "script-src 'unsafe-inline' 'unsafe-eval' data: blob:; "
    "style-src 'unsafe-inline' data: blob:; "
    "img-src data: blob:; font-src data: blob:; media-src data: blob:; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)

# - SVG_RAW_CSP: sandbox WITHOUT allow-scripts and WITHOUT allow-same-origin.
#   Rendered in <img> on the share page; even if opened directly in a tab,
#   inline <script>, onload handlers, javascript: links and network loads are dead.
SVG_RAW_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"

# - PDF_RAW_CSP: embedded in <iframe src=".../raw"> on same origin, no external fetches.
PDF_RAW_CSP = "default-src 'none'; object-src 'self'; frame-ancestors 'self'"

# - MEDIA_RAW_CSP: images, audio, video.
MEDIA_RAW_CSP = "sandbox; default-src 'none'; media-src 'self'"

# - ATTACH_CSP: downloads and unknown/spoofed files.
ATTACH_CSP = "sandbox; default-src 'none'"

MARKDOWN_EXTS = {".md", ".markdown"}
HTML_EXTS = {".html", ".htm"}
SVG_EXTS = {".svg"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".ico"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".oga", ".m4a", ".flac", ".aac", ".opus"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".ogv"}
TEXT_EXTS = {
    ".txt", ".text", ".log", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env", ".xml", ".css", ".scss", ".less",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".rb", ".go", ".rs",
    ".java", ".kt", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".php",
    ".sh", ".bash", ".zsh", ".fish", ".sql", ".graphql", ".proto", ".dockerfile",
    ".makefile", ".mk", ".r", ".lua", ".pl", ".ex", ".exs", ".erl", ".hs",
    ".scala", ".clj", ".vim", ".tex", ".rst", ".org", ".diff", ".patch",
}
TEXT_NAMES = {"readme", "license", "makefile", "dockerfile", "gemfile", "rakefile", "procfile"}

IMAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}
AUDIO_MIMES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".opus": "audio/opus",
}
VIDEO_MIMES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".ogv": "video/ogg",
}

KIND_LABELS = {
    "markdown": "Markdown",
    "image": "Image",
    "svg": "SVG image",
    "pdf": "PDF document",
    "html": "HTML page",
    "audio": "Audio",
    "video": "Video",
    "text": "Text",
    "other": "File",
}


def ext_of(name: str) -> str:
    return Path(name or "").suffix.lower()


def classify(name: str) -> str:
    """Return one of: markdown, image, svg, pdf, html, audio, video, text, other."""
    ext = ext_of(name)
    if ext in MARKDOWN_EXTS:
        return "markdown"
    if ext in SVG_EXTS:
        return "svg"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in HTML_EXTS:
        return "html"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in TEXT_EXTS or (Path(name or "").name.lower() in TEXT_NAMES):
        return "text"
    return "other"


def label_of(name: str) -> str:
    kind = classify(name)
    if kind == "text":
        ext = ext_of(name).lstrip(".").upper()
        return f"{ext} file" if ext else "Text file"
    if kind == "other":
        ext = ext_of(name).lstrip(".").upper()
        return f"{ext} file" if ext else "File"
    return KIND_LABELS[kind]


def safe_filename(name: str) -> str:
    base = Path(name or "file").name
    clean = re.sub(r'[\x00-\x1f\x7f"\\/;]', "_", base).strip(" .")
    return (clean or "file")[:180]


def verify_magic(kind: str, ext: str, data: bytes) -> bool:
    """Sniff leading bytes so a renamed HTML or script file can never be served
    as an inline image, SVG or PDF."""
    head = data[:1024]
    if kind == "pdf":
        return b"%PDF-" in head
    if kind == "svg":
        stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        return stripped.startswith(b"<svg") or (stripped.startswith(b"<?xml") and b"<svg" in data[:4096].lower())
    if kind == "image":
        if ext == ".png":
            return head.startswith(b"\x89PNG\r\n\x1a\n")
        if ext in (".jpg", ".jpeg"):
            return head.startswith(b"\xff\xd8\xff")
        if ext == ".gif":
            return head.startswith((b"GIF87a", b"GIF89a"))
        if ext == ".webp":
            return head.startswith(b"RIFF") and head[8:12] == b"WEBP"
        if ext == ".bmp":
            return head.startswith(b"BM")
        if ext == ".ico":
            return head.startswith((b"\x00\x00\x01\x00", b"\x00\x00\x02\x00"))
        if ext == ".avif":
            return len(head) >= 12 and head[4:8] == b"ftyp"
        return False
    return True


def raw_response_meta(name: str, data: bytes | None = None, *, is_range: bool = False) -> tuple[str, list[tuple[str, str]]]:
    """Return ``(content_type, headers)`` for ``/raw`` delivery of a file.
    HTML and unrecognized/spoofed files are forced to safe attachment download."""
    kind = classify(name)
    ext = ext_of(name)
    fname = safe_filename(name)

    if kind == "svg":
        if data is not None and not is_range and not verify_magic("svg", ext, data):
            return _attachment(fname)
        return "image/svg+xml; charset=utf-8", [
            ("Content-Disposition", f'inline; filename="{fname}"'),
            ("Content-Security-Policy", SVG_RAW_CSP),
        ]

    if kind == "image":
        if data is not None and not is_range and not verify_magic("image", ext, data):
            return _attachment(fname)
        return IMAGE_MIMES.get(ext, "application/octet-stream"), [
            ("Content-Disposition", f'inline; filename="{fname}"'),
            ("Content-Security-Policy", MEDIA_RAW_CSP),
        ]

    if kind == "pdf":
        if data is not None and not is_range and not verify_magic("pdf", ext, data):
            return _attachment(fname)
        return "application/pdf", [
            ("Content-Disposition", f'inline; filename="{fname}"'),
            ("Content-Security-Policy", PDF_RAW_CSP),
            ("X-Frame-Options", "SAMEORIGIN"),
        ]

    if kind == "audio":
        return AUDIO_MIMES.get(ext, mimetypes.guess_type(name)[0] or "audio/mpeg"), [
            ("Content-Disposition", f'inline; filename="{fname}"'),
            ("Content-Security-Policy", MEDIA_RAW_CSP),
        ]

    if kind == "video":
        return VIDEO_MIMES.get(ext, mimetypes.guess_type(name)[0] or "video/mp4"), [
            ("Content-Disposition", f'inline; filename="{fname}"'),
            ("Content-Security-Policy", MEDIA_RAW_CSP),
        ]

    if kind in ("text", "markdown"):
        return "text/plain; charset=utf-8", [
            ("Content-Disposition", f'inline; filename="{fname}"'),
            ("Content-Security-Policy", ATTACH_CSP),
        ]

    # HTML on /raw (never served as text/html here; HTML renders only via /frame)
    # and unknown/binary files: always forced download.
    return _attachment(fname)


def _attachment(fname: str) -> tuple[str, list[tuple[str, str]]]:
    return "application/octet-stream", [
        ("Content-Disposition", f'attachment; filename="{fname}"'),
        ("Content-Security-Policy", ATTACH_CSP),
    ]


def download_response_meta(name: str) -> tuple[str, list[tuple[str, str]]]:
    return _attachment(safe_filename(name))


def frame_response_meta() -> tuple[str, list[tuple[str, str]]]:
    return "text/html; charset=utf-8", [
        ("Content-Security-Policy", HTML_FRAME_CSP),
        ("X-Frame-Options", "SAMEORIGIN"),
    ]
