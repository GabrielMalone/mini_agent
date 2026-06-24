"""Tests for core/file_context_tracker.py."""

from __future__ import annotations

import os
import time
import tempfile
import unittest

from core.file_context_tracker import (
    FileContextTracker,
    get_tracker,
    remove_tracker,
)


class TestFileContextTracker(unittest.TestCase):
    """Test per-task file context tracking."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.tmpdir, "test.py")
        with open(self.test_file, "w") as f:
            f.write("x = 1\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_mark_file_read_sets_mtime(self):
        tracker = FileContextTracker()
        tracker.mark_file_read(self.test_file)
        self.assertIn(os.path.realpath(self.test_file), tracker._read_paths)
        self.assertIn(os.path.realpath(self.test_file), tracker._read_mtimes)

    def test_is_stale_returns_false_when_never_read(self):
        tracker = FileContextTracker()
        self.assertFalse(tracker.is_stale(self.test_file))

    def test_is_stale_returns_false_when_unchanged(self):
        tracker = FileContextTracker()
        tracker.mark_file_read(self.test_file)
        self.assertFalse(tracker.is_stale(self.test_file))

    def test_is_stale_returns_true_when_modified_externally(self):
        tracker = FileContextTracker()
        tracker.mark_file_read(self.test_file)
        time.sleep(0.02)
        with open(self.test_file, "w") as f:
            f.write("x = 2\n")
        self.assertTrue(tracker.is_stale(self.test_file))

    def test_get_stale_warning_returns_message(self):
        tracker = FileContextTracker()
        tracker.mark_file_read(self.test_file)
        time.sleep(0.02)
        with open(self.test_file, "w") as f:
            f.write("x = 2\n")
        warning = tracker.get_stale_warning(self.test_file)
        self.assertIsNotNone(warning)
        self.assertIn("modified externally", warning)

    def test_get_stale_warning_only_warns_once(self):
        tracker = FileContextTracker()
        tracker.mark_file_read(self.test_file)
        time.sleep(0.02)
        with open(self.test_file, "w") as f:
            f.write("x = 2\n")
        first = tracker.get_stale_warning(self.test_file)
        self.assertIsNotNone(first)
        second = tracker.get_stale_warning(self.test_file)
        self.assertIsNone(second)

    def test_mark_file_edited_clears_staleness(self):
        tracker = FileContextTracker()
        tracker.mark_file_read(self.test_file)
        time.sleep(0.02)
        with open(self.test_file, "w") as f:
            f.write("x = 2\n")
        self.assertTrue(tracker.is_stale(self.test_file))
        tracker.mark_file_edited(self.test_file)
        self.assertFalse(tracker.is_stale(self.test_file))

    def test_clear_state_removes_tracking(self):
        tracker = FileContextTracker()
        tracker.mark_file_read(self.test_file)
        tracker.clear_state(self.test_file)
        self.assertNotIn(os.path.realpath(self.test_file), tracker._read_paths)

    def test_clear_all_removes_everything(self):
        tracker = FileContextTracker()
        f2 = os.path.join(self.tmpdir, "other.py")
        with open(f2, "w") as f:
            f.write("y = 2\n")
        tracker.mark_file_read(self.test_file)
        tracker.mark_file_read(f2)
        tracker.clear_all()
        self.assertEqual(len(tracker._read_paths), 0)
        self.assertEqual(len(tracker._read_mtimes), 0)
        self.assertEqual(len(tracker._edit_mtimes), 0)
        self.assertEqual(len(tracker._stale_warnings), 0)

    def test_nonexistent_file_mark_read_does_not_crash(self):
        tracker = FileContextTracker()
        tracker.mark_file_read("/nonexistent/path/file.py")
        # Should not raise

    def test_nonexistent_file_is_stale_returns_false(self):
        tracker = FileContextTracker()
        self.assertFalse(tracker.is_stale("/nonexistent/path/file.py"))


class TestGetTracker(unittest.TestCase):
    """Test module-level tracker singleton."""

    def tearDown(self):
        remove_tracker("test_task")
        remove_tracker("")

    def test_get_tracker_returns_same_instance(self):
        t1 = get_tracker("test_task")
        t2 = get_tracker("test_task")
        self.assertIs(t1, t2)

    def test_get_tracker_different_tasks(self):
        t1 = get_tracker("task_a")
        t2 = get_tracker("task_b")
        self.assertIsNot(t1, t2)

    def test_default_tracker(self):
        t = get_tracker()
        self.assertIsInstance(t, FileContextTracker)

    def test_remove_tracker(self):
        get_tracker("to_remove")
        remove_tracker("to_remove")
        t = get_tracker("to_remove")
        self.assertIsInstance(t, FileContextTracker)


if __name__ == "__main__":
    unittest.main()
