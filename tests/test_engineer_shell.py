#!/usr/bin/env python3
"""Exercise the actual shell adapter with short foreground wait windows."""
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ADAPTER = Path(__file__).resolve().parents[1] / "bin" / "engineer-bash"
SHIM_DIR = Path(__file__).resolve().parents[1] / "bin" / "engineer-path"


class ShellTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scratch = Path(tmp.name)
        self.env = {**os.environ, "LOAM_COMMAND_SCRATCH": tmp.name}

    def start(self, script, seconds=0.3, stream=False, extra_env=None):
        env = {**self.env, **(extra_env or {})}
        return subprocess.run([sys.executable, "-c",
            "import runpy,sys; from pathlib import Path; "
            "f=runpy.run_path(sys.argv[1]); "
            "sys.exit(f['start'](['/bin/bash','-c',sys.argv[3]],"
            "Path(sys.argv[2]),float(sys.argv[4]),stream=(sys.argv[5]=='1')))",
            str(ADAPTER), str(self.scratch), script, str(seconds),
            "1" if stream else "0"],
            env=env, capture_output=True, timeout=10)

    def control(self, action, handle, *rest):
        return subprocess.run([ADAPTER, action, handle, *rest], env=self.env,
                              capture_output=True, timeout=10)

    def handle(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return re.search(rb"Handle (command-[\w-]+)\.", result.stdout)[1].decode()

    def test_short_preserves_streams_and_exit(self):
        result = self.start("printf 'a\\000b'; printf err >&2; exit 17", 3)
        self.assertEqual((result.stdout, result.stderr, result.returncode),
                         (b"a\0b", b"err", 17))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_hook_updates_only_an_early_native_deadline(self):
        original = {"command": "sleep 300", "timeout": 1000, "description": "wait"}
        result = subprocess.run([ADAPTER, "--tool-timeout"],
                                input=json.dumps({"tool_input": original}),
                                capture_output=True, text=True, check=True)
        change = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", change)
        self.assertEqual(change["updatedInput"], {**original, "timeout": 660000})

    def test_quiet_command_can_be_stopped(self):
        handle = self.handle(self.start("sleep 90"))
        self.assertIn(b"Still running", self.control("status", handle).stdout)
        self.assertIn(b"Requested stop", self.control("stop", handle).stdout)
        self.assertEqual(self.control("wait", handle).returncode, 143)
        self.assertEqual(self.control("stop", handle).returncode, 143)

    def test_stop_and_force_kill_cover_shell_job_control_groups(self):
        pidfile = self.scratch / "server.pid"
        # The foreground shell can exit on TERM while a process in a
        # separate job-control group ignores it. Keep the same handle.
        script = ("set -m; bash -c " + shlex.quote(
            f"trap '' TERM; echo $$ > {shlex.quote(str(pidfile))}; exec sleep 30")
            + " & wait")
        handle = self.handle(self.start(script))
        pid = int(pidfile.read_text())
        self.assertNotEqual(os.getpgid(pid), os.getsid(pid))
        try:
            self.control("stop", handle)
            self.assertIn(b"Still running", self.control("status", handle).stdout)
            self.control("kill", handle)
            self.assertIn(b"exited", self.control("wait", handle).stdout)
            stat = Path(f"/proc/{pid}/stat")
            if stat.exists():
                self.assertEqual(stat.read_bytes().rsplit(b")", 1)[1].split()[0], b"Z")
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_build_finishes_and_keeps_full_output(self):
        result = self.start("head -c 100000 /dev/zero; echo ready; sleep 1; echo done; exit 9")
        handle = self.handle(result)
        self.assertIn(b"ready", result.stdout)
        self.assertLess(len(result.stdout), 4000)
        self.assertEqual(self.control("wait", handle).returncode, 9)
        self.assertEqual((self.scratch / handle / "stdout").read_bytes(),
                         bytes(100000) + b"ready\ndone\n")

    def test_descendant_inheriting_output_does_not_hold_tool_open(self):
        pidfile = self.scratch / "child.pid"
        try:
            result = self.start(f"sleep 30 & echo $! > {shlex.quote(str(pidfile))}; echo launched", 2)
            self.assertEqual(result.stdout, b"launched\n")
            os.kill(int(pidfile.read_text()), 0)
        finally:
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def test_stream_short_preserves_streams_and_exit(self):
        result = self.start("printf 'a\\000b'; printf err >&2; exit 17", 3, stream=True)
        self.assertEqual((result.stdout, result.stderr, result.returncode),
                         (b"a\0b", b"err", 17))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_stream_handoff_keeps_tail_handle_and_full_output(self):
        result = self.start("echo ready; sleep 0.8; echo done; exit 5", 0.3, stream=True)
        handle = self.handle(result)
        self.assertIn(b"ready\n", result.stdout)
        self.assertTrue(result.stdout.index(b"ready\n") < result.stdout.index(b"Still running."))
        waited = self.control("wait", handle, "5")
        self.assertEqual(waited.returncode, 5)
        self.assertEqual((self.scratch / handle / "stdout").read_bytes(), b"ready\ndone\n")

    def test_path_shim_strips_itself_for_nested_bash(self):
        shim_env = {
            **self.env,
            "PATH": f"{SHIM_DIR}{os.pathsep}{os.environ.get('PATH', '')}",
            "LOAM_WALL_PATH": f"{SHIM_DIR}{os.pathsep}/usr/bin:/bin",
            "LOAM_COMMAND_PATH_SHIM": str(SHIM_DIR),
            "LOAM_COMMAND_STREAM": "1",
        }
        result = subprocess.run(
            [str(SHIM_DIR / "bash"), "-c", "command -v bash; printf '%s\\n' \"$LOAM_WALL_PATH\"; bash -c 'echo nested'"],
            env=shim_env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertNotIn(str(SHIM_DIR), lines[0])
        self.assertEqual(lines[1], "/usr/bin:/bin")
        self.assertEqual(lines[2], "nested")

    def test_early_caller_kill_terminates_worker_session(self):
        pidfile = self.scratch / "early.pid"
        proc = subprocess.Popen([
            sys.executable, "-c",
            "import runpy,sys; from pathlib import Path; "
            "f=runpy.run_path(sys.argv[1]); "
            "sys.exit(f['start'](['/bin/bash','-c',sys.argv[3]], Path(sys.argv[2]), 30, stream=True))",
            str(ADAPTER), str(self.scratch),
            f"echo $$ > {shlex.quote(str(pidfile))}; exec sleep 60",
        ], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for _ in range(40):
                if pidfile.exists() and pidfile.read_text().strip():
                    break
                import time
                time.sleep(0.05)
            pid = int(pidfile.read_text().strip())
            proc.kill()
            proc.wait(timeout=5)
            import time
            for _ in range(40):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass


if __name__ == "__main__":
    unittest.main()
