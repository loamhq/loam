#!/usr/bin/env python3
"""Signed releases (lib/loam_signing.py, docs/signing.md): every side of it,
with throwaway SSH keys made in a temp directory for this run alone.

Real git repositories: a bare `origin`, a release maker (`hq`) cutting
tags, installs cloned from it, and a scratch config (LOAM_CONFIG) whose root
is the install's checkout. The keys: A is the maintainer's, B the one A
rotates to, M an attacker's.

Checks:
  - the verifier: a tag signed by a trusted key passes and names the commit;
    a lightweight tag, an unsigned annotated tag, a tag signed by another
    key, a tag object altered after signing, and a signed tag pushed under
    another release's name each fail with their reason; no key means "not
    checked", said in words;
  - the updater on a scratch install whose release carries A: each bad
    release is refused by `check` (shown as blocked, the agent told it was
    refused) and by `apply` (nothing moved: HEAD, System/Loam, no rollback
    point left); a release that swaps in the attacker's key and signs with
    it is refused, because the keys are the running release's; the
    correctly signed one applies, to the verified commit; rotation A -> B
    works across two releases; a development install merges a signed one;
  - the release step (`cut_release`): a tree carrying keys and a checkout
    with no signing key waits for `sign-release` and pushes nothing; with
    the right key it pushes a tag that verifies; with the wrong key it is
    refused and leaves no tag; a tree with no keys tags as before;
  - `bin/sign-release` on "the maintainer's device" (a clone) signs and
    pushes a tag that verifies, and refuses a version that does not match;
  - `install.sh`, run as root in a user namespace against the scratch
    origin with A written into it: a signed release runs its installer; a
    lightweight and an altered one stop before anything from the clone
    runs, and the clone is removed; with no key written in, it says the
    release is not checked;
  - install.sh's copy of the keys equals keys/release-signers.

Run: python3 tests/test_signing.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-signing-"))
INSTALL = TMP / "install"
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f'''
[owner]
id = "sam"
name = "Sam"
[paths]
home = "{TMP}/home"
root = "{INSTALL}"
vault = "{TMP}/vault"
workspaces = "{TMP}/agents"
state = "{TMP}/state"
''')
os.environ["LOAM_CONFIG"] = str(CONFIG)
for var in ("LOAM_VAULT", "LOAM_AI_LABS", "LOAM_THREADS_STATE", "LOAM_AGENTS_STATE",
            "LOAM_UPDATE_STATE", "LOAM_UPDATE_RESTART", "LOAM_SESSION_ID",
            "SSH_AUTH_SOCK"):
    os.environ.pop(var, None)
os.environ.setdefault("LOAM_LOCK_DIR", str(TMP / "locks"))
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
os.environ.update(GIT_ENV)
(TMP / "agents" / "iris").mkdir(parents=True)
(TMP / "agents" / "iris" / "agent.json").write_text(json.dumps({"name": "Iris"}))

sys.path.insert(0, str(ROOT / "lib"))
import loam_signing as sg  # noqa: E402
import loam_skills as sk  # noqa: E402
import loam_update as up  # noqa: E402

FAILURES = 0


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def run(*argv, cwd=None, inp=None, env=None, check_=True) -> subprocess.CompletedProcess:
    done = subprocess.run([str(a) for a in argv], cwd=cwd, input=inp, capture_output=True,
                          text=isinstance(inp, str) or inp is None,
                          env={**os.environ, **(env or {})}, check=False)
    if check_ and done.returncode != 0:
        raise RuntimeError(f"{argv}: {done.stderr}")
    return done


def git(repo: Path, *args: str, **kw) -> str:
    return run("git", "-C", repo, *args, **kw).stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# -- the throwaway keys -------------------------------------------------------------

KEYS = TMP / "keys"
KEYS.mkdir()


def keypair(name: str) -> tuple[Path, str]:
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"test-{name}",
        "-f", KEYS / name)
    pub = (KEYS / f"{name}.pub").read_text().split()
    return KEYS / name, f'loam-release namespaces="git" {pub[0]} {pub[1]} test-{name}'


KEY_A, LINE_A = keypair("a")
KEY_B, LINE_B = keypair("b")
KEY_M, LINE_M = keypair("m")
HEADER = "# test signers\n"


def signers(*lines: str) -> str:
    return HEADER + "".join(line + "\n" for line in lines)


# -- the releases -------------------------------------------------------------------

ORIGIN, HQ = TMP / "origin.git", TMP / "hq"
run("git", "init", "-q", "--bare", "-b", "main", ORIGIN)
run("git", "init", "-q", "-b", "main", HQ)
git(HQ, "remote", "add", "origin", str(ORIGIN))
BASE = {
    ".gitignore": "*.pyc\n",
    "docs/library/README.md": "# Loam's library\n",
    "docs/library/skills/loam-a/SKILL.md": "---\nname: loam-a\n---\nA.\n",
    "docs/logs.md": "Logs.\n",
    # What install.sh hands over to: here a stub that leaves a mark, so the
    # test sees whether anything from the clone ran.
    "bin/loam-install": "import os, pathlib\n"
                        "pathlib.Path(os.environ['MARK']).write_text('ran')\n",
    sg.SIGNERS_FILE: signers(LINE_A),
}
for name, text in BASE.items():
    write(HQ / name, text)


def commit(version: str, changes: dict[str, str | None] | None = None) -> str:
    for name, text in (changes or {}).items():
        if text is None:
            (HQ / name).unlink()
        else:
            write(HQ / name, text)
    write(HQ / "manifest.json", json.dumps({"version": version}) + "\n")
    git(HQ, "add", "-A")
    git(HQ, "commit", "-qm", f"release {version}")
    return git(HQ, "rev-parse", "HEAD")


def sign(version: str, key: Path, at: str = "HEAD") -> None:
    git(HQ, "-c", "gpg.format=ssh", "-c", f"user.signingKey={key}", "tag", "-s",
        "-m", f"Loam {version}", version, at)


def publish(version: str) -> None:
    git(HQ, "push", "-q", "-f", "origin", "main", f"+refs/tags/{version}:refs/tags/{version}")


def unpublish(version: str) -> None:
    git(HQ, "push", "-q", "origin", f":refs/tags/{version}")
    git(HQ, "tag", "-d", version)


def install(at: str) -> None:
    shutil.rmtree(INSTALL, ignore_errors=True)
    run("git", "clone", "-q", "--branch", at, ORIGIN, INSTALL)


class QuietBox(up.Box):
    """The box beyond git and System/Loam does nothing here."""

    def _run(self, *argv, timeout=None) -> str:
        return "ok"

    def _root(self, *argv, timeout=None) -> str:
        return "ok"

    def library(self) -> str:
        sk.install_library(source=up.runtime() / "docs" / "library")
        return "installed"

    def broker(self) -> str:
        return "ok"

    def gateway(self) -> str:
        return "ok"


def notices() -> list[dict]:
    return up.owed_notices()


def clear_notices() -> None:
    for n in notices():
        Path(n["_path"]).unlink()


def forge(tag: str, signed_obj: str, new_commit: str) -> None:
    """Point origin's ``tag`` at a copy of a signed tag object whose
    `object` line now names ``new_commit``: the signature is the real one,
    the contents are not what was signed."""
    raw = run("git", "-C", HQ, "cat-file", "tag", signed_obj).stdout
    head, _, rest = raw.partition("\n")
    forged = f"object {new_commit}\n" + rest
    obj = run("git", "-C", HQ, "hash-object", "-t", "tag", "-w", "--stdin",
              inp=forged).stdout.strip()
    git(HQ, "update-ref", f"refs/tags/{tag}", obj)
    git(HQ, "push", "-q", "-f", "origin", "main", f"+refs/tags/{tag}:refs/tags/{tag}")


try:
    # == the verifier ==================================================================
    c1 = commit("1.0.0")
    sign("1.0.0", KEY_A)
    publish("1.0.0")
    good = sg.verify_tag(HQ, "1.0.0", signers(LINE_A))
    check("verifier: a tag signed by a trusted key passes, naming its commit",
          (good["ok"], good["checked"], good["commit"], good["principal"]),
          (True, True, c1, "loam-release"))
    (TMP / "allowed").write_text(LINE_A + "\n")
    check("verifier: ...and git's own verify-tag agrees",
          run("git", "-C", HQ, "-c", f"gpg.ssh.allowedSignersFile={TMP / 'allowed'}",
              "verify-tag", "1.0.0", check_=False).returncode, 0)
    git(HQ, "tag", "light", c1)
    git(HQ, "tag", "-a", "-m", "x", "plain", c1)
    check("verifier: a lightweight tag is unsigned",
          sg.verify_tag(HQ, "light", signers(LINE_A))["reason"], "unsigned")
    check("verifier: an annotated tag with no signature is unsigned",
          sg.verify_tag(HQ, "plain", signers(LINE_A))["reason"], "unsigned")
    check("verifier: signed by a key not in the file is untrusted-key",
          sg.verify_tag(HQ, "1.0.0", signers(LINE_M))["reason"], "untrusted-key")
    obj1 = git(HQ, "rev-parse", "refs/tags/1.0.0")
    git(HQ, "update-ref", "refs/tags/9.9.9", obj1)
    check("verifier: a signed tag pushed under another release's name is wrong-name",
          sg.verify_tag(HQ, "9.9.9", signers(LINE_A))["reason"], "wrong-name")
    git(HQ, "tag", "-d", "9.9.9")
    nothing = sg.verify_tag(HQ, "light", "# no keys\n")
    check("verifier: no key in the file means not checked, and says so",
          (nothing["ok"], nothing["checked"], "not checked" in nothing["message"]),
          (True, False, True))
    check("key_lines skips comments and blanks",
          sg.key_lines(f"# x\n\n  {LINE_A}\n#y\n"), [LINE_A])

    # == the updater =====================================================================
    install("1.0.0")
    sk.install_library(source=INSTALL / "docs" / "library")
    box = QuietBox()
    st = up.status()
    check("status: the install says it checks against one key",
          (st["current"]["signing"]["checked"], st["current"]["signing"]["keys"]),
          (True, 1))

    def refused_cleanly(label: str, reason: str) -> None:
        clear_notices()
        head0 = git(INSTALL, "rev-parse", "HEAD")
        chk = up.check(tell=True)["check"]
        check(f"{label}: check shows it blocked by its signature",
              ((chk.get("blocked") or {}).get("code"),
               (chk.get("blocked") or {}).get("reason")), ("signature", reason))
        told = [n for n in notices() if n["kind"] == "refused"]
        check(f"{label}: the agent is told it was refused, never that it is available",
              (len(told), "could not verify" in (told[0]["line"] if told else ""),
               any(n["kind"] == "available" for n in notices())), (1, True, False))
        rec = up.apply(box=box)
        check(f"{label}: apply is refused with the reason",
              (rec["outcome"], rec["refusal"]["code"], rec["refusal"].get("reason")),
              ("refused", "signature", reason))
        check(f"{label}: nothing moved (HEAD, no rollback point, System/Loam)",
              (git(INSTALL, "rev-parse", "HEAD") == head0,
               (up.STATE / "pending.json").exists(),
               [s["name"] for s in rec["steps"]]), (True, False, ["preflight"]))

    # A lightweight 1.1.0, as a tag push by anyone with push access makes.
    commit("1.1.0", {"docs/logs.md": "Evil.\n"})
    git(HQ, "tag", "1.1.0")
    publish("1.1.0")
    refused_cleanly("unsigned release", "unsigned")
    unpublish("1.1.0")

    # The attacker swaps their own key in and signs with it.
    git(HQ, "reset", "-q", "--hard", c1)
    commit("1.1.0", {"docs/logs.md": "Evil.\n", sg.SIGNERS_FILE: signers(LINE_M)})
    sign("1.1.0", KEY_M)
    publish("1.1.0")
    refused_cleanly("release signed by a key it brings itself", "untrusted-key")
    unpublish("1.1.0")

    # The genuine release, then the same signature over other contents.
    git(HQ, "reset", "-q", "--hard", c1)
    c_good = commit("1.1.0", {"docs/logs.md": "Better logs.\n"})
    sign("1.1.0", KEY_A)
    genuine = git(HQ, "rev-parse", "refs/tags/1.1.0")
    git(HQ, "reset", "-q", "--hard", c1)
    c_evil = commit("1.1.0", {"docs/logs.md": "Evil.\n"})
    forge("1.1.0", genuine, c_evil)
    refused_cleanly("signed tag altered to name other contents", "bad-signature")

    # The correctly signed one is accepted, at the commit that verified.
    git(HQ, "reset", "-q", "--hard", c_good)
    git(HQ, "update-ref", "refs/tags/1.1.0", genuine)
    publish("1.1.0")
    clear_notices()
    chk = up.check(tell=True)["check"]
    check("signed release: check offers it, not blocked",
          (chk["available"], chk["blocked"], chk["signature"]["ok"]), (True, None, True))
    rec = up.apply(box=box)
    check("signed release: applied, at the verified commit, the signer recorded",
          (rec["outcome"], git(INSTALL, "rev-parse", "HEAD"),
           (rec.get("signature") or {}).get("principal"),
           (INSTALL / "docs/logs.md").read_text()),
          ("applied", c_good, "loam-release", "Better logs.\n"))

    # Rotation: 1.2.0 adds B and is signed by A; 1.3.0 is signed by B alone
    # and drops A.
    commit("1.2.0", {sg.SIGNERS_FILE: signers(LINE_A, LINE_B)})
    sign("1.2.0", KEY_A)
    publish("1.2.0")
    check("rotation: 1.2.0 (adds B, signed by A) applies",
          up.apply(box=box)["outcome"], "applied")
    c13 = commit("1.3.0", {sg.SIGNERS_FILE: signers(LINE_B)})
    sign("1.3.0", KEY_B)
    publish("1.3.0")
    check("rotation: 1.3.0 (signed by B, drops A) applies",
          (up.apply(box=box)["outcome"], git(INSTALL, "rev-parse", "HEAD")),
          ("applied", c13))
    commit("1.4.0")
    sign("1.4.0", KEY_A)
    publish("1.4.0")
    check("rotation: after A is dropped, a release signed by A is refused",
          (up.apply(box=box).get("refusal") or {}).get("reason"), "untrusted-key")
    unpublish("1.4.0")
    git(HQ, "reset", "-q", "--hard", c13)

    # A development install (a local commit) merges a signed release.
    git(INSTALL, "checkout", "-q", "-b", "main")
    write(INSTALL / "local.txt", "mine\n")
    git(INSTALL, "add", "local.txt")
    git(INSTALL, "commit", "-qm", "local change")
    c14 = commit("1.4.0", {"docs/logs.md": "1.4 logs.\n"})
    sign("1.4.0", KEY_B)
    publish("1.4.0")
    rec = up.apply(box=box)
    check("development install: a signed release is merged, the local commit kept",
          (rec["outcome"], (INSTALL / "local.txt").exists(),
           git(INSTALL, "merge-base", "--is-ancestor", c14, "HEAD", check_=False) == ""),
          ("applied", True, True))

    # An install whose release carries no key: not checked, and it says so.
    shutil.rmtree(INSTALL)
    nokey = TMP / "nokey"
    run("git", "init", "-q", "-b", "main", nokey)
    write(nokey / "manifest.json", '{"version": "0.9.0"}\n')
    git(nokey, "add", "-A")
    git(nokey, "commit", "-qm", "0.9.0")
    run("git", "clone", "-q", nokey, INSTALL)
    st = up.status()["current"]["signing"]
    check("no key in the running release: status says releases are not checked",
          (st["checked"], st["message"].startswith("Releases are not checked")),
          (False, True))

    # == the release step (cut_release) ================================================
    import loam_worktree as wt  # noqa: E402
    REL_ORIGIN, REL = TMP / "rel-origin.git", TMP / "rel"
    run("git", "init", "-q", "--bare", "-b", "main", REL_ORIGIN)
    run("git", "clone", "-q", ORIGIN, REL)
    git(REL, "remote", "set-url", "origin", str(REL_ORIGIN))
    git(REL, "checkout", "-q", "-B", "main", c13)
    git(REL, "push", "-q", "origin", "main", "refs/tags/1.3.0")

    def landed(version: str, changes=None) -> dict:
        for name, text in (changes or {}).items():
            write(REL / name, text)
        write(REL / "manifest.json", json.dumps({"version": version}) + "\n")
        git(REL, "add", "-A")
        git(REL, "commit", "-qm", version)
        git(REL, "push", "-q", "origin", "main")
        return {"state": "landed", "sha": git(REL, "rev-parse", "HEAD"), "pushed": True}

    def origin_has(tag: str) -> bool:
        return bool(git(REL, "ls-remote", "origin", f"refs/tags/{tag}"))

    res = landed("1.5.0")
    out = wt.cut_release(REL, res, "1.5.0")
    check("release step, no signing key here: waits for sign-release, pushes nothing",
          (out["state"], "bin/sign-release 1.5.0" in out["detail"], origin_has("1.5.0"),
           "waiting to be signed" in wt.release_section(out)),
          ("to-sign", True, False, True))
    git(REL, "config", "user.signingKey", str(KEY_A))
    out = wt.cut_release(REL, res, "1.5.0")
    check("release step, a key the previous release does not trust: refused, no tag",
          (out["state"], origin_has("1.5.0"), git(REL, "tag", "-l", "1.5.0")),
          ("refused", False, ""))
    git(REL, "config", "user.signingKey", str(KEY_B))
    out = wt.cut_release(REL, res, "1.5.0")
    git(REL, "fetch", "-q", "origin", "+refs/tags/1.5.0:refs/tags/1.5.0")
    check("release step, the right key: signed in the same step, pushed, verifies",
          (out["state"], "signed by loam-release" in out["detail"],
           sg.verify_tag(REL, "1.5.0", signers(LINE_B))["ok"]), ("pushed", True, True))
    plain, plain_origin = TMP / "plain", TMP / "plain-origin.git"
    shutil.copytree(REL, plain)
    run("git", "init", "-q", "--bare", "-b", "main", plain_origin)
    git(plain, "remote", "set-url", "origin", str(plain_origin))
    git(plain, "rm", "-q", sg.SIGNERS_FILE)
    git(plain, "commit", "-qm", "no keys")
    git(plain, "tag", "-d", *git(plain, "tag", "-l").split())
    git(plain, "config", "--unset", "user.signingKey")
    write(plain / "manifest.json", '{"version": "2.0.0"}\n')
    git(plain, "add", "-A")
    git(plain, "commit", "-qm", "2.0.0")
    git(plain, "push", "-q", "origin", "main")
    out = wt.cut_release(plain, {"state": "landed", "sha": git(plain, "rev-parse", "HEAD"),
                                 "pushed": True}, "2.0.0")
    check("release step, a project with no keys: tagged as it always was",
          (out["state"], git(plain, "cat-file", "-t", "refs/tags/2.0.0")),
          ("pushed", "commit"))

    # == bin/sign-release, on the maintainer's device ====================================
    device = TMP / "device"
    run("git", "clone", "-q", REL_ORIGIN, device)
    git(REL, "fetch", "-q", "origin")
    git(REL, "reset", "-q", "--hard", "origin/main")
    git(REL, "config", "--unset", "user.signingKey")
    res = landed("2.1.0")
    out = wt.cut_release(REL, res, "2.1.0")
    check("the landing hands 2.1.0 to sign-release", out["state"], "to-sign")
    bad = run(sys.executable, ROOT / "bin/sign-release", "2.2.0", "--key", KEY_B, "--yes",
              cwd=device, check_=False)
    check("sign-release: a version main's manifest does not say is refused",
          (bad.returncode != 0, "says '2.1.0'" in bad.stderr), (True, True))
    # The private key file stands in for the maintainer's agent or hardware key.
    done = run(sys.executable, ROOT / "bin/sign-release", "2.1.0", "--key", KEY_B,
               "--yes", cwd=device, check_=False)
    git(REL, "fetch", "-q", "origin", "+refs/tags/2.1.0:refs/tags/2.1.0")
    check("sign-release: signs, checks and pushes a tag that verifies",
          (done.returncode, "signed and pushed" in done.stdout,
           sg.verify_tag(REL, "2.1.0", signers(LINE_B))["commit"]), (0, True, res["sha"]))

    # == install.sh =====================================================================
    def installer(lines: list[str]) -> Path:
        text = (ROOT / "install.sh").read_text()
        start = text.index("<<'RELEASE-SIGNERS'\n") + len("<<'RELEASE-SIGNERS'\n")
        end = text.index("RELEASE-SIGNERS\n)", start)
        path = TMP / f"install-{len(lines)}.sh"
        path.write_text(text[:start] + "".join(f"{l}\n" for l in lines) + text[end:])
        return path

    def fresh_install(script: Path, version: str, name: str) -> tuple[subprocess.CompletedProcess, bool, bool]:
        state = TMP / f"fresh-{name}" / "install"
        mark = TMP / f"mark-{name}"
        done = run("unshare", "-r", "bash", script, "--source", ORIGIN, "--version", version,
                   check_=False, env={"LOAM_INSTALL_STATE": str(state),
                                      "LOAM_INSTALL_LOG": str(TMP / f"log-{name}"),
                                      "MARK": str(mark)})
        return done, mark.exists(), (state / "release").exists()

    with_a = installer([LINE_A])
    done, ran, kept = fresh_install(with_a, "1.1.0", "good")
    check("install.sh: a signed release verifies and its installer runs",
          (done.returncode, ran, "signed by loam-release; checked" in done.stdout),
          (0, True, True))
    git(HQ, "tag", "1.0.9", c1)
    publish("1.0.9")
    done, ran, kept = fresh_install(with_a, "1.0.9", "light")
    check("install.sh: a lightweight tag stops before anything from it runs",
          (done.returncode, ran, kept, "could not be verified.\n  it is not signed."
           in done.stdout), (3, False, False, True))
    forge("1.0.8", genuine, c_evil)
    done, ran, kept = fresh_install(with_a, "1.0.8", "forged")
    check("install.sh: an altered signed tag stops, clone removed",
          (done.returncode, ran, kept), (3, False, False))
    done, ran, kept = fresh_install(installer([LINE_M]), "1.1.0", "other")
    check("install.sh: a release signed by a key it does not carry stops",
          (done.returncode, ran, "not by a Loam maintainer key" in done.stdout),
          (3, False, True))
    done, ran, kept = fresh_install(installer([]), "1.0.9", "nokeys")
    check("install.sh: with no key written in, it runs and says it did not check",
          (done.returncode, ran, "not checked" in done.stdout), (0, True, True))

    # == the two homes of the keys ======================================================
    text = (ROOT / "install.sh").read_text()
    block = text.split("<<'RELEASE-SIGNERS'\n", 1)[1].split("RELEASE-SIGNERS\n)", 1)[0]
    check("install.sh carries exactly the key lines of keys/release-signers",
          sg.key_lines(block),
          sg.key_lines((ROOT / sg.SIGNERS_FILE).read_text()))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'OK' if not FAILURES else 'FAILED'}: {FAILURES} failure(s)")
sys.exit(1 if FAILURES else 0)
