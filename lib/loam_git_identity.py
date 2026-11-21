"""Resolve commit attribution in order: project identity, owner setting, then the
checkout identity as fallback. For example, an explicit owner setting
can be {"source": "typed", "name": "Example Author", "email": "author@example.org"}.
The setting {"source": "neutral"} opts out of owner attribution.

With no setting, use an unambiguous connected account's no-reply address. With
no connection, use the global identity only when it already has a GitHub no-reply
address. Clean provider attribution only on the job's unsigned, unpublished
commits. Preserve human co-authors and upstream history. Commit metadata and the
credentials used to push are separate choices. See docs/attribution.md.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

KEY = "git"
SOURCES = ("github", "typed")          # an identity; "neutral" is the opt-out
NEUTRAL = "neutral"
NOREPLY_RE = re.compile(r"^(\d+\+)?[A-Za-z0-9-]+@users\.noreply\.github\.com$",
                        re.IGNORECASE)
NAME_MAX, EMAIL_MAX = 100, 254
EMAIL_RE = re.compile(r"^[^\s@<>,\"]+@[^\s@<>,\"]+\.[^\s@<>,\"]+$")
ENGINEER_EMAIL_RE = re.compile(r"^engineer\+[^@\s]+@noreply\.local$", re.IGNORECASE)

# What Codex is handed so it adds no `Co-authored-by: Codex` trailer
# (Codex 0.170: on by default; "" disables it, per its config reference).
CODEX_CONFIG = ('commit_attribution=""',)

# The addresses a provider signs its co-author trailer with.
PROVIDER_EMAILS = ("noreply@anthropic.com", "noreply@openai.com",
                   "codex@openai.com", "antigravity@google.com",
                   "noreply@google.com")
# A provider's GitHub bot, by its no-reply address.
PROVIDER_BOT_RE = re.compile(
    r"^(\d+\+)?(claude|codex|chatgpt-codex-connector|openai-codex|"
    r"google-labs-jules|gemini-code-assist|antigravity|copilot|"
    r"copilot-swe-agent)\[bot\]@users\.noreply\.github\.com$", re.IGNORECASE)
COAUTHOR_RE = re.compile(r"^\s*co-authored-by\s*:\s*(?P<who>.*?)\s*$",
                         re.IGNORECASE)
ADDRESS_RE = re.compile(r"<\s*([^<>\s]+)\s*>")
SESSION_RE = re.compile(r"^\s*claude-session\s*:", re.IGNORECASE)
GENERATED_RE = re.compile(
    r"^\s*(?:\S+\s+)?generated (?:with|by) \[?(?:claude code|claude|codex|"
    r"antigravity|gemini)\b.*$", re.IGNORECASE)


class IdentityError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# -- the setting --------------------------------------------------------------

def settings_path() -> Path:
    if os.environ.get("LOAM_HOST_SETTINGS"):
        return Path(os.environ["LOAM_HOST_SETTINGS"])
    import loam_config as lc  # noqa: PLC0415 -- not for the hook, below
    return lc.STATE / "broker" / "settings.json"


def _raw():
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get(KEY) if isinstance(data, dict) else None


def opted_out(raw=None) -> bool:
    raw = _raw() if raw is None else raw
    return isinstance(raw, dict) and raw.get("source") == NEUTRAL


def stored(raw=None) -> dict | None:
    """The owner's explicit identity as stored, or None when unset, opted
    out, or not whole. Only this is an explicit choice."""
    if raw is None:
        raw = _raw()
    if not isinstance(raw, dict) or raw.get("source") not in SOURCES:
        return None
    name, email = raw.get("name"), raw.get("email")
    if not (isinstance(name, str) and name.strip() and isinstance(email, str)
            and EMAIL_RE.match(email.strip())):
        return None
    out = {"source": raw["source"], "name": name.strip(), "email": email.strip()}
    if raw.get("login"):
        out["login"] = str(raw["login"])
    return out


def _clean_name(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityError("a name is needed: the one your commits should show")
    name = " ".join(value.split())
    if len(name) > NAME_MAX or any(c in name for c in "<>\n"):
        raise IdentityError(f"a name is at most {NAME_MAX} characters, without < or >")
    return name


def _clean_email(value) -> str:
    if not isinstance(value, str) or not EMAIL_RE.match(value.strip()) \
            or len(value.strip()) > EMAIL_MAX:
        raise IdentityError("an email address is needed, like name@example.org; "
                            "or choose your GitHub account to use its private "
                            "no-reply address")
    email = value.strip()
    if is_provider_email(email) or ENGINEER_EMAIL_RE.match(email):
        raise IdentityError(f"{email} is not a person's address")
    return email


def noreply(user_id: int, login: str) -> str:
    return f"{int(user_id)}+{login}@users.noreply.github.com"


def from_github(user: dict) -> dict:
    """The stored value for ``github``, from GitHub's ``GET /user`` answer."""
    login, uid = user.get("login"), user.get("id")
    if not login or not isinstance(uid, int):
        raise IdentityError("GitHub did not say which account is connected", 502)
    name = (user.get("name") or "").strip() or login
    return {"source": "github", "login": login, "name": _clean_name(name),
            "email": noreply(uid, login)}


def validate(patch, github_user=None) -> dict | None:
    """The value a PATCH ``{"identity": …}`` stores, or None to clear it
    (back to the default). ``identity`` is ``"github"`` (``github_user()``
    answers GitHub's ``/user``), ``{"name", "email"}``, ``"neutral"``, or
    null / ``"default"``. Anything else is refused whole with a sentence."""
    if not isinstance(patch, dict) or set(patch) != {"identity"}:
        raise IdentityError('send {"identity": "github"}, {"identity": '
                            '{"name": …, "email": …}}, {"identity": "neutral"} '
                            'or {"identity": null}')
    value = patch["identity"]
    if value is None or value == "default":
        return None
    if value == NEUTRAL:
        return {"source": NEUTRAL}
    if value == "github":
        if github_user is None:
            raise IdentityError("connect a GitHub account first (Settings › "
                                "GitHub), or type a name and an address", 409)
        return from_github(github_user())
    if isinstance(value, dict) and set(value) <= {"name", "email"}:
        return {"source": "typed", "name": _clean_name(value.get("name")),
                "email": _clean_email(value.get("email"))}
    raise IdentityError('identity is "github", {"name": …, "email": …}, '
                        '"neutral" or null (the default)')


def masked(email: str) -> str:
    """An address as a receipt or a log may show it: a GitHub no-reply in
    full (it names only the public account), any other with its local part
    hidden."""
    if email.lower().endswith("@users.noreply.github.com"):
        return email
    local, _, domain = email.partition("@")
    return f"{local[:1]}…@{domain}" if domain else "…"


# -- the default --------------------------------------------------------------

def _global_git() -> tuple[str, str]:
    import subprocess  # noqa: PLC0415
    out = []
    for key in ("user.name", "user.email"):
        try:
            r = subprocess.run(["git", "config", "--global", "--get", key],
                               capture_output=True, text=True, timeout=10)
            out.append(r.stdout.strip() if r.returncode == 0 else "")
        except (OSError, subprocess.SubprocessError):
            out.append("")
    return out[0], out[1]


# Where the default is read from. A test sets these (callables); in a test
# run left unset, the default reads neither the box's GitHub store nor its
# global git config (lib/loam_offline.py), so no fixture meets a real one.
PROFILE_SOURCE = None
GLOBAL_GIT_SOURCE = None


def _offline() -> bool:
    try:
        import loam_offline  # noqa: PLC0415
        return loam_offline.offline()
    except ImportError:
        return False


def default(profile=None, global_git=None) -> dict:
    """What an unset setting means here, now: ``{"status": "github",
    "identity": {...}}`` from the one connected account; ``{"status":
    "git-config", ...}`` from a global git identity whose address is a GitHub
    no-reply, when nothing is connected; else ``{"status": "missing" |
    "ambiguous" | "unreachable", "identity": None}`` with what to do in
    ``prompt``. ``profile`` and ``global_git`` are for tests."""
    if profile is None and PROFILE_SOURCE is not None:
        profile = PROFILE_SOURCE()
    elif profile is None and _offline():
        profile = {"status": "missing"}
    elif profile is None:
        try:
            import loam_github_app  # noqa: PLC0415
            profile = loam_github_app.owner_profile()
        except Exception as exc:  # noqa: BLE001 -- a default never stops a job
            profile = {"status": "unreachable", "error": str(exc)}
    status = profile.get("status")
    if status == "connected":
        name = (profile.get("name") or "").strip() or profile["login"]
        try:
            name = _clean_name(name)
        except IdentityError:
            name = profile["login"]
        return {"status": "github", "identity": {
            "source": "github", "login": profile["login"], "name": name,
            "email": noreply(profile["id"], profile["login"])}, "prompt": None}
    if status == "missing":
        if global_git is None:
            global_git = (GLOBAL_GIT_SOURCE() if GLOBAL_GIT_SOURCE is not None
                          else ("", "") if _offline() else _global_git())
        gname, gmail = global_git
        if gname and NOREPLY_RE.match(gmail or "") and not is_provider_email(gmail) \
                and not engineer_default(gname, gmail):
            try:
                return {"status": "git-config", "identity": {
                    "source": "git-config", "name": _clean_name(gname),
                    "email": gmail.strip()}, "prompt": None}
            except IdentityError:
                pass
    why = {"ambiguous": "more than one GitHub account is connected ("
                        + ", ".join(profile.get("logins") or []) + "), and "
                        "Loam does not choose between them",
           "unreachable": "GitHub could not be asked which account is "
                          "connected; it is tried again at the next job"
           }.get(status, "no GitHub account is connected")
    return {"status": status if status in ("ambiguous", "unreachable") else "missing",
            "identity": None, "prompt": prompt(why, status)}


def effective(raw=None, profile=None, global_git=None) -> tuple[dict | None, str, str | None]:
    """The identity an unset-or-chosen setting gives a job, how, and the
    prompt when there is one: ``(identity, "chosen" | "default" | "neutral"
    | <default status>, prompt)``."""
    raw = _raw() if raw is None else raw
    if opted_out(raw):
        return None, NEUTRAL, None
    chosen = stored(raw)
    if chosen:
        return chosen, "chosen", None
    d = default(profile, global_git)
    return d["identity"], ("default" if d["identity"] else d["status"]), d["prompt"]


def public(raw=None, profile=None, global_git=None) -> dict:
    """Return the configured and effective identity, status and any prompt to choose
    attribution. Local settings callers may see the address. Never log it.
    """
    raw = _raw() if raw is None else raw
    choice = NEUTRAL if opted_out(raw) else stored(raw)
    if choice:
        eff = None if choice == NEUTRAL else choice
        return {"identity": choice, "effective": eff,
                "status": NEUTRAL if choice == NEUTRAL else "set", "prompt": None}
    d = default(profile, global_git)
    return {"identity": None, "effective": d["identity"],
            "status": "default" if d["identity"] else d["status"],
            "default": d["status"], "prompt": d["prompt"]}


def prompt(why: str = "no name is set", status: str = "missing") -> str:
    fix = {"ambiguous": "Choose one: under Settings › GitHub › \"Your name on "
                        "agents' work\", type the name and address to use",
           "unreachable": "Check the connection in Settings › GitHub, or type a "
                          "name under \"Your name on agents' work\""}.get(
        status, "Connect your GitHub account in Settings › GitHub to use its "
                "name and private no-reply address, or type a name under "
                "\"Your name on agents' work\"")
    return (f"Your agents' commits carry the engineer's name (\"<project> "
            f"engineer\") because {why}. {fix}; a persona can do the same with "
            "`settings set git.identity '{\"name\": \"…\", \"email\": \"…\"}'`. "
            "To keep the engineer's name and stop this note: `settings set "
            "git.identity neutral`.")


# -- which identity a job commits as ------------------------------------------

def engineer_default(name: str, email: str) -> bool:
    """Whether ``name <email>`` is the identity bin/provision writes when the
    checkout has none (`<name> engineer`, `engineer+<name>@noreply.local`)."""
    return bool(ENGINEER_EMAIL_RE.match(email or "")) \
        and (name or "").lower().endswith(" engineer")


def resolve(project_identity: dict | None, repo_identity: tuple[str, str]) -> dict:
    """``{"name", "email", "source"}`` for a job (see the module doc for the
    order). ``source`` is one of
    ``project``, ``repo``, ``owner``, ``engineer``, or ``none`` when even the
    checkout has no identity (the commit will fail, as before). ``why`` says,
    for ``engineer``, whether the owner opted out (``neutral``) or which
    default was missing; ``prompt`` is the sentence for the receipt."""
    if project_identity:
        return {"name": project_identity["name"],
                "email": project_identity["email"], "source": "project"}
    owner, how, why = effective()
    if owner:
        return {"name": owner["name"], "email": owner["email"], "source": "owner",
                "via": owner["source"], "default": how == "default",
                **({"login": owner["login"]} if owner.get("login") else {})}
    name, email = repo_identity
    if name and email:
        if not engineer_default(name, email):
            return {"name": name, "email": email, "source": "repo"}
        return {"name": name, "email": email, "source": "engineer", "why": how,
                "prompt": why}
    return {"name": "", "email": "", "source": "none", "why": how, "prompt": why}


def summary(ident: dict) -> str:
    """One public-safe line for a receipt: whose name, from where."""
    src = ident.get("source")
    who = f"{ident.get('name')} <{masked(ident.get('email') or '')}>"
    if src == "owner":
        how = {"github": "the owner's GitHub account",
               "git-config": "the owner's global git identity"}.get(
            ident.get("via"), "the owner's setting")
        return (f"commits as {who}, from {how}"
                + (" (the default)" if ident.get("default") else ""))
    if src == "project":
        return f"commits as {who}, the project's own person"
    if src == "repo":
        return f"commits as {who}, the repository's own identity (kept)"
    if src == "engineer":
        if ident.get("why") == NEUTRAL:
            return f"commits as {who}, the owner's choice (neutral)"
        return f"commits as {who}. {ident.get('prompt') or prompt()}"
    return ("the checkout has no identity at all; commits will fail. "
            + (ident.get("prompt") or prompt()))


# -- provider attribution -------------------------------------------------------

def is_provider_email(email: str) -> bool:
    e = (email or "").strip().lower()
    return e in PROVIDER_EMAILS or bool(PROVIDER_BOT_RE.match(e))


def provider_line(line: str) -> bool:
    """Whether one message line is a provider's own attribution."""
    m = COAUTHOR_RE.match(line)
    if m:
        addr = ADDRESS_RE.search(m.group("who"))
        return bool(addr and is_provider_email(addr.group(1)))
    return bool(SESSION_RE.match(line) or GENERATED_RE.match(line))


def strip_provider(message: str) -> str:
    """``message`` without the lines a provider added about itself, and the
    blank lines they leave at the end. Every other line, trailer or not, is
    kept byte for byte; a message with none comes back unchanged."""
    lines = message.split("\n")
    kept = [ln for ln in lines if not provider_line(ln)]
    if len(kept) == len(lines):
        return message
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + "\n"


def agent_made(author: tuple[str, str], committer: tuple[str, str],
               agents: set[tuple[str, str]]) -> bool:
    """Whether a commit is one an agent made: both its author and committer
    are identities agents commit as here (``agents``: the resolved one, the
    checkout's) or the provision default. A commit anyone else made or
    committed is not."""
    def ours(who):
        key = (who[0], who[1].lower())
        return key in agents or engineer_default(*who)
    return ours(author) and ours(committer)


# -- the hook in a job's clone ----------------------------------------------------

# `.git/hooks/commit-msg` in every job's clone (lib/loam_worktree.configure_clone):
# a provider that writes its own trailer whatever it is told -- Codex 0.170
# takes "Co-authored-by: Codex" from a workspace policy it fetches, with no
# local setting -- has it taken out as the commit is made, before anything
# could push it. The landing's ``settle_commits`` is the second line, for a
# commit made with `--no-verify`. Standard library only: it runs inside the
# job's wall.
HOOK_NAME = "commit-msg"
HOOK_MARK = "# loam: provider attribution (lib/loam_git_identity.py)"


def hook_script(lib: Path) -> str:
    return (f"#!/bin/sh\n{HOOK_MARK}\n"
            f"exec python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); "
            f"import loam_git_identity as g; g.commit_msg(sys.argv[2])' "
            f"\"{lib}\" \"$1\"\n")


def commit_msg(path: str) -> int:
    """The hook: strip a provider's lines from the message file in place."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return 0
    clean = strip_provider(text)
    if clean != text:
        target.write_text(clean, encoding="utf-8", errors="surrogateescape")
    return 0


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--commit-msg":
    sys.exit(commit_msg(sys.argv[2]))
