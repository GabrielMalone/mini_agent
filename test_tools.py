#!/usr/bin/env python3
"""
test_tools.py — tests for tool implementations and tool_summary display.
"""

import json
import os
import tempfile
import unittest

from safety import ReadSafetyGate, WriteSafetyGate
from tools import ToolResult, execute_tool, tool_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# run_shell tests
# ---------------------------------------------------------------------------

class TestRunShell(unittest.TestCase):

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_simple_command_succeeds(self):
        tc = _make_tool_call("run_shell", command="echo hello")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("hello", result.content)
        self.assertIn("exit_code=0", result.content)

    def test_failing_command_returns_failure(self):
        tc = _make_tool_call("run_shell", command="exit 1")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("exit_code=1", result.content)

    def test_stderr_is_captured(self):
        tc = _make_tool_call("run_shell", command="echo err >&2")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("stderr:", result.content)
        self.assertIn("err", result.content)

    def test_stdout_and_stderr_both_captured(self):
        tc = _make_tool_call("run_shell", command="echo out && echo err >&2")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("out", result.content)
        self.assertIn("err", result.content)

    def test_returns_tool_result_not_exception(self):
        tc = _make_tool_call("run_shell", command="nonexistent_command_xyz")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertIsInstance(result, ToolResult)

    def test_runs_in_workspace_directory(self):
        tc = _make_tool_call("run_shell", command="pwd")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        resolved = os.path.realpath(self.workspace)
        self.assertIn(resolved, result.content)


# ---------------------------------------------------------------------------
# search_files tests
# ---------------------------------------------------------------------------

class TestSearchFiles(unittest.TestCase):

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)
        self._write("a.txt", "hello world\nfoo bar\n")
        self._write("b.txt", "hello again\nbaz qux\n")
        os.makedirs(os.path.join(self.workspace, "sub"))
        self._write(os.path.join("sub", "c.txt"), "nested hello\n")
        os.makedirs(os.path.join(self.workspace, ".hidden"))
        self._write(os.path.join(".hidden", "d.txt"), "hidden hello\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _write(self, relpath: str, content: str) -> str:
        full = os.path.join(self.workspace, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return full

    def test_finds_matches(self):
        tc = _make_tool_call("search_files", pattern="hello", path=self.workspace)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("a.txt:1:", result.content)
        self.assertIn("b.txt:1:", result.content)
        self.assertIn("c.txt:1:", result.content)

    def test_no_matches(self):
        tc = _make_tool_call("search_files", pattern="zzznonexistent", path=self.workspace)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("No matches", result.content)

    def test_match_includes_line_number(self):
        tc = _make_tool_call("search_files", pattern="bar", path=self.workspace)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("a.txt:2:", result.content)

    def test_skips_hidden_directories(self):
        tc = _make_tool_call("search_files", pattern="hidden", path=self.workspace)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("No matches", result.content)

    def test_blocked_outside_workspace(self):
        outside = tempfile.mkdtemp()
        try:
            tc = _make_tool_call("search_files", pattern="x", path=outside)
            result = execute_tool(tc, self.write_gate, self.read_gate)
            self.assertFalse(result.success)
            self.assertIn("blocked by safety layer", result.content)
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)

    def test_capped_at_50_results(self):
        lines = "\n".join(f"match_{i}" for i in range(60))
        self._write("big.txt", lines)
        tc = _make_tool_call("search_files", pattern="match_", path=self.workspace)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        match_lines = [l for l in result.content.split("\n") if "big.txt:" in l]
        self.assertEqual(len(match_lines), 50)
        self.assertIn("capped at 50", result.content)

    def test_default_path_is_dot(self):
        tc = _make_tool_call("search_files", pattern="hello")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertIsInstance(result, ToolResult)

    def test_unknown_tool_returns_failure(self):
        tc = _make_tool_call("no_such_tool", x="y")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("Unknown tool", result.content)


# ---------------------------------------------------------------------------
# edit_file tests
# ---------------------------------------------------------------------------

class TestEditFile(unittest.TestCase):

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _write(self, relpath: str, content: str) -> str:
        full = os.path.join(self.workspace, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return full

    def test_replaces_first_occurrence(self):
        path = self._write("f.txt", "hello world hello")
        tc = _make_tool_call("edit_file", path=path,
                             old_string="hello", new_string="hi")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        with open(path) as f:
            self.assertEqual(f.read(), "hi world hello")

    def test_old_string_not_found_returns_error(self):
        path = self._write("f.txt", "abc")
        tc = _make_tool_call("edit_file", path=path,
                             old_string="xyz", new_string="q")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("not found", result.content)

    def test_blocked_outside_workspace(self):
        outside = tempfile.mkdtemp()
        try:
            tc = _make_tool_call("edit_file",
                                 path=os.path.join(outside, "x.txt"),
                                 old_string="a", new_string="b")
            result = execute_tool(tc, self.write_gate, self.read_gate)
            self.assertFalse(result.success)
            self.assertIn("blocked by safety layer", result.content)
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)


# ---------------------------------------------------------------------------
# file_info tests
# ---------------------------------------------------------------------------

class TestFileInfo(unittest.TestCase):

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _write(self, relpath: str, content: str) -> str:
        full = os.path.join(self.workspace, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return full

    def test_existing_file_returns_metadata(self):
        path = self._write("notes.txt", "hello")
        tc = _make_tool_call("file_info", path=path)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("size: 5 bytes", result.content)
        self.assertIn("type: file", result.content)
        self.assertIn("mode:", result.content)
        self.assertIn("modified:", result.content)

    def test_directory_identified_as_directory(self):
        sub = os.path.join(self.workspace, "subdir")
        os.makedirs(sub)
        tc = _make_tool_call("file_info", path=sub)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("type: directory", result.content)

    def test_nonexistent_file_reports_not_found(self):
        path = os.path.join(self.workspace, "nope.txt")
        tc = _make_tool_call("file_info", path=path)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("exists: no", result.content)

    def test_blocked_outside_workspace(self):
        outside = tempfile.mkdtemp()
        try:
            tc = _make_tool_call("file_info", path=os.path.join(outside, "x.txt"))
            result = execute_tool(tc, self.write_gate, self.read_gate)
            self.assertFalse(result.success)
            self.assertIn("blocked by safety layer", result.content)
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)


# ---------------------------------------------------------------------------
# tool_summary tests
# ---------------------------------------------------------------------------

class TestToolSummary(unittest.TestCase):

    def test_read_file_summary(self):
        tc = _make_tool_call("read_file", path="/some/file.txt")
        s = tool_summary(tc)
        self.assertIn("read_file", s)
        self.assertIn("/some/file.txt", s)

    def test_write_file_summary(self):
        tc = _make_tool_call("write_file", path="out.txt", content="hello world")
        s = tool_summary(tc)
        self.assertIn("write_file", s)
        self.assertIn("out.txt", s)
        self.assertIn("11B", s)
        self.assertIn("hello world", s)

    def test_write_file_long_content_truncated(self):
        tc = _make_tool_call("write_file", path="x", content="a" * 100)
        s = tool_summary(tc)
        self.assertIn("…", s)
        self.assertLess(len(s), 150)

    def test_edit_file_summary(self):
        tc = _make_tool_call("edit_file", path="f.txt",
                             old_string="replace me", new_string="done")
        s = tool_summary(tc)
        self.assertIn("edit_file", s)
        self.assertIn("f.txt", s)
        self.assertIn("replace me", s)

    def test_list_directory_summary(self):
        tc = _make_tool_call("list_directory", path="/tmp")
        s = tool_summary(tc)
        self.assertIn("list_directory", s)
        self.assertIn("/tmp", s)

    def test_run_shell_summary(self):
        tc = _make_tool_call("run_shell", command="python -m pytest -v")
        s = tool_summary(tc)
        self.assertIn("run_shell", s)
        self.assertIn("python -m pytest", s)

    def test_run_shell_long_command_truncated(self):
        tc = _make_tool_call("run_shell", command="x" * 100)
        s = tool_summary(tc)
        self.assertIn("…", s)

    def test_search_files_summary(self):
        tc = _make_tool_call("search_files", pattern="TODO", path="src")
        s = tool_summary(tc)
        self.assertIn("search_files", s)
        self.assertIn("TODO", s)
        self.assertIn("src", s)

    def test_file_info_summary(self):
        tc = _make_tool_call("file_info", path="/a/b")
        s = tool_summary(tc)
        self.assertIn("file_info", s)
        self.assertIn("/a/b", s)

    def test_unknown_tool_summary(self):
        tc = _make_tool_call("nonexistent_tool", foo="bar")
        s = tool_summary(tc)
        self.assertIn("nonexistent_tool", s)
        self.assertIn("…", s)

    def test_summary_handles_bad_json(self):
        tc = {
            "id": "call_x",
            "function": {
                "name": "read_file",
                "arguments": "not valid json {{{",
            },
        }
        s = tool_summary(tc)
        self.assertIn("read_file", s)


if __name__ == "__main__":
    unittest.main()
