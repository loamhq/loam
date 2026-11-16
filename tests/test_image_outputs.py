"""generate_image writes more files than the boundary hook checks.

hooks/boundary_guard.py sees exactly one path -- ``target_path`` -- but the
tool can write ten (``n``), and it renames around collisions rather than
overwriting. That is only safe because every file it writes lands in
``target_path``'s own directory, which makes the one checked path cover all
of them. That is a property of ``_unique``, so it is asserted here rather
than left as a comment: if a later edit ever makes an output land somewhere
else, the guard would be checking a path the server no longer writes to.

Also pinned: outputs never destroy an existing file, and the collision
suffix cannot smuggle in a character Android refuses.

Run: mcp/.venv/bin/python tests/test_image_outputs.py

NOT bare `python3`: this imports mcp/composition_server.py, which imports the
`mcp` package, which lives only in mcp/.venv -- the interpreter bin/provision
gives the composition server in every agent's .mcp.json. Under `python3` it
dies on `ModuleNotFoundError: No module named 'mcp'` before a single check
runs, which reads like a broken test rather than a wrong interpreter.
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = tempfile.mkdtemp(prefix="imgout-")
os.environ.update({
    "LOAM_AGENT": "tester",
    "LOAM_AGENT_WORKSPACE": f"{ROOT}/ai-labs/tester",
    "LOAM_VAULT": f"{ROOT}/vault",
    "LOAM_AI_LABS": f"{ROOT}/ai-labs",
    "LOAM_IMAGE_LOG": f"{ROOT}/image-gen.jsonl",
})
for d in ("ai-labs/tester", "vault"):
    Path(ROOT, d).mkdir(parents=True, exist_ok=True)

SERVER = os.environ.get(
    "LOAM_COMPOSITION_SERVER",
    str(Path(__file__).resolve().parent.parent / "mcp" / "composition_server.py")
)
spec = importlib.util.spec_from_file_location("cs", SERVER)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ANDROID_ILLEGAL = set('?:*"<>|')
fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    fails += (not cond)
    print(("PASS  " if cond else "FAIL  ") + label + (("  | " + detail) if detail else ""))


work = Path(ROOT, "ai-labs/tester/art")
work.mkdir(parents=True, exist_ok=True)
target = work / "cover.png"

# A free name comes back unchanged.
check("free name is used as-is", m._unique(target) == target)

# Ten outputs, written the way generate_image writes them, all land beside
# the target -- never in a parent, never in a sibling directory.
written = []
for _ in range(10):
    p = m._unique(target)
    p.write_bytes(b"x")
    written.append(p)

check("10 outputs are 10 distinct files", len(set(written)) == 10)
check(
    "every output is in target_path's own directory",
    all(p.parent == target.parent for p in written),
    ", ".join(str(p.parent) for p in written if p.parent != target.parent),
)
check(
    "no output escapes the agent's workspace",
    all(p.resolve().is_relative_to(Path(os.environ["LOAM_AGENT_WORKSPACE"]).resolve()) for p in written),
)
check("first output keeps the requested name", written[0] == target)
check("outputs keep the requested extension", all(p.suffix == ".png" for p in written))
check(
    "collision suffixes are Android-legal",
    all(not (set(p.name) & ANDROID_ILLEGAL) for p in written),
)

# Nothing was overwritten: ten writes, ten files, all still 1 byte.
check("no output overwrote another", all(p.stat().st_size == 1 for p in written))

# And a pre-existing file is stepped over rather than through.
existing = work / "keepme.png"
existing.write_bytes(b"original")
nxt = m._unique(existing)
check("existing file is not the chosen name", nxt != existing)
check("existing file survives untouched", existing.read_bytes() == b"original")
check("step-over stays in the same directory", nxt.parent == existing.parent)

# The write boundary must agree that the derived names are writable too --
# the containment argument, asserted rather than reasoned about.
bad = [p for p in written + [nxt] if m._writable(str(p))[0] is None]
check("every derived name passes _writable", not bad, ", ".join(map(str, bad)))

print()
print("FAILURES: " + str(fails))
sys.exit(1 if fails else 0)
