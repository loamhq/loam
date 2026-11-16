"""The reviewer plugin's nightly run: start the reviewer's lane.

bin/plugin-run has already held this run to the timer's own calendar and to
the plugin being on. The lane is Loam's unit (loam-reviewer.service), which
carries the session's credential, its memory ceiling and its failure
handler; it is started without waiting, so this run ends at once and the
review takes as long as it takes.
"""
import subprocess
import sys

done = subprocess.run(["systemctl", "--user", "start", "--no-block",
                       "loam-reviewer.service"], capture_output=True, text=True)
print(f"reviewer: started loam-reviewer.service (exit {done.returncode})"
      + (f": {done.stderr.strip()}" if done.returncode else ""), file=sys.stderr)
sys.exit(done.returncode)
