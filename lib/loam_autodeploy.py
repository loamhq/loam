"""A project job that deploys itself when it lands (since 2026-11-12).

Until this, every loam-ui change the owner asked for took two engineer jobs:
the page job landed on `main`, then a separate Loam job ran
`sudo -n bin/deploy loam-ui up <commit>`, because a project engineer's unit
carries NoNewPrivileges and its sandbox has no Docker socket. That second
job cost about six minutes and a round through a persona per fix.

Now a brief may carry `deploy: true` (the brief tool's `deploy` parameter,
written into the brief's header as `deploy: <app>`). After the launcher has
merged the branch, passed the tests and pushed, it deploys the commit that
landed with the same command a deploy job ran, in the form that never goes
backwards:

    sudo -n <root>/bin/deploy <app> up <sha> --forward-only

**An explicit field, not the involvement's prose.** "Land and deploy", "Go
and do it ... after it is live", "deploy once it's green" -- a parser of
the owner's quoted words either misses a phrasing or fires on "do not deploy".
`Read-only` is the one dial read from the involvement, and it is matched at
the start of the string only for that reason. A deploy is a live change, so
the persona sets it on purpose and the receipt shows it was set.

**Who runs it.** Not the job's unit: NoNewPrivileges there is what keeps
sudo from the engineer, and it holds for this launcher code too. The deploy
runs in a transient user unit of its own (`systemd-run --user --wait
--pipe`), started by the box user's user manager, which does not inherit the
flag -- the same way every engineer job's unit is started. The session is
over by then: what runs is this file's fixed command line with a sha the
landing produced, never anything the engineer wrote.

**Which app.** The deployment record named after the project
(`deploy/<project>/deploy.toml`) that runs a container. Staging
(`loam-ui-next`) is never deployed this way: a brief that says "staging
first" sets no `deploy`, lands only, and a persona asks for staging as a
Loam deploy job, as before.

**Two landings close together.** Each landing merges onto `main` as it is,
so the later landing's sha carries the earlier one. `--forward-only` makes
whichever deploy runs second a no-op when what runs already contains its
commit, under the app's live lock, so the earlier commit is never put back.

**A failed deploy never fails the landing.** `main` has already moved and
been pushed; the deploy's outcome is reported in the receipt beside it.
bin/deploy leaves the previous container running when the build fails, and
puts the previous image back when the new one is not healthy.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import loam_deploy as dep

DEPLOY_BIN = dep.DEPLOY_BIN
RESULT_PREFIX = "deploy-result: "      # bin/deploy's RESULT_PREFIX
# A build of loam-ui is a few minutes; this is a backstop against a hang,
# not a clock on the work.
TIMEOUT_SECONDS = 30 * 60

TRUE_WORDS = {"1", "true", "yes", "on", "live"}


def wanted(value) -> bool:
    """The tool's `deploy` argument as a yes or a no."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in TRUE_WORDS


def app_for(project) -> tuple[str | None, str | None]:
    """(app name, None), or (None, the sentence refusing the brief)."""
    if project.platform:
        return None, ("deploy is for a project engineer's job. A Loam job "
                      "deploys by running bin/deploy itself.")
    try:
        apps = {a.name: a for a in dep.records()}
    except dep.DeployError as exc:
        return None, f"deploy: the deployment records could not be read ({exc})."
    app = apps.get(project.name)
    if app is None or app.compose is None:
        return None, (f"deploy: {project.name} has no deployment record that "
                      f"runs a container (deploy/{project.name}/deploy.toml), "
                      f"so there is nothing for its landing to deploy. Send "
                      f"the brief without `deploy`.")
    return app.name, None


def command(app: str, sha: str) -> list[str]:
    return ["sudo", "-n", str(DEPLOY_BIN), app, "up", sha, "--forward-only"]


def unit_name(job_id: str) -> str:
    return f"loam-autodeploy-{job_id}"


def argv(app: str, sha: str, job_id: str) -> list[str]:
    return ["systemd-run", "--user", "--wait", "--pipe", "--collect", "--quiet",
            f"--unit={unit_name(job_id)}",
            f"--description=Loam deploy of {app} at {sha[:7]} after job {job_id}",
            "--property=StandardError=journal",
            *command(app, sha)]


def parse_result(output: str) -> dict | None:
    """bin/deploy's last `deploy-result:` line, or None."""
    found = None
    for line in output.splitlines():
        if line.startswith(RESULT_PREFIX):
            try:
                found = json.loads(line[len(RESULT_PREFIX):])
            except ValueError:
                continue
    return found


def should_deploy(landing: dict) -> str | None:
    """The sha to deploy, or None: only a landing that put the job's work
    on `main` and pushed it (or has no remote to push to)."""
    if landing.get("state") not in ("landed", "landed_earlier"):
        return None
    if landing.get("state") == "landed" and landing.get("pushed") not in (True, None):
        return None
    return landing.get("sha") or None


def run(app: str, sha: str, job_id: str, say=None, runner=subprocess.run) -> dict:
    """Run the deploy; never raises. Returns {"app", "sha", "command",
    "exit", "result", "tail", "seconds"}."""
    say = say or (lambda _t: None)
    say(f"deploy: {app} up {sha[:7]} --forward-only")
    t0 = time.monotonic()
    try:
        res = runner(argv(app, sha, job_id), capture_output=True, text=True,
                     timeout=TIMEOUT_SECONDS, check=False)
        out, code = (res.stdout or "") + (res.stderr or ""), res.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        out, code = str(exc), None
    seconds = round(time.monotonic() - t0, 1)
    result = parse_result(out)
    say(f"deploy: exit {code}, {(result or {}).get('action', 'no result line')}")
    lines = [ln for ln in out.splitlines() if not ln.startswith(RESULT_PREFIX)]
    live = []
    if result and result.get("action") == "deployed":
        live = went_live(checkout_of(app), result.get("before"), result.get("after"))
    return {"app": app, "sha": sha, "command": " ".join(command(app, sha)),
            "unit": f"{unit_name(job_id)}.service",
            "exit": code, "result": result, "tail": "\n".join(lines[-15:]),
            "seconds": seconds, "went_live": live}


def went_live(checkout: Path | None, before: str | None, after: str | None,
              limit: int = 30) -> list[str]:
    """The commits a deploy put live: `before..after` in the app's checkout,
    one `<short> <subject>` each, newest first. A landing's sha carries every
    commit landed before it, including another job's that said "do not
    deploy" or "staging first" -- so the receipt names all of them, not only
    this job's (found 2026-11-12: the plugin host, landed for staging only,
    sat under every later loam-ui commit). [] when nothing changed or the
    range cannot be read."""
    if not checkout or not before or not after or before == after:
        return []
    if not all(len(c) == 40 for c in (before, after)):
        return []
    try:
        res = subprocess.run(["git", "-C", str(checkout), "log", "--format=%h %s",
                              f"-{limit + 1}", f"{before}..{after}"],
                             capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return res.stdout.splitlines() if res.returncode == 0 else []


def checkout_of(app: str) -> Path | None:
    try:
        return next((a.checkout for a in dep.records() if a.name == app), None)
    except dep.DeployError:
        return None


def short(commit: str | None) -> str:
    if not commit:
        return "nothing"
    return commit[:7] if len(commit) == 40 else commit


def receipt_text(outcome: dict | None) -> str:
    """The launcher's paragraph on the deploy, for the receipt."""
    if outcome is None:
        return ""
    if outcome.get("skipped"):
        return f"\n\n**Deploy: not run.** {outcome['skipped']}"
    r = outcome.get("result") or {}
    action = r.get("action")
    head = {
        "deployed": f"**Deploy: live at `{short(r.get('after'))}`**",
        "carried": (f"**Deploy: nothing to do** — `{outcome['app']}` already runs "
                    f"`{short(r.get('after'))}`, which carries "
                    f"`{outcome['sha'][:7]}`"),
        "rolled_back": (f"**Deploy failed, rolled back** — `{outcome['sha'][:7]}` "
                        f"was not healthy and `{short(r.get('after'))}` runs again"),
        "unhealthy": (f"**Deploy failed** — `{outcome['sha'][:7]}` was not "
                      f"healthy and nothing could be put back"),
        "build_failed": (f"**Deploy failed at the build** — the running "
                         f"container was not touched"),
        "compose_failed": f"**Deploy failed starting the container**",
    }.get(action, f"**Deploy failed** — exit {outcome.get('exit')}, no result "
                  f"line, so what runs was not read")
    rows = ""
    if r:
        rows = (f" Before: `{short(r.get('before'))}`; after: "
                f"`{short(r.get('after'))}`; health: {r.get('health')}.")
    tail = outcome.get("tail") or ""
    tail_block = ("" if action in ("deployed", "carried") or not tail else
                  "\n\n```\n" + tail[-1500:] + "\n```")
    detail = f" {r['detail']}" if r.get("detail") and action not in ("deployed", "carried") else ""
    live = outcome.get("went_live") or []
    live_block = ""
    if live:
        more = " (and more)" if len(live) > 30 else ""
        live_block = (f"\n\n{len(live[:30])}{more} commit(s) went live with it, this "
                      f"job's and every one landed since the last deploy:\n\n"
                      + "\n".join(f"- `{ln}`" for ln in live[:30]))
    return (f"\n\n{head}.{rows}{detail} `{outcome['command']}`, exit "
            f"{outcome.get('exit')}, {outcome.get('seconds')} s, in its own unit "
            f"`{unit_name_from(outcome)}`; the landing stands whatever this says."
            f"{live_block}{tail_block}")


def unit_name_from(outcome: dict) -> str:
    return outcome.get("unit") or "loam-autodeploy-<job>.service"


def receipt_cell(outcome: dict | None, requested: str) -> str:
    """The `deploy at landing` row of the launcher's table."""
    if not requested:
        return "—"
    if outcome is None:
        return f"`{requested}` asked; the job did not land, so nothing deployed"
    if outcome.get("skipped"):
        return f"`{requested}` asked; not run ({outcome['skipped']})"
    r = outcome.get("result") or {}
    return (f"`{requested}` {r.get('action') or 'failed'}: "
            f"{short(r.get('before'))} → {short(r.get('after'))}, "
            f"{r.get('health') or '?'}")


def path_for(job_dir: Path) -> Path:
    return job_dir / "deploy.json"
