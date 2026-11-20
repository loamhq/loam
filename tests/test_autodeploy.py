#!/usr/bin/env python3
"""Verify that a requested deployment uses the landed revision and cannot roll a newer
release back.

Run: python3 tests/test_autodeploy.py"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_autodeploy as ad  # noqa: E402
import loam_engineer as eng  # noqa: E402


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


deploy = load("deploy_mod", ROOT / "bin" / "deploy")
engineer_job = load("engineer_job_mod", ROOT / "bin" / "engineer-job")

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


A = "a" * 40      # earlier main
B = "b" * 40      # later main, carries A
C = "c" * 40      # the commit being deployed


class Box:
    """bin/deploy's view of Docker and git, in memory."""

    def __init__(self, running, images=(), healthy=True, ancestors=(),
                 build_fails=False):
        self.running = running
        self.images = set(images)
        self.healthy = healthy
        self.ancestors = set(ancestors)      # (older, newer)
        self.build_fails = build_fails
        self.ups: list[str] = []
        self.pruned = False

    def install(self):
        d = deploy
        d.resolve_commit = lambda app, ref: ref
        d.running_commit = lambda app: self.running
        d.is_ancestor = lambda app, o, n: (o, n) in self.ancestors
        d.health_of = lambda name: "healthy" if self.running else "not running"
        d.image_exists = lambda tag: tag in self.images

        def build(app, full):
            if self.build_fails:
                d.die("docker build failed:\nboom")
            self.images.add(d.image_tag(app, full))
        d.build = build

        def compose(app, *args, env=None, **kw):
            if args and args[0] == "up":
                short = env["LOAM_COMMIT"]
                full = next(c for c in (A, B, C) if c.startswith(short))
                self.ups.append(short)
                self.running = full
            return subprocess.CompletedProcess(args, 0, stdout="log line\n", stderr="")
        d.compose = compose

        def wait_healthy(name, seconds=90):
            return "healthy" if (self.healthy or self.running != C) else "unhealthy"
        d.wait_healthy = wait_healthy

        def prune(app, previous=None):
            self.pruned = True
        d.prune = prune


class FakeApp:
    name = "loam-ui"


def run_up(box: Box, target: str, forward_only=True):
    box.install()
    out = io.StringIO()
    code = 0
    with redirect_stdout(out):
        try:
            deploy.up(FakeApp(), target, forward_only=forward_only)
        except SystemExit as exc:
            code = exc.code
    return code, ad.parse_result(out.getvalue()), out.getvalue()


def test_deploy_up() -> None:
    # Two landings close together: B's deploy ran first; A's must not undo it.
    box = Box(running=B, images={"loam-ui:bbbbbbb"}, ancestors={(A, B)})
    code, r, _ = run_up(box, A)
    check("forward-only: a commit carried by what runs changes nothing",
          (code, r["action"], r["before"], r["after"], box.ups), (0, "carried", B, B, []))
    # Without the flag, the same call is a deliberate rollback, as before.
    box = Box(running=B, images={"loam-ui:aaaaaaa", "loam-ui:bbbbbbb"}, ancestors={(A, B)})
    code, r, _ = run_up(box, A, forward_only=False)
    check("without --forward-only an older commit is deployed, as before",
          (code, r["action"], r["after"]), (0, "deployed", A))
    # A healthy up: built, started, pruned, reported.
    box = Box(running=B, images={"loam-ui:bbbbbbb"})
    code, r, _ = run_up(box, C)
    check("a healthy up reports before, after and health",
          (code, r["action"], r["before"], r["after"], r["health"], box.pruned,
           "loam-ui:ccccccc" in box.images),
          (0, "deployed", B, C, "healthy", True, True))
    # Unhealthy: the previous image goes back, nothing is pruned, exit 2.
    box = Box(running=B, images={"loam-ui:bbbbbbb"}, healthy=False)
    code, r, out = run_up(box, C)
    check("an unhealthy up puts the previous image back",
          (code, r["action"], r["before"], r["after"], box.ups, box.pruned),
          (2, "rolled_back", B, B, ["ccccccc", "bbbbbbb"], False))
    check("...and prints the unhealthy container's last lines",
          "the unhealthy container's last lines" in out, True)
    # Build fails: the running container is not touched.
    box = Box(running=B, images={"loam-ui:bbbbbbb"}, build_fails=True)
    code, r, _ = run_up(box, C)
    check("a failed build leaves what runs alone and says so",
          (code, r["action"], r["after"], box.ups), (1, "build_failed", B, []))


def test_library() -> None:
    check("deploy: true / 'yes' / absent",
          (ad.wanted(True), ad.wanted("yes"), ad.wanted(None), ad.wanted(False),
           ad.wanted("")), (True, True, False, False, False))
    check("only a pushed landing deploys",
          (ad.should_deploy({"state": "landed", "sha": C, "pushed": True}),
           ad.should_deploy({"state": "landed", "sha": C, "pushed": "rejected"}),
           ad.should_deploy({"state": "not_landed", "sha": None}),
           ad.should_deploy({"state": "landed_earlier", "sha": A})),
          (C, None, None, A))
    argv = ad.argv("loam-ui", C, "20261112-000000-deadbeef")
    check("the command is bin/deploy's, forward-only, in a user unit of its own",
          (argv[:3], argv[-6:], "--unit=loam-autodeploy-20261112-000000-deadbeef" in argv),
          (["systemd-run", "--user", "--wait"],
           ["sudo", "-n", str(ad.DEPLOY_BIN), "loam-ui", "up", C][1:] + ["--forward-only"],
           True))

    def runner(argv, **kw):
        out = ("  building loam-ui:ccccccc\n" + ad.RESULT_PREFIX + json.dumps(
            {"app": "loam-ui", "action": "deployed", "target": C, "before": B,
             "after": C, "health": "healthy"}) + "\n")
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
    said: list[str] = []
    outcome = ad.run("loam-ui", C, "j", say=said.append, runner=runner)
    check("run reads the result line", (outcome["exit"], outcome["result"]["action"],
                                        outcome["unit"]),
          (0, "deployed", "loam-autodeploy-j.service"))
    text = ad.receipt_text(outcome)
    check("the receipt says live, before and after, and health",
          ("**Deploy: live at `ccccccc`**" in text, "Before: `bbbbbbb`" in text,
           "health: healthy" in text, "the landing stands" in text),
          (True, True, True, True))
    outcome_live = {**outcome, "went_live": ["ccccccc This job", "bbbbbbb Staging only"]}
    text_live = ad.receipt_text(outcome_live)
    check("the receipt names every commit that went live, not only this job's",
          ("2 commit(s) went live" in text_live, "- `bbbbbbb Staging only`" in text_live),
          (True, True))
    check("went_live reads nothing for an unchanged or unlabelled range",
          (ad.went_live(ROOT, B, B), ad.went_live(ROOT, "loam-ui:old", C)), ([], []))
    check("the table row", ad.receipt_cell(outcome, "loam-ui"),
          "`loam-ui` deployed: bbbbbbb → ccccccc, healthy")

    def broken(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="",
                                           stderr="sudo: a password is required\n")
    outcome = ad.run("loam-ui", C, "j", runner=broken)
    text = ad.receipt_text(outcome)
    check("a deploy that never ran says so, with its last lines",
          ("**Deploy failed** — exit 1, no result line" in text,
           "a password is required" in text), (True, True))
    check("no deploy asked, no row", ad.receipt_cell(None, ""), "—")
    check("asked but not landed",
          ad.receipt_cell(None, "loam-ui"),
          "`loam-ui` asked; the job did not land, so nothing deployed")

    projects = eng.projects()
    if "loam-ui" in projects:
        check("loam-ui means the loam-ui record",
              ad.app_for(projects["loam-ui"]), ("loam-ui", None))
    check("Loam itself is refused",
          ad.app_for(projects["Loam"])[0], None)


def test_accept() -> None:
    projects = eng.projects()
    if "loam-ui" not in projects:
        print("SKIP  accept: no loam-ui project on this box")
        return
    base = {"project": "loam-ui", "title": "A small fix", "brief": "Do it.",
            "involvement": "Go and do it."}
    said = engineer_job.accept({**base, "deploy": True,
                                "involvement": "Read-only. Change nothing."}, "ada")
    check("deploy on a read-only brief is refused",
          isinstance(said, str) and "read-only" in said, True)
    with tempfile.TemporaryDirectory() as tmp:
        claimed = Path(tmp)
        real = eng.claim_job_dir
        eng.claim_job_dir = lambda project: ("20261112-000000-0000aaaa", claimed)
        try:
            said = engineer_job.accept({**base, "deploy": True}, "ada")
            head = (claimed / "brief.md").read_text(encoding="utf-8")
            check("an explicit yes writes `deploy: loam-ui` into the header",
                  (isinstance(said, tuple), "\ndeploy: loam-ui\n" in head), (True, True))
            meta = engineer_job.read_brief_meta({"brief": claimed / "brief.md"})
            check("...and the launcher reads it back", meta["deploy"], "loam-ui")
            said = engineer_job.accept({**base, "involvement": "Land and deploy."}, "ada")
            head = (claimed / "brief.md").read_text(encoding="utf-8")
            check("the word in the involvement alone deploys nothing",
                  "\ndeploy:" in head, False)
        finally:
            eng.claim_job_dir = real


def main() -> int:
    test_deploy_up()
    test_library()
    test_accept()
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
