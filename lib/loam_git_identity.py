"""Whose name an agent's commits carry, by default (since 2026-11-18).

The owner, 2026-11-18: "Whatever gets developed by their agents should be
under their name and not the provider they are using." So the work an
engineer job commits is authored and committed as the install's owner, and
no provider (Claude Code, Codex, Antigravity) adds itself as a co-author.

**The setting** is one value in the host settings file
(``state/broker/settings.json``, key ``git``), changed by the same route from
the page (Settings, under GitHub) and from a persona (`settings set
git.identity …`, ``bin/settings``)::

    {"source": "github", "login": "ada", "name": "Ada Lovelace",
     "email": "123+ada@users.noreply.github.com"}       # from GitHub
    {"source": "typed", "name": "Ada Lovelace", "email": "ada@example.org"}

``github`` takes the connected GitHub account's profile name and GitHub's own
no-reply address for it (``<id>+<login>@users.noreply.github.com``), which
GitHub attributes to the account and which keeps the person's real address
out of every commit. It is read once from GitHub when it is chosen, with the
connection's existing token; no scope is added. ``typed`` is a name and an
address the owner gave. Nothing else: Loam never guesses an address, and
there is no built-in person.

**Which identity a job commits as** (``resolve``), first that applies:

1. ``project`` -- the project record's ``commit_identity``, a person who
   answers for every commit in a shared repository
   (lib/loam_commit_identity.py, stricter rules of its own);
2. ``repo`` -- the canonical checkout's own ``user.name``/``user.email``,
   when someone set them on purpose: any identity that is not the default
   ``<name> engineer <engineer+<name>@noreply.local>`` that bin/provision
   writes, and always on a checkout with the push gate on
   (``loam.pushGate``), whose gate *is* that identity;
3. ``owner`` -- the setting above;
4. ``engineer`` -- nothing set: the checkout's engineer identity, as before
   this module, never a provider. ``prompt()`` says how to set the owner's,
   and the job's receipt carries it.

**Provider attribution.** Each harness is told not to add itself: Claude
Code by ``CLI_SETTINGS`` (lib/loam_commit_identity), Codex by
``CODEX_CONFIG``; Antigravity 1.5 adds none. As a backstop the landing
re-makes the job's own unpushed commits that an agent made (``agent_made``)
with the identity resolved here and ``strip_provider`` applied to the
message. Only lines that name a provider are taken out: a co-author trailer
whose address is a provider's (``PROVIDER_EMAILS``, a provider bot's GitHub
no-reply), a ``Claude-Session`` trailer and a "Generated with Claude Code /
Codex" line. A person's co-author trailer, a commit someone else made and a
signed commit are left exactly as they are, and nothing already on a remote
is ever rewritten.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

KEY = "git"
SOURCES = ("github", "typed")
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


def stored(raw=None) -> dict | None:
    """The owner's identity as stored, or None when unset or not whole."""
    if raw is None:
        try:
            data = json.loads(settings_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        raw = data.get(KEY) if isinstance(data, dict) else None
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
    """The value a PATCH ``{"identity": …}`` stores, or None to clear it.
    ``identity`` is ``"github"`` (``github_user()`` answers GitHub's
    ``/user``), ``{"name", "email"}``, or null. Anything else is refused
    whole with a sentence."""
    if not isinstance(patch, dict) or set(patch) != {"identity"}:
        raise IdentityError('send {"identity": "github"}, {"identity": '
                            '{"name": …, "email": …}} or {"identity": null}')
    value = patch["identity"]
    if value is None:
        return None
    if value == "github":
        if github_user is None:
            raise IdentityError("connect a GitHub account first (Settings › "
                                "GitHub), or type a name and an address", 409)
        return from_github(github_user())
    if isinstance(value, dict) and set(value) <= {"name", "email"}:
        return {"source": "typed", "name": _clean_name(value.get("name")),
                "email": _clean_email(value.get("email"))}
    raise IdentityError('identity is "github", {"name": …, "email": …} or null')


def masked(email: str) -> str:
    """An address as a receipt or a log may show it: a GitHub no-reply in
    full (it names only the public account), any other with its local part
    hidden."""
    if email.lower().endswith("@users.noreply.github.com"):
        return email
    local, _, domain = email.partition("@")
    return f"{local[:1]}…@{domain}" if domain else "…"


def public(value: dict | None) -> dict:
    """What ``GET /v1/settings/git`` answers. The owner's own page and
    personas on this box may see the address; it never goes to a log."""
    if not value:
        return {"identity": None, "status": "missing", "prompt": prompt()}
    return {"identity": value, "status": "set", "prompt": None}


def prompt() -> str:
    return ("No name is set for your agents' commits, so they carry the "
            "engineer's (\"<project> engineer\"). Set yours in Settings › GitHub "
            "› \"Your name on agents' work\", or ask a persona to run "
            "`settings set git.identity github` (your GitHub account's name and "
            "private no-reply address) or `settings set git.identity "
            "'{\"name\": \"…\", \"email\": \"…\"}'`.")


# -- which identity a job commits as ------------------------------------------

def engineer_default(name: str, email: str) -> bool:
    """Whether ``name <email>`` is the identity bin/provision writes when the
    checkout has none (`<name> engineer`, `engineer+<name>@noreply.local`)."""
    return bool(ENGINEER_EMAIL_RE.match(email or "")) \
        and (name or "").lower().endswith(" engineer")


def resolve(project_identity: dict | None, repo_identity: tuple[str, str],
            gated: bool, owner: dict | None = None) -> dict:
    """``{"name", "email", "source"}`` for a job (see the module doc for the
    order). ``owner`` defaults to the stored setting. ``source`` is one of
    ``project``, ``repo``, ``owner``, ``engineer``, or ``none`` when even the
    checkout has no identity (the commit will fail, as before)."""
    if project_identity:
        return {"name": project_identity["name"],
                "email": project_identity["email"], "source": "project"}
    name, email = repo_identity
    if name and email and (gated or not engineer_default(name, email)):
        return {"name": name, "email": email, "source": "repo"}
    owner = stored() if owner is None else owner
    if owner:
        return {"name": owner["name"], "email": owner["email"], "source": "owner",
                "via": owner["source"], **({"login": owner["login"]}
                                           if owner.get("login") else {})}
    if name and email:
        return {"name": name, "email": email, "source": "engineer"}
    return {"name": "", "email": "", "source": "none"}


def summary(ident: dict) -> str:
    """One public-safe line for a receipt: whose name, from where."""
    src = ident.get("source")
    who = f"{ident.get('name')} <{masked(ident.get('email') or '')}>"
    if src == "owner":
        how = ("the owner's GitHub account" if ident.get("via") == "github"
               else "the owner's setting")
        return f"commits as {who}, from {how}"
    if src == "project":
        return f"commits as {who}, the project's own person"
    if src == "repo":
        return f"commits as {who}, the repository's own identity (kept)"
    if src == "engineer":
        return f"commits as {who}, because no owner identity is set. {prompt()}"
    return "the checkout has no identity at all; commits will fail. " + prompt()


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
