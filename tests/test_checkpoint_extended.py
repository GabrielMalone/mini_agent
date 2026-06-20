#!/usr/bin/env python3
"""test_checkpoint_extended.py -- edge case and lifecycle tests for checkpoint system.

Covers gaps the basic test suite doesn't hit:
- Multi-turn lifecycle (checkpoint state across turn resets)
- MAX_CHECKPOINTS pruning
- Deleted-file restoration
- Untracked (new) file restoration
- Non-git fallback at convenience-function level
- reset() classmethod behavior
- last_checkpoint_sha()
- Two risky tools in same turn → single checkpoint
"""

import os
import subprocess
import tempfile
import textwrap
import unittest

from core.checkpoint import (
    Checkpoint,
    CheckpointManager,
    checkpoint_before_risky,
    get_checkpoint_manager,
    reset_turn_checkpoint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_git_repo(path: str) -> None:
    """Initialize a git repo at *path* with an initial commit."""
    subprocess.run(
        ["git", "init", "-q"],
        cwd=path,
        capture_output=True,
        timeout=10,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@mini_agent.test"],
        cwd=path,
        capture_output=True,
        timeout=5,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test Agent"],
        cwd=path,
        capture_output=True,
        timeout=5,
        check=True,
    )
    init_file = os.path.join(path, "README.md")
    with open(init_file, "w") as f:
        f.write("# Test Repo\n")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=path,
        capture_output=True,
        timeout=5,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify", "-q"],
        cwd=path,
        capture_output=True,
        timeout=10,
        check=True,
    )


def _write(path: str, relpath: str, content: str) -> str:
    """Write a file relative to *path* and return the full path."""
    full = os.path.join(path, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    return full


def _read(path: str, relpath: str) -> str:
    """Read a file relative to *path*."""
    with open(os.path.join(path, relpath)) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Edge Case Tests
# ---------------------------------------------------------------------------

class TestMultiTurnLifecycle(unittest.TestCase):
    """Checkpoint state across multiple turns: reset, accumulate, restore."""

    def setUp(self):
        CheckpointManager.reset()
        self.workspace = tempfile.mkdtemp()
        _init_git_repo(self.workspace)
        self.cm = CheckpointManager.get(self.workspace)

    def tearDown(self):
        CheckpointManager.reset()
        import shutil

        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_two_turns_restore_gets_second_turn_state(self):
        """After two checkpointed writes across turns, restore gets turn-2 state."""
        # Turn 1
        _write(self.workspace, "file.txt", "turn1\n")
        sha1 = self.cm.checkpoint("turn-1-edit")
        self.assertIsNotNone(sha1)
        self.assertEqual(self.cm.checkpoint_count(), 1)

        # Reset turn flag (simulates turn boundary)
        self.cm.reset_turn()

        # Turn 2: modify same file
        _write(self.workspace, "file.txt", "turn2\n")
        sha2 = self.cm.checkpoint("turn-2-edit")
        self.assertIsNotNone(sha2)
        self.assertEqual(self.cm.checkpoint_count(), 2)
        self.assertNotEqual(sha1, sha2)

        # Corrupt the file
        _write(self.workspace, "file.txt", "corrupt\n")

        # Restore — should get turn-2 state (most recent checkpoint)
        self.assertTrue(self.cm.restore_file("file.txt"))
        self.assertEqual(_read(self.workspace, "file.txt"), "turn2\n")

    def test_reset_turn_allows_same_file_checkpoint_twice(self):
        """Without reset_turn(), second checkpoint on same file is a no-op."""
        _write(self.workspace, "f.py", "v1\n")
        sha1 = self.cm.checkpoint("first")
        self.assertIsNotNone(sha1)

        # Modify again but DON'T reset turn
        _write(self.workspace, "f.py", "v2\n")
        sha2 = self.cm.checkpoint("second-no-reset")
        self.assertIsNone(sha2, "Second checkpoint in same turn should be no-op")

        # Restore should get v1 (only checkpoint captured)
        self.assertTrue(self.cm.restore_file("f.py"))
        self.assertEqual(_read(self.workspace, "f.py"), "v1\n")

    def test_checkpoint_count_increments_across_turns(self):
        """checkpoint_count() should reflect all checkpoints across turns."""
        for i in range(5):
            _write(self.workspace, f"turn_{i}.py", f"v{i}\n")
            self.cm.checkpoint(f"turn-{i}")
            self.cm.reset_turn()

        self.assertEqual(self.cm.checkpoint_count(), 5)


class TestMaxCheckpointsPruning(unittest.TestCase):
    """MAX_CHECKPOINTS (50) log-ring behaviour."""

    def test_prunes_oldest_when_exceeding_max(self):
        CheckpointManager.reset()
        workspace = tempfile.mkdtemp()
        _init_git_repo(workspace)
        cm = CheckpointManager.get(workspace)

        # Create just enough to hit the cap + 10 extra
        for i in range(60):
            _write(workspace, f"f{i}.py", f"v{i}\n")
            cm.checkpoint(f"cp-{i}")
            cm.reset_turn()

        # Only 50 should be retained in the log ring
        self.assertLessEqual(cm.checkpoint_count(), 50)

        # list_checkpoints() returns the last 20; verify newest entries present
        cps = cm.list_checkpoints()
        self.assertGreaterEqual(len(cps), 1)
        # Oldest items (cp-0 through cp-9) should have been pruned
        visible_messages = {cp["message"] for cp in cps}
        self.assertNotIn("cp-0", visible_messages,
                         "cp-0 should have been pruned from the ring")
        # Newest should be present
        self.assertIn("cp-59", visible_messages,
                      "cp-59 (newest) should still be in the ring")

        CheckpointManager.reset()
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)


class TestDeletedFileRestore(unittest.TestCase):
    """Checkpoint captures deleted-file state; restore brings it back."""

    def setUp(self):
        CheckpointManager.reset()
        self.workspace = tempfile.mkdtemp()
        _init_git_repo(self.workspace)
        self.cm = CheckpointManager.get(self.workspace)

    def tearDown(self):
        CheckpointManager.reset()
        import shutil

        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_restore_deleted_file(self):
        """File deleted after checkpoint should be recoverable."""
        full = _write(self.workspace, "important.py", "def foo(): pass\n")
        self.cm.checkpoint("pre-delete")

        # Delete the file
        os.unlink(full)
        self.assertFalse(os.path.exists(full))

        # Restore
        self.assertTrue(self.cm.restore_file("important.py"))
        self.assertTrue(os.path.exists(full))
        self.assertEqual(_read(self.workspace, "important.py"), "def foo(): pass\n")

    def test_deleted_and_recreated_restore_gets_checkpoint(self):
        """Delete file, create new one with same name, restore gets old content."""
        _write(self.workspace, "config.cfg", "port = 8080\n")
        self.cm.checkpoint("pre-recreate")

        os.unlink(os.path.join(self.workspace, "config.cfg"))
        _write(self.workspace, "config.cfg", "port = 9999\n")

        self.assertTrue(self.cm.restore_file("config.cfg"))
        self.assertEqual(_read(self.workspace, "config.cfg"), "port = 8080\n")


class TestUntrackedFileBehaviour(unittest.TestCase):
    """Untracked (brand-new) files and checkpoint interaction."""

    def setUp(self):
        CheckpointManager.reset()
        self.workspace = tempfile.mkdtemp()
        _init_git_repo(self.workspace)
        self.cm = CheckpointManager.get(self.workspace)

    def tearDown(self):
        CheckpointManager.reset()
        import shutil

        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_new_untracked_file_captured_by_checkpoint(self):
        """Untracked file (git add -A) is committed by checkpoint."""
        _write(self.workspace, "new_file.py", "new\n")
        sha = self.cm.checkpoint("capture-new")
        self.assertIsNotNone(sha, "Untracked file should be committed by git add -A")

        # Corrupt
        _write(self.workspace, "new_file.py", "corrupt\n")

        self.assertTrue(self.cm.restore_file("new_file.py"))
        self.assertEqual(_read(self.workspace, "new_file.py"), "new\n")

    def test_new_file_created_after_checkpoint_is_removed_by_restore_all(self):
        """restore_all() cleans files created after the checkpoint."""
        _write(self.workspace, "tracked.py", "tracked\n")
        self.cm.checkpoint("pre-new-files")

        # Create new file after checkpoint
        new_path = _write(self.workspace, "untracked.py", "untracked\n")
        self.assertTrue(os.path.exists(new_path))

        self.assertTrue(self.cm.restore_all())
        self.assertFalse(
            os.path.exists(new_path),
            "untracked.py should be removed by git clean -fd",
        )
        self.assertEqual(_read(self.workspace, "tracked.py"), "tracked\n")


class TestConvenienceFunctionEdgeCases(unittest.TestCase):
    """checkpoint_before_risky and reset_turn_checkpoint edge cases."""

    def setUp(self):
        CheckpointManager.reset()

    def tearDown(self):
        CheckpointManager.reset()

    def test_checkpoint_before_risky_no_git_returns_none(self):
        """In non-git directory, checkpoint_before_risky returns None silently."""
        with tempfile.TemporaryDirectory() as tmp:
            result = checkpoint_before_risky(tmp, "pre-op")
            self.assertIsNone(result)

    def test_checkpoint_before_risky_clean_tree_returns_none(self):
        """Clean tree → no-op → returns None."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            result = checkpoint_before_risky(tmp, "clean-op")
            self.assertIsNone(result, "Clean tree should not create a checkpoint")

    def test_reset_turn_checkpoint_no_git_does_not_crash(self):
        """reset_turn_checkpoint in non-git dir should not raise."""
        with tempfile.TemporaryDirectory() as tmp:
            try:
                reset_turn_checkpoint(tmp)
            except Exception as exc:
                self.fail(f"reset_turn_checkpoint raised {exc} in non-git dir")

    def test_checkpoint_before_risky_nonexistent_path(self):
        """checkpoint_before_risky with non-existent workspace returns None."""
        result = checkpoint_before_risky("/nonexistent/path/for/test", "op")
        self.assertIsNone(result)


class TestPathNormalization(unittest.TestCase):
    """Absolute vs relative paths and realpath() normalization."""

    def setUp(self):
        CheckpointManager.reset()
        self.workspace = tempfile.mkdtemp()
        _init_git_repo(self.workspace)
        self.cm = CheckpointManager.get(self.workspace)

    def tearDown(self):
        CheckpointManager.reset()
        import shutil

        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_restore_file_with_relative_path(self):
        """restore_file should accept relative paths."""
        _write(self.workspace, "sub/dir/file.txt", "hello\n")
        self.cm.checkpoint("relative-path-test")

        _write(self.workspace, "sub/dir/file.txt", "bad\n")

        # Use relative path
        self.assertTrue(self.cm.restore_file("sub/dir/file.txt"))
        self.assertEqual(_read(self.workspace, "sub/dir/file.txt"), "hello\n")

    def test_restore_file_with_absolute_path(self):
        """restore_file should accept absolute paths."""
        full = _write(self.workspace, "abs_test.py", "original\n")
        self.cm.checkpoint("abs-path-test")

        _write(self.workspace, "abs_test.py", "modified\n")

        self.assertTrue(self.cm.restore_file(full))
        self.assertEqual(_read(self.workspace, "abs_test.py"), "original\n")

    def test_same_singleton_for_different_path_forms(self):
        """Dot, relative, and absolute forms should yield the same manager."""
        # Navigate into the workspace and use '.' or relpath
        cwd = os.getcwd()
        try:
            os.chdir(self.workspace)
            cm1 = CheckpointManager.get(".")
            cm2 = CheckpointManager.get(self.workspace)
            self.assertIs(cm1, cm2, "Different path forms should share singleton")
        finally:
            os.chdir(cwd)


class TestLastCheckpointSha(unittest.TestCase):
    """last_checkpoint_sha() behaviour."""

    def setUp(self):
        CheckpointManager.reset()
        self.workspace = tempfile.mkdtemp()
        _init_git_repo(self.workspace)
        self.cm = CheckpointManager.get(self.workspace)

    def tearDown(self):
        CheckpointManager.reset()
        import shutil

        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_last_checkpoint_sha_is_none_initially(self):
        self.assertIsNone(self.cm.last_checkpoint_sha())

    def test_last_checkpoint_sha_matches_most_recent(self):
        _write(self.workspace, "f1.py", "v1\n")
        sha1 = self.cm.checkpoint("first")
        self.assertEqual(self.cm.last_checkpoint_sha(), sha1)

        self.cm.reset_turn()
        _write(self.workspace, "f2.py", "v2\n")
        sha2 = self.cm.checkpoint("second")
        self.assertEqual(self.cm.last_checkpoint_sha(), sha2)
        self.assertNotEqual(sha1, sha2)


class TestResetClassMethod(unittest.TestCase):
    """reset() classmethod tears down all singleton instances."""

    def tearDown(self):
        CheckpointManager.reset()

    def test_reset_clears_all_instances(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            _init_git_repo(d1)
            _init_git_repo(d2)

            cm1 = CheckpointManager.get(d1)
            cm2 = CheckpointManager.get(d2)

            self.assertEqual(len(CheckpointManager._instances), 2)

            CheckpointManager.reset()

            self.assertEqual(len(CheckpointManager._instances), 0)

            # New get() after reset creates fresh instances
            cm3 = CheckpointManager.get(d1)
            self.assertIsNot(cm1, cm3)


class TestEditFileCheckpointIntegration(unittest.TestCase):
    """Integration: execute_tool edit_file triggers checkpoint exactly once."""

    def setUp(self):
        CheckpointManager.reset()
        self.workspace = tempfile.mkdtemp()
        _init_git_repo(self.workspace)

        from conftest import make_gates

        self.write_gate, self.read_gate = make_gates(self.workspace)

    def tearDown(self):
        CheckpointManager.reset()
        import shutil

        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_edit_file_triggers_checkpoint(self):
        """edit_file should create a checkpoint when tree is dirty."""
        from tools import execute_tool

        cm = get_checkpoint_manager(self.workspace)
        self.assertEqual(cm.checkpoint_count(), 0)

        # Pre-dirty the tree so the checkpoint has something to commit
        _write(self.workspace, "hello.py", "print('hello')\n")

        # Must read before edit (read-before-edit guard)
        execute_tool(
            {
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "hello.py"}',
                }
            },
            self.write_gate,
            self.read_gate,
        )

        # edit_file should trigger checkpoint before dispatch
        result = execute_tool(
            {
                "function": {
                    "name": "edit_file",
                    "arguments": (
                        '{"path": "hello.py", "old_string": "print(\'hello\')", '
                        '"new_string": "print(\'world\')"}'
                    ),
                }
            },
            self.write_gate,
            self.read_gate,
        )
        self.assertTrue(result.success)

        # Checkpoint fires before dispatch via checkpoint_before_risky
        self.assertEqual(cm.checkpoint_count(), 1)

    def test_two_writes_same_turn_no_auto_checkpoint(self):
        """Two write_file calls in same turn → no auto-checkpoints created."""
        from tools import execute_tool

        cm = get_checkpoint_manager(self.workspace)

        # Pre-dirty so first checkpoint has something to commit
        _write(self.workspace, "a.py", "a\n")

        # First write
        execute_tool(
            {
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "a.py"}',
                }
            },
            self.write_gate,
            self.read_gate,
        )
        r1 = execute_tool(
            {
                "function": {
                    "name": "write_file",
                    "arguments": '{"path": "a.py", "content": "a_prime\\n"}',
                }
            },
            self.write_gate,
            self.read_gate,
        )
        self.assertTrue(r1.success)
        self.assertEqual(cm.checkpoint_count(), 1, "checkpoint fires before dispatch")

        # Second write in same turn — NO new checkpoint (per-turn gating)
        execute_tool(
            {
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "a.py"}',
                }
            },
            self.write_gate,
            self.read_gate,
        )
        r2 = execute_tool(
            {
                "function": {
                    "name": "write_file",
                    "arguments": '{"path": "a.py", "content": "a_double\\n"}',
                }
            },
            self.write_gate,
            self.read_gate,
        )
        self.assertTrue(r2.success)
        self.assertEqual(cm.checkpoint_count(), 1, "Per-turn gating: still 1 checkpoint after second write")


# ---------------------------------------------------------------------------
# Debug/listing edge cases
# ---------------------------------------------------------------------------

class TestListCheckpointsEdgeCases(unittest.TestCase):
    """list_checkpoints edge cases."""

    def setUp(self):
        CheckpointManager.reset()
        self.workspace = tempfile.mkdtemp()
        _init_git_repo(self.workspace)
        self.cm = CheckpointManager.get(self.workspace)

    def tearDown(self):
        CheckpointManager.reset()
        import shutil

        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_list_checkpoints_empty_initially(self):
        """No checkpoints → empty list."""
        self.assertEqual(self.cm.list_checkpoints(), [])

    def test_list_checkpoints_newest_last(self):
        """Most recent checkpoint should be at end of list."""
        _write(self.workspace, "f1.py", "v1\n")
        self.cm.checkpoint("early")
        self.cm.reset_turn()
        _write(self.workspace, "f2.py", "v2\n")
        self.cm.checkpoint("late")

        cps = self.cm.list_checkpoints()
        self.assertGreaterEqual(len(cps), 2)
        self.assertEqual(cps[-1]["message"], "late")

    def test_checkpoint_count_empty(self):
        """checkpoint_count returns 0 initially."""
        self.assertEqual(self.cm.checkpoint_count(), 0)


if __name__ == "__main__":
    unittest.main()
