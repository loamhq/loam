"""Replace a regular text file only while it still has the expected contents."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
from pathlib import Path


class RevisionConflict(Exception):
    pass


def revision(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def replace_text(path: Path, text: str, expected: str) -> None:
    """Stage beside the target, then recheck its contents immediately before replacement."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        raise RevisionConflict("moved or was removed") from None
    if not stat.S_ISREG(mode):
        raise RevisionConflict("is no longer a regular file")

    data = text.encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     delete=False) as handle:
        tmp = Path(handle.name)
    try:
        tmp.write_bytes(data)
        tmp.chmod(stat.S_IMODE(mode))
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ELOOP):
                raise RevisionConflict("moved or was removed") from None
            raise
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RevisionConflict("is no longer a regular file")
            current = source.read()
        if revision(current) != expected:
            raise RevisionConflict("changed since you opened it")
        if current != data:
            os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
