"""Exercise a contributed file across the thread lifecycle in a scratch installation."""

import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import loam_threads as threads
import loam_thread_files as files
import loam_contributions as contributions


class ThreadFileTest(TestCase):
    def test_lifecycle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            from unittest.mock import patch
            with patch.multiple(threads, VAULT=root / "vault", STATE=root / "state",
                                AI_LABS=root / "people"), patch.object(threads, "roster", return_value=[]):
                self.exercise()

    def exercise(self):
        entries = contributions.contributes_of({"thread.file": [{"label": "checklist",
            "suffix": " — steps", "initial": "# {title}"}]}, None, [])
        install = [{"id": "checklists", "contributes": entries}]
        files.configure(install)
        a = threads.create(title="Trip", thread_id="t-first")
        b = threads.create(title="Trip", thread_id="t-second")
        inbox = threads.lc.vault_folder("inbox")
        legacy = f"{inbox}/checklists/Trip — steps.md"
        files.adopt("checklists", [(b["id"], b["title"], legacy), (a["id"], a["title"], legacy)])
        first = files.locations(a)["checklists"]["path"]
        second = files.locations(b)["checklists"]["path"]
        self.assertNotEqual(first, second)
        self.assertFalse(Path(first).exists())
        self.assertFalse(Path(second).exists())
        snapshot = files.document("checklists", a["id"])
        raw = b"# Trip\r\n- one\r\n"
        Path(first).write_bytes(raw)
        with self.assertRaises(threads.ThreadError) as fail:
            files.document("checklists", a["id"], {"base_revision": snapshot["revision"], "markdown": "older"})
        self.assertEqual(fail.exception.status, 409)
        snap = files.document("checklists", a["id"])
        before = Path(first).stat()
        files.document("checklists", a["id"], {**snap, "base_revision": snap["revision"]})
        self.assertEqual(Path(first).stat().st_mtime_ns, before.st_mtime_ns)
        meta = threads.update(a["id"], title="Holiday")
        moved = Path(files.locations(meta)["checklists"]["path"])
        self.assertFalse(Path(first).exists())
        self.assertEqual(moved.read_bytes(), raw)
        self.assertIn(str(moved), threads.turn_prompt(meta, "tester", [], True))
        files.configure([])
        self.assertNotIn(str(moved), threads.turn_prompt(meta, "tester", [], True))
        self.assertTrue(moved.exists())
        with self.assertRaises(threads.ThreadError) as fail:
            files.document("checklists", a["id"])
        self.assertEqual(fail.exception.status, 404)
        files.configure(install)
        files.document("checklists", b["id"])
        threads.delete(a["id"])
        self.assertTrue(Path(second).exists())
        self.assertFalse(moved.exists())


if __name__ == "__main__":
    main()
