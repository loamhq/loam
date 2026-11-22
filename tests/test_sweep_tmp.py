"""Exercise sweep-tmp's narrow sudo fallback for ended job scratch."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

SWEEP = Path(__file__).resolve().parents[1] / "bin" / "sweep-tmp"


def _load_sweep():
    loader = importlib.machinery.SourceFileLoader("sweep_tmp", str(SWEEP))
    spec = importlib.util.spec_from_loader("sweep_tmp", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test() -> None:
    mod = _load_sweep()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        job_dir = root / "loam-job-20261119-085028-b1d801a3"
        job_dir.mkdir()
        other_dir = root / "other-scratch"
        other_dir.mkdir()
        link = root / "loam-job-20261119-105033-30141d4e"
        link.symlink_to(other_dir)

        assert mod.is_job_scratch(str(job_dir), root=root)
        assert not mod.is_job_scratch(str(other_dir), root=root)
        assert not mod.is_job_scratch(str(link), root=root)
        assert not mod.is_job_scratch(str(job_dir), root=Path("/tmp"))

        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "rm":
                return subprocess.CompletedProcess(cmd, 1)
            return subprocess.CompletedProcess(cmd, 0)

        # Fallback triggers only for job scratch under ROOT when allow_sudo=True.
        with mock.patch.object(mod, "ROOT", root), mock.patch.object(
            mod.subprocess, "run", side_effect=fake_run
        ):
            assert mod.remove(str(job_dir), allow_sudo=True) is True
            assert calls == [
                ["rm", "-rf", "--one-file-system", "--", str(job_dir)],
                ["sudo", "-n", "rm", "-rf", "--one-file-system", "--", str(job_dir)],
            ]

            calls.clear()
            assert mod.remove(str(job_dir), allow_sudo=False) is False
            assert calls == [
                ["rm", "-rf", "--one-file-system", "--", str(job_dir)],
            ]

            calls.clear()
            assert mod.remove(str(other_dir), allow_sudo=True) is False
            assert calls == [
                ["rm", "-rf", "--one-file-system", "--", str(other_dir)],
            ]

            calls.clear()
            assert mod.remove(str(link), allow_sudo=True) is False
            assert calls == [
                ["rm", "-rf", "--one-file-system", "--", str(link)],
            ]

    print("PASS sweep-tmp sudo fallback is limited to non-symlink job scratch")


if __name__ == "__main__":
    test()
