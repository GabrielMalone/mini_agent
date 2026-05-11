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

    # --- shell guard ---

    def test_blocks_rm_rf(self):
        tc = _make_tool_call("run_shell", command="rm -rf /etc")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("blocked by safety guard", result.content)

    def test_blocks_fork_bomb(self):
        tc = _make_tool_call("run_shell", command=":(){ :|:& };:")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("blocked by safety guard", result.content)

    def test_force_bypasses_guard(self):
        tc = _make_tool_call("run_shell", command="rm -rf /nonexistent_test_dir", force=True)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        # Will succeed or fail depending on permissions, but NOT blocked by guard
        self.assertNotIn("blocked by safety guard", result.content)

    def test_safe_commands_not_blocked(self):
        tc = _make_tool_call("run_shell", command="echo hello && ls -la")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("hello", result.content)


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

    # --- regex search ---

    def test_regex_matches(self):
        self._write("funcs.py", "def hello():\n  pass\nclass Foo:\n  pass\n")
        tc = _make_tool_call("search_files", pattern=r"def \w+", path=self.workspace, regex=True)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("def hello", result.content)
        # "class Foo" should NOT match
        self.assertNotIn("class Foo", result.content)

    def test_regex_finds_decorators(self):
        self._write("deco.py", "@register\ndef f(): pass\n@summarize\ndef g(): pass\n")
        tc = _make_tool_call("search_files", pattern=r"@\w+", path=self.workspace, regex=True)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("@register", result.content)
        self.assertIn("@summarize", result.content)

    def test_invalid_regex_returns_error(self):
        tc = _make_tool_call("search_files", pattern="[unclosed", path=self.workspace, regex=True)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("Invalid regex", result.content)

    # --- case-insensitive search ---

    def test_case_insensitive_matches(self):
        self._write("caps.py", "HELLO WORLD\nFooBar\n")
        tc = _make_tool_call("search_files", pattern="hello", path=self.workspace, ignore_case=True)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("HELLO", result.content)

    def test_case_sensitive_still_works(self):
        self._write("caps.py", "HELLO WORLD\n")
        tc = _make_tool_call("search_files", pattern="hello",
                             path=os.path.join(self.workspace, "caps.py"))
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("No matches", result.content)

    def test_case_insensitive_with_regex(self):
        self._write("caps.py", "HELLO test\nhello TEST\n")
        tc = _make_tool_call("search_files", pattern=r"test", path=self.workspace,
                             regex=True, ignore_case=True)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("HELLO test", result.content)
        self.assertIn("hello TEST", result.content)


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


# ---------------------------------------------------------------------------
# run_tests tool tests
# ---------------------------------------------------------------------------

class TestRunTests(unittest.TestCase):
    """Verify the run_tests tool works with real pytest output."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)
        # Create a minimal test file so pytest has something to discover
        test_dir = os.path.join(self.workspace, "tests")
        os.makedirs(test_dir)
        with open(os.path.join(test_dir, "__init__.py"), "w") as f:
            f.write("")
        with open(os.path.join(test_dir, "test_dummy.py"), "w") as f:
            f.write("def test_pass(): assert True\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_runs_all_tests_in_workspace(self):
        tc = _make_tool_call("run_tests")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("passed", result.content)

    def test_runs_specific_file(self):
        tc = _make_tool_call("run_tests", path="tests/test_dummy.py")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("passed", result.content)

    def test_failing_tests_return_failure(self):
        with open(os.path.join(self.workspace, "tests", "test_fail.py"), "w") as f:
            f.write("def test_fail(): assert False\n")
        tc = _make_tool_call("run_tests", path="tests/test_fail.py")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("failed", result.content)

    def test_returns_tool_result_not_exception(self):
        tc = _make_tool_call("run_tests", path="nonexistent_file.py")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertIsInstance(result, ToolResult)


# ---------------------------------------------------------------------------
# web_search tests
# ---------------------------------------------------------------------------

class TestWebSearch(unittest.TestCase):
    """Verify web_search tool behavior. Uses real API if key is available."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)
        from config import DEFAULT_EXA_API_KEY
        from tools import set_context
        set_context(exa_api_key=os.environ.get("EXA_API_KEY", DEFAULT_EXA_API_KEY))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_requires_query(self):
        tc = _make_tool_call("web_search")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertIsInstance(result, ToolResult)

    # --- API-call tests disabled to save Exa tokens ---
    #
    # def test_valid_search_returns_results(self):
    #     from config import DEFAULT_EXA_API_KEY
    #     api_key = os.environ.get("EXA_API_KEY", DEFAULT_EXA_API_KEY)
    #     if not api_key:
    #         self.skipTest("EXA_API_KEY not set")
    #     tc = _make_tool_call("web_search", query="Python typing module best practices", num_results=3)
    #     result = execute_tool(tc, self.write_gate, self.read_gate)
    #     self.assertTrue(result.success)
    #     self.assertIn("1.", result.content)
    #     self.assertIn("http", result.content)
    #
    # def test_no_results_for_nonsense_query(self):
    #     from config import DEFAULT_EXA_API_KEY
    #     api_key = os.environ.get("EXA_API_KEY", DEFAULT_EXA_API_KEY)
    #     if not api_key:
    #         self.skipTest("EXA_API_KEY not set")
    #     tc = _make_tool_call("web_search", query="xxyzzzblargnothingatall123456789")
    #     result = execute_tool(tc, self.write_gate, self.read_gate)
    #     self.assertTrue(result.success)


# ---------------------------------------------------------------------------
# semantic_search tests
# ---------------------------------------------------------------------------

class TestSemanticSearch(unittest.TestCase):
    """Verify semantic_search indexes .py files and returns relevant chunks."""

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

    def test_requires_query(self):
        tc = _make_tool_call("semantic_search")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertIsInstance(result, ToolResult)
        self.assertFalse(result.success)
        self.assertIn("query", result.content)

    def test_finds_relevant_chunks(self):
        self._write("auth.py", "def authenticate_user(token):\n    if token:\n        return True\n    return False\n")
        self._write("storage.py", "def save_file(path, data):\n    with open(path, 'w') as f:\n        f.write(data)\n")
        tc = _make_tool_call("semantic_search", query="user login and authentication", path=self.workspace)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        # Should find the auth function first
        self.assertIn("auth.py", result.content.lower())

    def test_finds_file_io_chunks(self):
        self._write("auth.py", "def authenticate_user(token):\n    if token:\n        return True\n    return False\n")
        self._write("storage.py", "def save_file(path, data):\n    with open(path, 'w') as f:\n        f.write(data)\n")
        tc = _make_tool_call("semantic_search", query="writing files to disk", path=self.workspace)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("storage.py", result.content.lower())

    def test_no_python_files_returns_message(self):
        self._write("readme.md", "# hello")
        tc = _make_tool_call("semantic_search", query="anything", path=self.workspace)
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("No matches found", result.content)

    def test_blocked_outside_workspace(self):
        outside = tempfile.mkdtemp()
        try:
            tc = _make_tool_call("semantic_search", query="anything", path=outside)
            result = execute_tool(tc, self.write_gate, self.read_gate)
            self.assertFalse(result.success)
            self.assertIn("blocked by safety layer", result.content)
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
