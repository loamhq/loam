"""Provider readiness and automatic selection shared by Loam's entry points."""

from __future__ import annotations

import shutil

import loam_broker as broker
import loam_codex as codex
import loam_antigravity as antigravity


ORDER = broker.WIRED_PROVIDERS
SETUP = "Connect a provider in Settings › Connections to start a reply."


def available(provider: str, login: dict | None = None) -> tuple[bool, str | None]:
    """A CLI must be installed and have a usable login for automatic routing."""
    binary, read = {
        "claude-code": (broker.CLAUDE, broker.login_state),
        "codex": (codex.CODEX, codex.login_state),
        "antigravity": (antigravity.AGY, antigravity.login_state),
    }[provider]
    if not shutil.which(binary):
        return False, "the CLI is not installed"
    state = read() if login is None else login
    return state["connected"], state["reason"]


def automatic(eligible: tuple[str, ...] = ORDER) -> str | None:
    """Pick the first usable provider in the lane's existing preference order."""
    return next((provider for provider in eligible if available(provider)[0]), None)


def compatible_defaults(provider: str | None, model: str | None,
                        effort: str | None) -> tuple[str | None, str | None]:
    """Use the CLI's own defaults when its catalog cannot take a built-in."""
    if provider == "codex":
        entry = next((row for row in codex.read_models() if row["id"] == model), None)
        return (model, effort if effort in entry["efforts"] else None) if entry else (None, None)
    if provider == "antigravity":
        listed = antigravity.read_models()
        return (model if any(row.get("id") == model for row in listed) else None), None
    return model, effort


def configured(card: dict) -> str | None:
    value = card.get("provider")
    return value if value in ORDER else None


def agent_choice(card: dict, provider: str | None) -> dict:
    # Before explicit Claude was stored, a model or effort with no provider
    # was checked against Claude's catalog. Only use it on that provider.
    model_provider = configured(card) or "claude-code"
    return {"provider": provider,
            **{k: (card[k] if provider == model_provider
                   and isinstance(card.get(k), str) and card[k].strip() else None)
               for k in ("model", "effort")}}
