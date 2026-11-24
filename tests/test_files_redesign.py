#!/usr/bin/env python3
"""Tests for the Files redesign: Recent attribution, grouping, filters,
windowing/fallback, vault cleanup, new-install seed, no seeded pins, and the
unified quick-actions plugin."""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-files-redesign-"))

HOME_DIR = TMP / "home"
VAULT = TMP / "vault"
SPACES = TMP / "ai-labs"
STATE = TMP / "state"
PLUGINS = TMP / "plugins"
for d in (HOME_DIR, VAULT, SPACES, STATE, PLUGINS):
    d.mkdir(parents=True, exist_ok=True)

CONF = TMP / "loam.toml"
CONF.write_text(
    f"""[owner]
id = "sam"
name = "Sam"
link = "[[Sam]]"

[box]
timezone = "America/Los_Angeles"

[paths]
home = "{HOME_DIR}"
root = "{AGENTS}"
vault = "{VAULT}"
workspaces = "{SPACES}"
state = "{STATE}"
plugins = "{PLUGINS}"
logs = "{TMP / 'logs'}"

[vault]
inbox = "Inbox"
conversations = "Conversations"
attachments = "Attachments"
boards = "Boards"
general_board = "Inbox/from-agents/comms.md"
projects = "Projects"
system = "System"
""",
    encoding="utf-8",
)
os.environ["LOAM_CONFIG"] = str(CONF)
os.environ["LOAM_PLUGINS_DIR"] = str(PLUGINS)
os.environ["LOAM_CONTRIBUTIONS_FOLLOW"] = "0"
os.environ["LOAM_OFFLINE"] = "1"
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "host-settings.json")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_config as lc  # noqa: E402
import loam_contributions as contrib  # noqa: E402
import loam_files as files  # noqa: E402
import loam_plugins as lp  # noqa: E402
import loam_recent as recent  # noqa: E402
import loam_settings as settings  # noqa: E402
import loam_skills as skills  # noqa: E402
import loam_thread_files as thread_files  # noqa: E402
import loam_vault_cleanup as cleanup  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail="") -> None:
    results.append(bool(ok))
    tail = f"  | {str(detail)[:260]}" if detail != "" else ""
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}{tail}")


def seed_persona(pid: str, name: str) -> None:
    pdir = SPACES / pid
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "soul.md").write_text(f"# {name}\n", encoding="utf-8")
    (pdir / "agent.json").write_text(
        json.dumps({"id": pid, "name": name, "workspace": str(pdir), "vault": str(VAULT)}),
        encoding="utf-8",
    )


def set_mtime(path: Path, ts: float) -> int:
    ns = int(ts * 1_000_000_000)
    os.utime(path, ns=(ns, ns))
    return path.stat().st_mtime_ns


def reset_ledger() -> None:
    p = recent.db_path()
    for suffix in ("", "-wal", "-shm"):
        cand = Path(str(p) + suffix)
        if cand.exists():
            cand.unlink()


seed_persona("ada", "Ada")
seed_persona("finn", "Finn")

# ---------------------------------------------------------------------------
# 1. New install seed: About me, Start here.html, README, Loam files only
# ---------------------------------------------------------------------------
seed_vault = TMP / "fresh-seed-vault"
created = skills.seed_vault(seed_vault)
check(
    "new install seed: About me.md, Start here.html, README.md exist",
    (seed_vault / "About me.md").is_file()
    and (seed_vault / "Projects" / "Loam" / "Start here.html").is_file()
    and (seed_vault / "README.md").is_file(),
    created,
)
check(
    "new install seed: Loam files folders exist (Conversations, Attachments, Boards, System)",
    all((seed_vault / name).is_dir() for name in ("Conversations", "Attachments", "Boards", "System")),
)
check(
    "new install seed: NO Inbox, Daily, or Images folders",
    not any((seed_vault / name).exists() for name in ("Inbox", "Daily", "Images")),
    sorted(p.name for p in seed_vault.iterdir()),
)
explainer = (seed_vault / "Projects" / "Loam" / "Start here.html").read_text(encoding="utf-8")
check(
    "new install seed: Start here.html is self-contained with light+dark styles",
    "prefers-color-scheme: dark" in explainer and "http://" not in explainer and "https://" not in explainer,
)

# ---------------------------------------------------------------------------
# 2. No seeded pins + configured Loam files list + root names
# ---------------------------------------------------------------------------
for folder in ("Conversations", "Attachments", "Boards", "System", "Notes"):
    (VAULT / folder).mkdir(parents=True, exist_ok=True)

roots_body = files.roots()
check(
    "roots: Vault -> Your files, AI Spaces -> Agent files",
    [(r["root"], r["name"]) for r in roots_body["roots"][:2]]
    == [("vault", "Your files"), ("spaces", "Agent files")],
    roots_body["roots"][:2],
)
check("roots: no seeded shortcuts/pins", roots_body["shortcuts"] == [], roots_body["shortcuts"])
loam_items = {item["key"]: item["path"] for item in roots_body["loam_files"]}
check(
    "roots: loam_files reports all configured machinery folders",
    loam_items == {
        "conversations": "Conversations",
        "attachments": "Attachments",
        "boards": "Boards",
        "system": "System",
    },
    loam_items,
)

# ---------------------------------------------------------------------------
# 3. Attribution: app, persona by, hook, diff, external, engineer/receipts excluded
# ---------------------------------------------------------------------------
reset_ledger()
fixed_now = datetime(2026, 11, 22, 14, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()

# 3a. App create + save -> owner; save with by=ada -> agent
files.create_file({"root": "vault", "path": "Notes/owner-note.md", "text": "# Owner note\n"})
set_mtime(VAULT / "Notes" / "owner-note.md", fixed_now - 600)
recent.record_write("Notes/owner-note.md", kind="owner", actor="sam", source="app",
                    mtime_ns=(VAULT / "Notes" / "owner-note.md").stat().st_mtime_ns)

files.create_file({"root": "vault", "path": "Notes/by-ada.md", "text": "# Draft\n"})
ada_rev = files.read_text("vault", "Notes/by-ada.md")["revision"]
files.save_text({"root": "vault", "path": "Notes/by-ada.md", "text": "# By Ada\n",
                 "base_revision": ada_rev, "by": "ada"})
ada_ts = set_mtime(VAULT / "Notes" / "by-ada.md", fixed_now - 500)
recent.record_write("Notes/by-ada.md", kind="agent", actor="ada", source="app", mtime_ns=ada_ts)

# 3b. Hook Write & generate_image -> agent
hook_file = VAULT / "Notes" / "hook-written.md"
hook_file.write_text("# From hook\n", encoding="utf-8")
set_mtime(hook_file, fixed_now - 450)
hook_payload = json.dumps({
    "tool_name": "Write",
    "cwd": str(SPACES / "ada"),
    "tool_input": {"file_path": str(hook_file)},
})
proc = subprocess.run(
    [sys.executable, str(AGENTS / "hooks" / "loading_size.py")],
    input=hook_payload,
    text=True,
    capture_output=True,
    cwd=str(SPACES / "ada"),
    env={**os.environ, "CLAUDE_PROJECT_DIR": str(SPACES / "ada")},
    check=True,
)
check("hook: loading_size.py exits 0", proc.returncode == 0, proc.stderr)

img_file = VAULT / "Notes" / "diagram.png"
img_file.write_bytes(b"\x89PNG\r\n\x1a\n")
set_mtime(img_file, fixed_now - 420)
img_payload = json.dumps({
    "tool_name": "mcp__images__generate_image",
    "cwd": str(SPACES / "finn"),
    "tool_input": {"prompt": "diagram"},
    "tool_response": json.dumps({"paths": [str(img_file)]}),
})
subprocess.run(
    [sys.executable, str(AGENTS / "hooks" / "loading_size.py")],
    input=img_payload,
    text=True,
    capture_output=True,
    cwd=str(SPACES / "finn"),
    env={**os.environ, "CLAUDE_PROJECT_DIR": str(SPACES / "finn")},
    check=True,
)

# 3c. Turn/wake start-end diff records agent write without clobbering concurrent app owner save
snap_before = recent.snapshot_vault()
t_start = time.time()
bash_file = VAULT / "Notes" / "bash-output.md"
bash_file.write_text("# From bash\n", encoding="utf-8")
concurrent_owner = VAULT / "Notes" / "concurrent.md"
concurrent_owner.write_text("# Owner concurrent\n", encoding="utf-8")
recent.record_write(concurrent_owner, kind="owner", actor="sam", source="app", recorded_at=time.time())
recent.record_diff(snap_before, kind="agent", actor="finn", source="thread", started_at=t_start)
# Normalize timestamps to fixed_now window for deterministic querying
for p_rel, delta in (("Notes/bash-output.md", 380), ("Notes/concurrent.md", 360)):
    ns = set_mtime(VAULT / p_rel, fixed_now - delta)
    rec = recent._load_ledger()[p_rel]
    recent.record_write(p_rel, kind=rec["kind"], actor=rec["actor"], source=rec["source"], mtime_ns=ns)

# 3d. Engineer write & launcher receipt & Loam files & schedule.md -> NEVER appear
eng_before = recent.snapshot_vault()
eng_note = VAULT / "Notes" / "engineer-touched.md"
eng_note.write_text("# Engineer wrote this\n", encoding="utf-8")
set_mtime(eng_note, fixed_now - 300)
recent.record_diff(eng_before, kind="engineer", actor="engineer", source="engineer-job")

receipt_dir = VAULT / "Projects" / "Demo" / "receipts"
receipt_dir.mkdir(parents=True, exist_ok=True)
receipt_file = receipt_dir / "2026-11-22-job.md"
receipt_file.write_text("---\ncreated_by: \"[[Ada]]\"\n---\nReceipt\n", encoding="utf-8")
set_mtime(receipt_file, fixed_now - 280)

eng_dir = VAULT / "Projects" / "Loam" / "engineer"
eng_dir.mkdir(parents=True, exist_ok=True)
(eng_dir / "role.md").write_text("# Role\n", encoding="utf-8")
set_mtime(eng_dir / "role.md", fixed_now - 260)

sched_dir = VAULT / "Projects" / "Loam" / "infrastructure"
sched_dir.mkdir(parents=True, exist_ok=True)
(sched_dir / "schedule.md").write_text("# Schedule\n", encoding="utf-8")
set_mtime(sched_dir / "schedule.md", fixed_now - 240)

(VAULT / "Boards" / "t-123.md").write_text("# Board\n", encoding="utf-8")
set_mtime(VAULT / "Boards" / "t-123.md", fixed_now - 220)
(VAULT / "Conversations" / "2026-11-22.md").write_text("# Chat\n", encoding="utf-8")
set_mtime(VAULT / "Conversations" / "2026-11-22.md", fixed_now - 210)
(VAULT / "System" / "loam-wake-failures.md").write_text("# Alert\n", encoding="utf-8")
set_mtime(VAULT / "System" / "loam-wake-failures.md", fixed_now - 200)

# Boards outside Boards/: General board (vault.general_board) and a thread board
# recorded in thread-files.json. Both are attributed to an agent and must still
# stay out of Recent.
gen_board = VAULT / "Inbox" / "from-agents" / "comms.md"
gen_board.parent.mkdir(parents=True, exist_ok=True)
gen_board.write_text("# General board\n", encoding="utf-8")
gen_ns = set_mtime(gen_board, fixed_now - 195)
recent.record_write(gen_board, kind="agent", actor="ada", source="hook", mtime_ns=gen_ns)

outside_board = VAULT / "Notes" / "thread-board-outside.md"
outside_board.write_text("# Thread board outside Boards/\n", encoding="utf-8")
out_ns = set_mtime(outside_board, fixed_now - 190)
recent.record_write(outside_board, kind="agent", actor="finn", source="hook", mtime_ns=out_ns)
tf_data = PLUGINS / "_data" / "thread-files.json"
tf_data.parent.mkdir(parents=True, exist_ok=True)
tf_data.write_text(
    json.dumps({
        "boards": {
            "t-outside": {"path": "Notes/thread-board-outside.md", "title": "Outside"},
        }
    }),
    encoding="utf-8",
)

# 3e. Unattributed writes (running engineer job, script, unrecorded overwrite)
#     are EXCLUDED from Recent — never credited to owner.
unattr_file = VAULT / "Notes" / "unattributed-script.md"
unattr_file.write_text("# Written by background script or running engineer\n", encoding="utf-8")
set_mtime(unattr_file, fixed_now - 180)

overwritten = VAULT / "Notes" / "overwritten.md"
overwritten.write_text("# Agent draft\n", encoding="utf-8")
old_ns = set_mtime(overwritten, fixed_now - 900)
recent.record_write(overwritten, kind="agent", actor="ada", source="hook", mtime_ns=old_ns)
overwritten.write_text("# Unrecorded external write\n", encoding="utf-8")
set_mtime(overwritten, fixed_now - 150)

# 3f. Non-readable files (.json, .py, .js, .txt) NEVER appear in Recent even if attributed
for bad_name in ("results.json", "script.py", "helper.js", "dump.txt", "trace.jsonl"):
    bp = VAULT / "Notes" / bad_name
    bp.write_text("data\n", encoding="utf-8")
    bns = set_mtime(bp, fixed_now - 140)
    recent.record_write(bp, kind="owner", actor="sam", source="app", mtime_ns=bns)

# Readable PDF and HTML DO appear when positively attributed
pdf_file = VAULT / "Notes" / "report.pdf"
pdf_file.write_bytes(b"%PDF-1.4\n")
pdf_ns = set_mtime(pdf_file, fixed_now - 135)
recent.record_write(pdf_file, kind="owner", actor="sam", source="app", mtime_ns=pdf_ns)

# 3g. Rename carries attribution
renamed_src = VAULT / "Notes" / "to-rename.md"
renamed_src.write_text("# Ada wrote\n", encoding="utf-8")
ren_ns = set_mtime(renamed_src, fixed_now - 120)
recent.record_write(renamed_src, kind="agent", actor="ada", source="hook", mtime_ns=ren_ns)
files.rename({"root": "vault", "path": "Notes/to-rename.md", "name": "renamed-by-ada.md"})
set_mtime(VAULT / "Notes" / "renamed-by-ada.md", fixed_now - 120)

# 3h. Positive Syncthing owner attribution (ItemFinished + /rest/db/file remote modifiedBy)
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading as _threading
from urllib.parse import parse_qs, urlparse

synced_file = VAULT / "Notes" / "synced-from-phone.md"
synced_file.write_text("# Edited on phone via Obsidian\n", encoding="utf-8")
set_mtime(synced_file, fixed_now - 110)

remote_backfill_file = VAULT / "Notes" / "remote-device-note.md"
remote_backfill_file.write_text("# Synced earlier from laptop\n", encoding="utf-8")
set_mtime(remote_backfill_file, fixed_now - 105)

ST_TOKEN = "test-syncthing-key"
st_state = {
    "events": [
        {
            "id": 501,
            "type": "ItemFinished",
            "data": {
                "action": "update",
                "error": None,
                "folder": "loam-vault",
                "item": "Notes/synced-from-phone.md",
                "type": "file",
            },
        },
        {
            "id": 502,
            "type": "ItemFinished",
            "data": {
                "action": "update",
                "error": None,
                "folder": "loam-vault",
                "item": "Notes/ignored-sync.json",
                "type": "file",
            },
        },
    ],
    "remote_files": {"Notes/remote-device-note.md": "REMOTE1"},
}


class _MockSyncthing(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.headers.get("X-API-Key") != ST_TOKEN:
            self.send_response(403)
            self.end_headers()
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/rest/system/status":
            body = {"myID": "LOCAL01-AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG",
                    "startTime": "2026-11-22T00:00:00Z"}
        elif parsed.path == "/rest/config/folders":
            body = [{"id": "loam-vault", "path": str(VAULT)}]
        elif parsed.path == "/rest/events":
            since = int((qs.get("since") or ["0"])[0])
            body = [e for e in st_state["events"] if e["id"] > since]
        elif parsed.path == "/rest/db/file":
            rel = (qs.get("file") or [""])[0]
            mby = st_state["remote_files"].get(rel, "LOCAL01")
            body = {"local": {"modifiedBy": mby}}
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


st_srv = HTTPServer(("127.0.0.1", 0), _MockSyncthing)
st_port = st_srv.server_address[1]
_threading.Thread(target=st_srv.serve_forever, daemon=True).start()
st_cfg = {"host": "127.0.0.1", "port": st_port, "tls": False, "apikey": ST_TOKEN}
try:
    sync_res = recent.sync_syncthing_events(vault=VAULT, st_cfg=st_cfg, timeout=0)
    check(
        "syncthing: ItemFinished records synced vault file positively as owner",
        sync_res["ok"] is True and "Notes/synced-from-phone.md" in sync_res["recorded"],
        sync_res,
    )
    # Concurrent agent turn diff MUST NOT clobber syncthing owner attribution
    snap_pre = recent.snapshot_vault()
    snap_pre.pop("Notes/synced-from-phone.md", None)
    recent.record_diff(snap_pre, kind="agent", actor="ada", source="turn", started_at=time.time() - 5)
    check(
        "syncthing: concurrent agent record_diff preserves syncthing owner record",
        recent._load_ledger().get("Notes/synced-from-phone.md", {}).get("kind") == "owner"
        and recent._load_ledger().get("Notes/synced-from-phone.md", {}).get("source") == "syncthing",
        recent._load_ledger().get("Notes/synced-from-phone.md"),
    )

    rec_all = recent.recent_files("all", "week", now=fixed_now)
    flat_items = []
    for day in rec_all["days"]:
        for entry in day["items"]:
            if entry["kind"] == "group":
                for it in entry["items"]:
                    flat_items.append((it["path"], entry["author"]["kind"], entry["author"]["id"]))
            else:
                flat_items.append((entry["path"], entry["author"]["kind"], entry["author"]["id"]))
    by_path = {p: (k, a) for p, k, a in flat_items}

    check(
        "attribution: owner app, concurrent app, pdf, and syncthing -> owner",
        all(by_path.get(p) == ("owner", "sam") for p in (
            "Notes/owner-note.md",
            "Notes/concurrent.md",
            "Notes/report.pdf",
            "Notes/synced-from-phone.md",
        )),
        by_path,
    )
    check(
        "attribution: unattributed writes & unrecorded overwrites are ABSENT from Recent",
        "Notes/unattributed-script.md" not in by_path
        and "Notes/overwritten.md" not in by_path,
        by_path,
    )
    check(
        "readable filter: .json/.jsonl/.py/.js/.txt excluded from Recent even when attributed",
        not any(f"Notes/{bad}" in by_path for bad in ("results.json", "script.py", "helper.js", "dump.txt", "trace.jsonl")),
        sorted(by_path),
    )
    check(
        "attribution: app by=, hook Write, hook generate_image, diff, rename -> agents",
        by_path.get("Notes/by-ada.md") == ("agent", "ada")
        and by_path.get("Notes/hook-written.md") == ("agent", "ada")
        and by_path.get("Notes/renamed-by-ada.md") == ("agent", "ada")
        and by_path.get("Notes/diagram.png") == ("agent", "finn")
        and by_path.get("Notes/bash-output.md") == ("agent", "finn"),
        by_path,
    )
    check(
        "exclusions: engineer writes, receipts, engineer/, schedule.md, Loam files absent",
        not any(p in by_path for p in (
            "Notes/engineer-touched.md",
            "Projects/Demo/receipts/2026-11-22-job.md",
            "Projects/Loam/engineer/role.md",
            "Projects/Loam/infrastructure/schedule.md",
            "Boards/t-123.md",
            "Conversations/2026-11-22.md",
            "System/loam-wake-failures.md",
        )),
        sorted(by_path),
    )
    check(
        "exclusions: boards outside Boards/ (general_board and thread-files.json) absent from Recent",
        "Inbox/from-agents/comms.md" not in by_path
        and "Notes/thread-board-outside.md" not in by_path
        and recent.excluded_path("Inbox/from-agents/comms.md")
        and recent.excluded_path("Notes/thread-board-outside.md"),
        sorted(by_path),
    )
finally:
    st_srv.shutdown()

# ---------------------------------------------------------------------------
# 4. Grouping (3+ same author/folder/local day) + Filter (all/you/agents)
# ---------------------------------------------------------------------------
# Ada already has 3 files in Notes today (by-ada, hook-written, renamed-by-ada) -> grouped!
# Finn has 2 files in Notes today (diagram.png, bash-output.md) -> NOT grouped!
today_entries = rec_all["days"][0]["items"]
ada_groups = [e for e in today_entries if e["kind"] == "group" and e["author"]["id"] == "ada"]
finn_rows = [e for e in today_entries if e["author"]["id"] == "finn"]
check(
    "grouping: 3 files by Ada in Notes today collapse to 1 group row",
    len(ada_groups) == 1 and ada_groups[0]["label"] == "3 files in Notes" and ada_groups[0]["count"] == 3,
    ada_groups,
)
check(
    "grouping: 2 files by Finn in Notes today stay separate rows",
    len(finn_rows) == 2 and all(e["kind"] == "file" for e in finn_rows),
    finn_rows,
)
rec_you = recent.recent_files("you", "week", now=fixed_now)
rec_agents = recent.recent_files("agents", "week", now=fixed_now)
check(
    "filter: who=you returns only owner entries",
    all(e["author"]["kind"] == "owner" for d in rec_you["days"] for e in d["items"])
    and len(rec_you["days"]) == 1,
)
check(
    "filter: who=agents returns only agent entries",
    all(e["author"]["kind"] == "agent" for d in rec_agents["days"] for e in d["items"])
    and len(rec_agents["days"]) == 1,
)

# ---------------------------------------------------------------------------
# 5. Windowing: 48h cap 10 (group=1), 48h empty -> week fallback 10, Show more -> week 50, >7d excluded
# ---------------------------------------------------------------------------
win_vault = TMP / "window-vault"
(win_vault / "Batch").mkdir(parents=True)
(win_vault / "Solo").mkdir(parents=True)
reset_ledger()
# Create 1 group (4 files) + 11 singles within past 48h = 12 rows total in 48h, plus 5 older in week, plus 2 > 7 days
for i in range(4):
    p = win_vault / "Batch" / f"g{i}.md"
    p.write_text(f"group {i}\n", encoding="utf-8")
    ns = set_mtime(p, fixed_now - 100 - i * 10)
    recent.record_write(p, kind="agent", actor="ada", source="hook", vault=win_vault, mtime_ns=ns)
for i in range(11):
    sub = win_vault / "Solo" / f"d{i}"
    sub.mkdir()
    p = sub / f"s{i}.md"
    p.write_text(f"solo {i}\n", encoding="utf-8")
    ns = set_mtime(p, fixed_now - 1000 - i * 60)
    recent.record_write(p, kind="owner", actor="sam", source="app", vault=win_vault, mtime_ns=ns)
for i in range(5):
    sub = win_vault / "Solo" / f"w{i}"
    sub.mkdir()
    p = sub / f"w{i}.md"
    p.write_text(f"week {i}\n", encoding="utf-8")
    ns = set_mtime(p, fixed_now - 3 * 86400 - i * 60)
    recent.record_write(p, kind="owner", actor="sam", source="app", vault=win_vault, mtime_ns=ns)
for i in range(2):
    p = win_vault / "Solo" / f"old{i}.md"
    p.write_text(f"old {i}\n", encoding="utf-8")
    ns = set_mtime(p, fixed_now - 9 * 86400 - i * 60)
    recent.record_write(p, kind="owner", actor="sam", source="app", vault=win_vault, mtime_ns=ns)

w_def = recent.recent_files("all", "default", vault=win_vault, now=fixed_now)
w_week = recent.recent_files("all", "week", vault=win_vault, now=fixed_now)
check(
    "window default: 48h non-empty returns max 10 rows (group counts as 1), has_more=True",
    w_def["total_shown"] == 10 and w_def["used_fallback"] is False and w_def["has_more"] is True
    and w_def["days"][0]["items"][0]["kind"] == "group",
    (w_def["total_shown"], w_def["used_fallback"], w_def["has_more"]),
)
check(
    "window week: Show more returns all 17 rows within 7d and excludes >7d old files",
    w_week["total_shown"] == 17 and w_week["has_more"] is False
    and all("old" not in (e.get("path") or "") for d in w_week["days"] for e in d["items"]),
    w_week["total_shown"],
)

# 48h empty -> fallback to up to 10 from past 7 days
fb_vault = TMP / "fallback-vault"
fb_vault.mkdir()
reset_ledger()
for i in range(14):
    sub = fb_vault / f"f{i}"
    sub.mkdir()
    p = sub / f"note{i}.md"
    p.write_text("hi\n", encoding="utf-8")
    ns = set_mtime(p, fixed_now - 4 * 86400 - i * 60)
    recent.record_write(p, kind="owner", actor="sam", source="app", vault=fb_vault, mtime_ns=ns)
fb_def = recent.recent_files("all", "default", vault=fb_vault, now=fixed_now)
check(
    "window fallback: empty 48h falls back to 10 from past week with used_fallback=True, has_more=True",
    fb_def["total_shown"] == 10 and fb_def["used_fallback"] is True and fb_def["has_more"] is True,
    (fb_def["total_shown"], fb_def["used_fallback"], fb_def["has_more"]),
)

# Cap at 50 rows in week window
cap_vault = TMP / "cap-vault"
cap_vault.mkdir()
reset_ledger()
for i in range(60):
    sub = cap_vault / f"c{i}"
    sub.mkdir()
    p = sub / f"f{i}.md"
    p.write_text("x\n", encoding="utf-8")
    ns = set_mtime(p, fixed_now - 3600 - i * 30)
    recent.record_write(p, kind="owner", actor="sam", source="app", vault=cap_vault, mtime_ns=ns)
cap_week = recent.recent_files("all", "week", vault=cap_vault, now=fixed_now)
check("window week cap: never exceeds 50 rows", cap_week["total_shown"] == 50, cap_week["total_shown"])

# ---------------------------------------------------------------------------
# 6. Backfill: positive-only (frontmatter/HTML hints + Syncthing remote modifiedBy);
#    plain unattributed notes and non-readable files stay OUT of Recent
# ---------------------------------------------------------------------------
bf_vault = TMP / "backfill-vault"
(bf_vault / "Docs").mkdir(parents=True)
reset_ledger()
(bf_vault / "Docs" / "md-agent.md").write_text("---\nlast_modified_by: \"[[Ada]]\"\n---\nHello\n", encoding="utf-8")
(bf_vault / "Docs" / "html-agent.html").write_text("<!-- created_by: Finn -->\n<h1>Hi</h1>\n", encoding="utf-8")
(bf_vault / "Docs" / "owner-hint.md").write_text("---\nlast_modified_by: \"[[Sam]]\"\n---\nOwner note\n", encoding="utf-8")
(bf_vault / "Docs" / "eng-hint.md").write_text("---\ncreated_by: Platform engineer\n---\nLog\n", encoding="utf-8")
(bf_vault / "Docs" / "unattributed.md").write_text("# Unattributed note\n", encoding="utf-8")
(bf_vault / "Docs" / "remote-synced.md").write_text("# Synced from phone without frontmatter\n", encoding="utf-8")
(bf_vault / "Docs" / "data.json").write_text("{\"a\": 1}\n", encoding="utf-8")
for name in ("md-agent.md", "html-agent.html", "owner-hint.md", "eng-hint.md", "unattributed.md", "remote-synced.md", "data.json"):
    set_mtime(bf_vault / "Docs" / name, fixed_now - 300)

class _BackfillSyncthing(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/rest/system/status":
            body = {"myID": "LOCAL01-AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG",
                    "startTime": "2026-11-22T00:00:00Z"}
        elif parsed.path == "/rest/config/folders":
            body = [{"id": "bf-vault", "path": str(bf_vault)}]
        elif parsed.path == "/rest/events":
            body = []
        elif parsed.path == "/rest/db/file":
            rel = (qs.get("file") or [""])[0]
            body = {"local": {"modifiedBy": "PHONE99" if rel == "Docs/remote-synced.md" else "LOCAL01"}}
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

bf_srv = HTTPServer(("127.0.0.1", 0), _BackfillSyncthing)
bf_port = bf_srv.server_address[1]
_threading.Thread(target=bf_srv.serve_forever, daemon=True).start()
try:
    bf_counts = recent.backfill_recent(
        bf_vault, now=fixed_now,
        st_cfg={"host": "127.0.0.1", "port": bf_port, "tls": False, "apikey": "k"},
    )
finally:
    bf_srv.shutdown()

bf_res = recent.recent_files("all", "week", vault=bf_vault, now=fixed_now)
bf_paths = {e["path"]: (e["author"]["kind"], e["author"]["id"]) for d in bf_res["days"] for e in d["items"]}
check(
    "backfill: positive hints & Syncthing remote -> included; unattributed & .json -> ABSENT",
    bf_counts["agent"] == 2 and bf_counts["owner"] == 2 and bf_counts["engineer"] == 1
    and bf_counts["unattributed"] == 1
    and bf_paths == {
        "Docs/md-agent.md": ("agent", "ada"),
        "Docs/html-agent.html": ("agent", "finn"),
        "Docs/owner-hint.md": ("owner", "sam"),
        "Docs/remote-synced.md": ("owner", "sam"),
    },
    (bf_counts, bf_paths),
)

# ---------------------------------------------------------------------------
# 7. Cleanup rule + board & alert note migration + quick-actions migration
# ---------------------------------------------------------------------------
check(
    "insubstantial rule: empty, frontmatter-only, and <=5 words are insubstantial; >5 words is substantial",
    cleanup.is_insubstantial_text("")
    and cleanup.is_insubstantial_text("---\ndate: 2026-11-21\n---\n\n# 2026-11-21\n")
    and cleanup.is_insubstantial_text("one two three four five")
    and not cleanup.is_insubstantial_text("one two three four five six"),
)

# Build a minimal-writing git vault: Inbox has only boards + wake-failures;
# Daily has one frontmatter-only daily note; Images is empty; Test/ and tmp.md exist.
mig_vault = TMP / "quiet-vault"
mig_vault.mkdir()
subprocess.run(["git", "-C", str(mig_vault), "init", "-q"], check=True)
subprocess.run(["git", "-C", str(mig_vault), "config", "user.name", "Test"], check=True)
subprocess.run(["git", "-C", str(mig_vault), "config", "user.email", "test@example.com"], check=True)

(mig_vault / "Inbox" / "boards").mkdir(parents=True)
(mig_vault / "Inbox" / "boards" / "t-abc.md").write_text("# Board for t-abc\n", encoding="utf-8")
(mig_vault / "Inbox" / "from-agents").mkdir(parents=True)
(mig_vault / "Inbox" / "from-agents" / "loam-wake-failures.md").write_text("# Failures\n", encoding="utf-8")
(mig_vault / "Inbox" / "from-agents" / "comms.md").write_text("# General board\n", encoding="utf-8")
(mig_vault / "Daily").mkdir()
(mig_vault / "Daily" / "2026-11-21.md").write_text("---\ncreated_by: \"[[Sam]]\"\n---\n\n# 2026-11-21\n", encoding="utf-8")
(mig_vault / "Images").mkdir()
(mig_vault / "Test").mkdir()
(mig_vault / "Test" / "tiny.md").write_text("hi\n", encoding="utf-8")
(mig_vault / "tmp.md").write_text("hi\n", encoding="utf-8")
subprocess.run(["git", "-C", str(mig_vault), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(mig_vault), "commit", "-q", "-m", "initial"], check=True)

# Record thread-files.json with both a per-thread board and a general board
tf_path = thread_files._binding_file()
tf_path.parent.mkdir(parents=True, exist_ok=True)
tf_path.write_text(json.dumps({
    "boards": {
        "t-abc": {"path": "Inbox/boards/t-abc.md", "created": "2026-11-20T00:00:00Z"},
        "general": {"path": "Inbox/from-agents/comms.md", "created": "2026-11-20T00:00:00Z"},
    }
}), encoding="utf-8")

# First test with general comms removed (Inbox has only boards + wake failures)
(mig_vault / "Inbox" / "from-agents" / "comms.md").unlink()
subprocess.run(["git", "-C", str(mig_vault), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(mig_vault), "commit", "-q", "-m", "drop comms"], check=True)

# Simulate daily-notes previously on
(PLUGINS / "installed.json").write_text(json.dumps({"on": {"daily-notes": True}, "plugins": {}}), encoding="utf-8")

report = cleanup.run_update_migration(mig_vault, dry_run=False)
check(
    "migration: boards moved to Boards/ with git history and thread-files.json updated",
    (mig_vault / "Boards" / "t-abc.md").is_file()
    and not (mig_vault / "Inbox" / "boards" / "t-abc.md").exists()
    and json.loads(tf_path.read_text())["boards"]["t-abc"]["path"] == "Boards/t-abc.md"
    and json.loads(tf_path.read_text())["boards"]["general"]["path"] == "Inbox/from-agents/comms.md",
    report["boards_moved"],
)
check(
    "migration: loam-wake-failures.md moved into System/",
    (mig_vault / "System" / "loam-wake-failures.md").is_file()
    and not (mig_vault / "Inbox" / "from-agents" / "loam-wake-failures.md").exists(),
    report["failure_log_moved"],
)
removed_folders = {r["folder"] for r in report["folders_removed"]}
check(
    "migration: empty/insubstantial Inbox, Daily, Images moved recoverably to .trash",
    removed_folders == {"Inbox", "Daily", "Images"}
    and not (mig_vault / "Inbox").exists()
    and not (mig_vault / "Daily").exists()
    and not (mig_vault / "Images").exists()
    and all((mig_vault / r["trash"]).exists() for r in report["folders_removed"]),
    report["folders_removed"],
)
check(
    "migration: non-seeded Test/ and tmp.md are untouched",
    (mig_vault / "Test" / "tiny.md").is_file() and (mig_vault / "tmp.md").is_file(),
)
check(
    "migration: cleaned-up install leaves quick-actions OFF",
    report["quick_actions"]["on"] is False
    and lp.load_list()["on"].get("quick-actions") is False
    and "daily-notes" not in lp.load_list()["on"],
    report["quick_actions"],
)

# Now test an active vault: Inbox and Daily have real writing (>5 words) -> both stay & quick-actions ON
active_vault = TMP / "active-vault"
(active_vault / "Inbox").mkdir(parents=True)
(active_vault / "Inbox" / "real.md").write_text("This note has more than five real words in it.\n", encoding="utf-8")
(active_vault / "Daily").mkdir(parents=True)
(active_vault / "Daily" / "2026-11-22.md").write_text("Today I worked on the Files redesign and tested everything.\n", encoding="utf-8")
(PLUGINS / "installed.json").write_text(json.dumps({"on": {"daily-notes": True}, "plugins": {}}), encoding="utf-8")
report_m = cleanup.run_update_migration(active_vault, dry_run=False)
check(
    "migration: substantial Inbox & Daily stay and quick-actions migrates ON (both)",
    (active_vault / "Inbox" / "real.md").is_file()
    and (active_vault / "Daily" / "2026-11-22.md").is_file()
    and report_m["quick_actions"]["on"] is True
    and report_m["quick_actions"]["actions"] == "both",
    report_m,
)

# Dry-run & standalone substance rule: even before folders are deleted, a vault
# with only a frontmatter-only Daily note and empty Inbox migrates OFF.
dry_vault = TMP / "dry-substance-vault"
(dry_vault / "Inbox").mkdir(parents=True)
(dry_vault / "Daily").mkdir(parents=True)
(dry_vault / "Daily" / "2026-11-21.md").write_text("---\ncreated_by: \"[[Sam]]\"\n---\n\n# 2026-11-21\n", encoding="utf-8")
(PLUGINS / "installed.json").write_text(json.dumps({"on": {"daily-notes": True}, "plugins": {}}), encoding="utf-8")
dry_qa = cleanup.migrate_quick_actions(dry_vault, dry_run=True)
check(
    "migration substance rule: frontmatter-only Daily + empty Inbox -> OFF even before cleanup",
    dry_qa["on"] is False and dry_qa["status"] == "migrated",
    dry_qa,
)

# Daily-only substantial vault -> ON with daily-note only
daily_only_vault = TMP / "daily-only-vault"
(daily_only_vault / "Inbox").mkdir(parents=True)
(daily_only_vault / "Daily").mkdir(parents=True)
(daily_only_vault / "Daily" / "2026-11-22.md").write_text("A genuine daily journal entry with plenty of real words.\n", encoding="utf-8")
(PLUGINS / "installed.json").write_text(json.dumps({"on": {}, "plugins": {}}), encoding="utf-8")
daily_only_qa = cleanup.migrate_quick_actions(daily_only_vault, dry_run=True)
check(
    "migration substance rule: substantial Daily only -> ON (daily-note)",
    daily_only_qa["on"] is True and daily_only_qa["actions"] == "daily-note",
    daily_only_qa,
)

# ---------------------------------------------------------------------------
# 8. quick-actions plugin name, persona settings parity + conditional vault.folder
# ---------------------------------------------------------------------------
qa_row = next(r for r in lp._rows() if r["id"] == "quick-actions")
check(
    "plugin display name: quick-actions is named 'Files quick actions'",
    qa_row["name"] == "Files quick actions",
    qa_row["name"],
)
lp.set_on("quick-actions", True)
keys = {e.key for e in settings.entries()}
check(
    "settings parity: plugin.quick-actions.{actions,inbox_folder,daily_folder} registered",
    {"plugin.quick-actions.actions", "plugin.quick-actions.inbox_folder", "plugin.quick-actions.daily_folder"}.issubset(keys),
    sorted(k for k in keys if "quick-actions" in k),
)
contrib.set_setting("quick-actions", "actions", "daily-note")
check(
    "quick-actions conditional folder: daily-note requests only daily_folder, not Inbox",
    contrib.vault_folders() == ["Daily"],
    contrib.vault_folders(),
)
contrib.set_setting("quick-actions", "actions", "new-note")
check(
    "quick-actions conditional folder: new-note requests only inbox_folder, not Daily",
    contrib.vault_folders() == ["Inbox"],
    contrib.vault_folders(),
)

# ---------------------------------------------------------------------------
# 9. Migration defers when a live pre-Boards broker is running, then completes
# ---------------------------------------------------------------------------
from unittest.mock import patch  # noqa: E402
def_vault = TMP / "deferred-vault"
(def_vault / "Inbox" / "boards").mkdir(parents=True)
(def_vault / "Inbox" / "boards" / "t-def.md").write_text("# Deferred board\n", encoding="utf-8")
tf_path.write_text(json.dumps({
    "boards": {"t-def": {"path": "Inbox/boards/t-def.md", "title": "Deferred"}}
}), encoding="utf-8")
with patch.object(cleanup, "_running_broker_rejects_boards", return_value=True):
    deferred = cleanup.run_update_migration(def_vault, dry_run=False)
check(
    "migration deferral: stale broker defers migration, preserves Inbox/boards, writes marker",
    deferred.get("deferred") is True
    and (def_vault / "Inbox" / "boards" / "t-def.md").is_file()
    and json.loads(tf_path.read_text())["boards"]["t-def"]["path"] == "Inbox/boards/t-def.md"
    and cleanup.pending_marker().is_file(),
    deferred,
)
with patch.object(cleanup.lc, "VAULT", def_vault), \
        patch.object(cleanup, "_running_broker_rejects_boards", return_value=False):
    finished = cleanup.run_pending_migration()
check(
    "migration deferral: post-restart hook completes migration and clears marker",
    finished is not None
    and not finished.get("deferred")
    and (def_vault / "Boards" / "t-def.md").is_file()
    and json.loads(tf_path.read_text())["boards"]["t-def"]["path"] == "Boards/t-def.md"
    and not cleanup.pending_marker().exists(),
    finished,
)

# ---------------------------------------------------------------------------
# 10. Stock README refresh, engineer/schedule migration, schedule & reviewer text
# ---------------------------------------------------------------------------
readme_vault = TMP / "readme-migration-vault"
readme_vault.mkdir(parents=True)
old_nov19_readme = cleanup._legacy_stock_readmes()[1]
(readme_vault / "README.md").write_text(old_nov19_readme, encoding="utf-8")
(readme_vault / "Projects" / "Loam" / "engineer" / "projects").mkdir(parents=True)
(readme_vault / "Projects" / "Loam" / "engineer" / "role.md").write_text("# Role\n", encoding="utf-8")
(readme_vault / "Projects" / "Loam" / "engineer" / "projects" / "loam.md").write_text("# Project\n", encoding="utf-8")
(readme_vault / "Projects" / "Loam" / "infrastructure").mkdir(parents=True)
(readme_vault / "Projects" / "Loam" / "infrastructure" / "schedule.md").write_text("# Schedule\n", encoding="utf-8")

r_res = cleanup.refresh_vault_readme(readme_vault)
e_res = cleanup.migrate_engineer_sources(readme_vault)
s_res = cleanup.migrate_schedule_page(readme_vault)
new_readme = (readme_vault / "README.md").read_text(encoding="utf-8")
check(
    "README refresh: untouched pre-redesign stock README is replaced with plain current README",
    r_res["status"] == "refreshed"
    and "Inbox/boards/general.md" not in new_readme
    and "/etc/loam/loam.toml" not in new_readme
    and "SKILL.md" not in new_readme,
    r_res,
)
(readme_vault / "README.md").write_text("# My own notes\n", encoding="utf-8")
kept_res = cleanup.refresh_vault_readme(readme_vault)
check(
    "README refresh: owner-edited README is never overwritten",
    kept_res["status"] == "kept" and (readme_vault / "README.md").read_text(encoding="utf-8") == "# My own notes\n",
    kept_res,
)
check(
    "migration: stock Projects/Loam/engineer and infrastructure/schedule.md move to System/ and clean empty Loam/",
    len(e_res) == 2 and len(s_res) == 1
    and (readme_vault / "System" / "engineer" / "role.md").is_file()
    and (readme_vault / "System" / "engineer" / "projects" / "loam.md").is_file()
    and (readme_vault / "System" / "schedule.md").is_file()
    and not (readme_vault / "Projects" / "Loam").exists()
    and (readme_vault / "Projects").is_dir(),
    (e_res, s_res),
)

# Vault with sibling project docs (like agent-runtime.md) is left alone.
dev_vault = TMP / "dev-vault"
(dev_vault / "Projects" / "Loam" / "infrastructure").mkdir(parents=True)
(dev_vault / "Projects" / "Loam" / "infrastructure" / "schedule.md").write_text("# Schedule\n", encoding="utf-8")
(dev_vault / "Projects" / "Loam" / "infrastructure" / "agent-runtime.md").write_text("# Runtime\n", encoding="utf-8")
(dev_vault / "Projects" / "Loam" / "engineer").mkdir(parents=True)
(dev_vault / "Projects" / "Loam" / "engineer" / "role.md").write_text("# Role\n", encoding="utf-8")
(dev_vault / "Projects" / "Loam" / "engineer" / "box-memory.md").write_text("# Notes\n", encoding="utf-8")
check(
    "migration: non-empty custom infrastructure/ and engineer/ folders are left untouched",
    cleanup.migrate_schedule_page(dev_vault) == []
    and cleanup.migrate_engineer_sources(dev_vault) == []
    and (dev_vault / "Projects" / "Loam" / "infrastructure" / "schedule.md").is_file()
    and (dev_vault / "Projects" / "Loam" / "engineer" / "role.md").is_file(),
)

# Standard install: loam_profile.engineers() points to System/engineer/projects/loam.md,
# loam_cron.schedule_page() uses configured paths/timezone without dead links,
# and provision's claude_md + reviewer_instructions avoid hardcoded legacy paths.
import importlib.machinery  # noqa: E402
import importlib.util  # noqa: E402
import loam_cron  # noqa: E402
import loam_profile  # noqa: E402

eng_info = loam_profile.engineers()
platform_row = next(r for r in eng_info["engineers"] if r["platform"])
check(
    "profile: platform engineer instructions path uses configured engineer_vault_dir (not legacy numbered path)",
    platform_row["instructions"]["path"] == "System/engineer/projects/loam.md",
    platform_row["instructions"],
)

with patch.object(loam_cron, "AI_LABS", TMP / "agents"):
    sched_text = loam_cron.schedule_page(VAULT, names=[])
check(
    "schedule_page: uses configured zone, workspaces name, STATE events path, and omits dead [[agent-runtime]]",
    f"When ({lc.ZONE_WORD})" in sched_text
    and "`agents/<agent>/crons/`" in sched_text
    and str(lc.STATE / "schedules" / "events.jsonl") in sched_text
    and "[[agent-runtime]]" not in sched_text
    and "agents/state/" not in sched_text
    and "ai-labs/" not in sched_text,
    sched_text[:600],
)

_prov_loader = importlib.machinery.SourceFileLoader("prov_check", str(AGENTS / "bin" / "provision"))
_prov_spec = importlib.util.spec_from_loader(_prov_loader.name, _prov_loader)
prov_mod = importlib.util.module_from_spec(_prov_spec)
_prov_spec.loader.exec_module(prov_mod)
rendered_claude = prov_mod.claude_md("Ada", SPACES / "ada")
check(
    "provision claude_md: provider-neutral Surface and accurate wiki-link behavior",
    "**Surface:** On the server that holds the vault." in rendered_claude
    and "open the note in Files" in rendered_claude
    and "stay as written" not in rendered_claude
    and "**Surface:** Claude Code" not in rendered_claude,
)
with patch.object(prov_mod, "AI_LABS", TMP / "agents"):
    rendered_reviewer = prov_mod.reviewer_instructions()
import re as _re  # noqa: E402
check(
    "provision reviewer_instructions: expands @REVIEWER_STATE@/memory/ and @CONVERSATIONS@ with no legacy leaks",
    f"Under `{lc.REVIEWER_STATE}/memory/`" in rendered_reviewer
    and f"except `{lc.vault_folder('conversations')}/`" in rendered_reviewer
    and not _re.search(r"\b\d\d_[A-Z]+\b", rendered_reviewer)
    and "ai-labs" not in rendered_reviewer
    and "@ROOT@" not in rendered_reviewer
    and "@CONVERSATIONS@" not in rendered_reviewer
    and "@REVIEWER_STATE@" not in rendered_reviewer,
)

passed = sum(results)
total = len(results)
print(f"\n{passed}/{total} passed")
sys.exit(0 if passed == total else 1)
