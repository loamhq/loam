"""Seed missing persona maps during an update."""

from pathlib import Path
from tempfile import TemporaryDirectory

import loam_agents
from loam_loading import MAP, MAP_SEED


POINTERS = (
    ("_always.md", "Read `{name}` at the start of every run."),
    ("_active.md", "Read `{name}` at the start of a conversation."),
    ("_reflection.md", "Read `{name}` at the start of a reflection wake."),
)


def initial_map(workspace: Path) -> str:
    pointers = [words.format(name=name) for name, words in POINTERS
                if (workspace / name).is_file()]
    text = MAP_SEED
    if pointers:
        text += "\n" + "".join(f"- {line}\n" for line in pointers)
    return text


def migrate(workspace: Path) -> bool:
    target = workspace / MAP
    if target.exists() or target.is_symlink():
        return False
    text = initial_map(workspace)
    with TemporaryDirectory(prefix=".map-", dir=workspace) as tmp:
        seed = Path(tmp) / MAP
        seed.write_text(text, encoding="utf-8")
        try:
            target.hardlink_to(seed)
        except FileExistsError:
            return False
    return True


def main() -> None:
    root = loam_agents.ai_labs()
    names = loam_agents.every(root)
    made = [name for name in names if migrate(root / name)]
    print(f"Checked {len(names)} persona maps. Created {len(made)}"
          + (f" ({', '.join(made)})" if made else "") + ".")


if __name__ == "__main__":
    main()
