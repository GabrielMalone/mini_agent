#!/usr/bin/env python3
"""
test_git.py — tests for the git tool.
"""

import json
import os
import subprocess
import tempfile
import unittest

from safety import ReadSafetyGate, WriteSafetyGate
from tools import execute_tool


def _make_tool_call(name: str, **kwargs) -> dict:
    return {
        "id": "call_test",
        "function": {
            "name": name,
            "arguments": json.dumps(kwargs),
        },
    }


def _gates(workspace: str) -> tuple[WriteSafetyGate, ReadSafetyGate]:
    return WriteSafetyGate(workspace, allow_overwrites=True), ReadSafetyGate(workspace)


class TestGitTool(unittest.TestCase):

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)
        # Initialize a git repo
        subprocess.run(["git", "init"], cwd=self.workspace,
                       capture_output=True)
        # Set identity for commits
        subprocess.run(["git", "config", "user.email", "test@test"],
                       cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=self.workspace, capture_output=True)
        # Create a file to get past "nothing to commit"
        self._write("readme.md", "# test")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _write(self, relpath: str, content: str) -> str:
        full = os.path.join(self.workspace, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return full

    def _git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.workspace,
                       capture_output=True)

    # --- status ---

    def test_status_clean(self):
        self._git("add", "readme.md")
        self._git("commit", "-m", "init")
        tc = _make_tool_call("git", subcommand="status")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("clean", result.content)

    def test_status_dirty(self):
        self._git("add", "readme.md")
        self._git("commit", "-m", "init")
        self._write("readme.md", "# modified")
        tc = _make_tool_call("git", subcommand="status")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("M ", result.content)  # modified

    def test_status_untracked(self):
        self._git("add", "readme.md")
        self._git("commit", "-m", "init")
        self._write("new.txt", "hello")
        tc = _make_tool_call("git", subcommand="status")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("?? ", result.content)  # untracked

    # --- diff ---

    def test_diff_no_changes(self):
        self._git("add", "readme.md")
        self._git("commit", "-m", "init")
        tc = _make_tool_call("git", subcommand="diff")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("No unstaged changes", result.content)

    def test_diff_shows_changes(self):
        self._git("add", "readme.md")
        self._git("commit", "-m", "init")
        self._write("readme.md", "# changed\nextra line\n")
        tc = _make_tool_call("git", subcommand="diff")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("+# changed", result.content)

    # --- log ---

    def test_log_no_commits(self):
        # repo has no commits yet (file not staged/committed)
        tc = _make_tool_call("git", subcommand="log")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("No commits", result.content)

    def test_log_with_commits(self):
        self._git("add", "readme.md")
        self._git("commit", "-m", "first")
        tc = _make_tool_call("git", subcommand="log")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("first", result.content)

    # --- init ---

    def test_init_reports_ok(self):
        # Already initialized, so it should still succeed
        tc = _make_tool_call("git", subcommand="init")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)

    # --- add ---

    def test_add_stages_file(self):
        self._write("staged.txt", "data")
        tc = _make_tool_call("git", subcommand="add", args="staged.txt")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        # Verify file is staged
        check = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=self.workspace, capture_output=True, text=True,
        )
        self.assertIn("staged.txt", check.stdout)

    # --- commit ---

    def test_commit_requires_message(self):
        tc = _make_tool_call("git", subcommand="commit")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)

    def test_commit_works(self):
        self._git("add", "readme.md")
        tc = _make_tool_call("git", subcommand="commit", args="test commit")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)

    # --- safety ---

    def test_unsafe_subcommand_blocked(self):
        tc = _make_tool_call("git", subcommand="push")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("unsafe", result.content.lower())


if __name__ == "__main__":
    unittest.main()
