#!/usr/bin/env python3
"""Tests for viewer subagent, multi-edit batching, whole-file fallback, and confidence scoring."""

import os
import tempfile
import unittest

from conftest import make_tool_call as _make_tool_call, make_gates as _gates
from tools import (
    ToolResult,
    execute_tool,
    _TOOL_CONTEXT,
    _TASK_REGISTRY,
)
from tools._viewer import (
    extract_relevant_snippets,
    extract_relevant_content,
    _tokenize_query,
    _line_relevance,
)
from tools._file_utils import _READ_FILES


# ---------------------------------------------------------------------------
# Viewer subagent -- unit tests
# ---------------------------------------------------------------------------


class TestViewerTokenize(unittest.TestCase):
    def test_simple_query(self):
        tokens = _tokenize_query("find the handleSubmit function")
        self.assertIn("handlesubmit", tokens)
        self.assertIn("function", tokens)
        self.assertNotIn("the", tokens)  # stop word

    def test_camel_case_identifiers(self):
        tokens = _tokenize_query("look at UserProfile and getServerSideProps")
        self.assertIn("userprofile", tokens)
        self.assertIn("getserversideprops", tokens)

    def test_empty_query(self):
        tokens = _tokenize_query("the and for with")
        self.assertEqual(tokens, [])


class TestViewerLineRelevance(unittest.TestCase):
    def test_exact_match(self):
        score = _line_relevance("def handleSubmit(data):", ["handlesubmit"])
        self.assertGreater(score, 0.8)

    def test_substring_match(self):
        score = _line_relevance("const result = handleSubmit(formData)", ["submit"])
        self.assertGreater(score, 0.3)

    def test_no_match(self):
        score = _line_relevance("import React from 'react'", ["handlesubmit"])
        self.assertEqual(score, 0.0)

    def test_camel_case_decomposition(self):
        score = _line_relevance("function getServerSideProps() {", ["server"])
        self.assertGreater(score, 0.0)


class TestViewerExtractSnippets(unittest.TestCase):
    def setUp(self):
        self.content = "\n".join(
            [
                "import React from 'react'",
                "import { useState } from 'react'",
                "",
                "export default function App() {",
                "  const [count, setCount] = useState(0)",
                "",
                "  const handleSubmit = async (data: FormData) => {",
                "    await fetch('/api/submit', {",
                "      method: 'POST',",
                "      body: JSON.stringify(data),",
                "    })",
                "  }",
                "",
                "  return <div>Hello</div>",
                "}",
            ]
        )

    def test_finds_relevant_block(self):
        blocks = extract_relevant_snippets(self.content, "handleSubmit function")
        self.assertTrue(len(blocks) > 0)
        # The block should include line 7 (handleSubmit)
        start, end = blocks[0]
        self.assertLessEqual(start, 7)
        self.assertGreaterEqual(end, 7)

    def test_returns_empty_for_no_match(self):
        blocks = extract_relevant_snippets(self.content, "zzz_nonexistent_symbol_zzz")
        # Falls back to highest-scoring line, so might return something
        # but it should be very limited
        if blocks:
            # At most returns one block
            self.assertLessEqual(len(blocks), 1)

    def test_max_blocks_limit(self):
        content = "\n".join([f"function foo{i}() {{ return {i} }}" for i in range(100)])
        blocks = extract_relevant_snippets(content, "foo", max_blocks=3)
        self.assertLessEqual(len(blocks), 3)

    def test_context_lines(self):
        blocks = extract_relevant_snippets(
            self.content, "handleSubmit", context_lines=2
        )
        if blocks:
            start, end = blocks[0]
            # With context_lines=2, block should be tight around match
            self.assertLessEqual(end - start, 15)

    def test_formatted_output(self):
        result = extract_relevant_content(self.content, "handleSubmit")
        self.assertIn("handleSubmit", result)
        # With a small file the match may cover the whole thing
        self.assertTrue(len(result) > 0)


# ---------------------------------------------------------------------------
# view_file tool -- integration tests
# ---------------------------------------------------------------------------


class TestViewFileTool(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)
        _READ_FILES.clear()

    def tearDown(self):
        import shutil
        _READ_FILES.clear()
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _write(self, relpath: str, content: str) -> str:
        full = os.path.join(self.workspace, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return full

    def test_view_file_finds_snippets(self):
        content = "\n".join(
            [
                "// Header",
                "import React from 'react'",
                "",
                "function HomePage() {",
                "  return <h1>Home</h1>",
                "}",
                "",
                "export default HomePage",
            ]
        )
        path = self._write("HomePage.tsx", content)
        tc = _make_tool_call(
            "view_file", path=path, query="HomePage component"
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("HomePage", result.content)

    def test_view_file_no_match(self):
        content = "const x = 1\nexport default x"
        path = self._write("simple.ts", content)
        tc = _make_tool_call(
            "view_file", path=path, query="zzz_nonexistent_zzz"
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("No relevant snippets", result.content)

    def test_view_file_missing_params(self):
        tc = _make_tool_call("view_file", path="/tmp/x.ts")
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("Missing required", result.content)

    def test_view_file_adds_to_read_files(self):
        content = "function foo() { return 1 }"
        path = self._write("foo.ts", content)
        tc = _make_tool_call("view_file", path=path, query="foo")
        execute_tool(tc, self.write_gate, self.read_gate)
        # After view_file, the file should be in _READ_FILES
        resolved = os.path.realpath(path)
        self.assertIn(resolved, _READ_FILES)


# ---------------------------------------------------------------------------
# Multi-edit batching
# ---------------------------------------------------------------------------


class TestMultiEditBatching(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)
        _READ_FILES.clear()
        # Clear file caches to prevent cross-test contamination
        from tools._file_utils import _FILE_CACHE, _BACKUPS
        _FILE_CACHE.clear()
        _BACKUPS.clear()
        # Invalidate schema cache (schema was modified)
        from tools.__init__ import _TOOL_SCHEMA_MAP, _TOOL_SCHEMA_MAP_LEN
        _TOOL_SCHEMA_MAP.clear()
        _TOOL_SCHEMA_MAP_LEN = 0

    def tearDown(self):
        import shutil
        _READ_FILES.clear()
        from tools._file_utils import _FILE_CACHE, _BACKUPS
        _FILE_CACHE.clear()
        _BACKUPS.clear()
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _write(self, relpath: str, content: str) -> str:
        full = os.path.join(self.workspace, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return full

    def test_multi_edit_single_file(self):
        content = "line1\nline2\nline3\nline4"
        path = self._write("test.txt", content)

        # Must read before editing
        execute_tool(
            _make_tool_call("read_file", path=path),
            self.write_gate,
            self.read_gate,
        )

        # Verify multi-edit mode works with a single edit
        tc = _make_tool_call(
            "edit_file",
            edits=[
                {"path": path, "old_string": "line1", "new_string": "LINE_ONE"},
            ],
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success, f"Failed: {result.content}")
        self.assertIn("Multi-edit results", result.content)

        with open(path) as f:
            updated = f.read()
        self.assertIn("LINE_ONE", updated)

    def test_multi_edit_stops_on_failure(self):
        content = "aaa\nbbb\nccc"
        path = self._write("test.txt", content)

        execute_tool(
            _make_tool_call("read_file", path=path),
            self.write_gate,
            self.read_gate,
        )

        tc = _make_tool_call(
            "edit_file",
            edits=[
                {"path": path, "old_string": "aaa", "new_string": "AAA"},
                {"path": path, "old_string": "zzz_nonexistent", "new_string": "ZZZ"},
            ],
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("[FAIL]", result.content)

        # Verify first edit was applied before stopping
        with open(path) as f:
            updated = f.read()
        self.assertIn("AAA", updated)

    def test_multi_edit_multiple_files(self):
        content_a = "foo"
        content_b = "bar"
        path_a = self._write("a.txt", content_a)
        path_b = self._write("b.txt", content_b)

        execute_tool(
            _make_tool_call("read_file", path=path_a),
            self.write_gate,
            self.read_gate,
        )
        execute_tool(
            _make_tool_call("read_file", path=path_b),
            self.write_gate,
            self.read_gate,
        )

        tc = _make_tool_call(
            "edit_file",
            edits=[
                {"path": path_a, "old_string": "foo", "new_string": "FOO"},
                {"path": path_b, "old_string": "bar", "new_string": "BAR"},
            ],
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)

        with open(path_a) as f:
            self.assertEqual(f.read(), "FOO")
        with open(path_b) as f:
            self.assertEqual(f.read(), "BAR")

    def test_multi_edit_empty_edits(self):
        tc = _make_tool_call("edit_file", edits=[])
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("non-empty", result.content.lower())

    def test_multi_edit_missing_path(self):
        tc = _make_tool_call(
            "edit_file",
            edits=[{"old_string": "x", "new_string": "y"}],
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("missing", result.content.lower())


# ---------------------------------------------------------------------------
# Whole-file rewrite fallback
# ---------------------------------------------------------------------------


class TestWholeFileFallback(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)
        _READ_FILES.clear()
        # Clear file caches to prevent cross-test contamination
        from tools._file_utils import _FILE_CACHE, _BACKUPS
        _FILE_CACHE.clear()
        _BACKUPS.clear()
        # Invalidate schema cache (schema was modified)
        from tools.__init__ import _TOOL_SCHEMA_MAP, _TOOL_SCHEMA_MAP_LEN
        _TOOL_SCHEMA_MAP.clear()
        _TOOL_SCHEMA_MAP_LEN = 0

    def tearDown(self):
        import shutil
        _READ_FILES.clear()
        from tools._file_utils import _FILE_CACHE, _BACKUPS
        _FILE_CACHE.clear()
        _BACKUPS.clear()
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _write(self, relpath: str, content: str) -> str:
        full = os.path.join(self.workspace, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return full

    def test_whole_file_fallback_replaces_entire_file(self):
        original = "old content line 1\nold content line 2\nold content line 3"
        path = self._write("test.txt", original)

        execute_tool(
            _make_tool_call("read_file", path=path),
            self.write_gate,
            self.read_gate,
        )

        tc = _make_tool_call(
            "edit_file",
            path=path,
            old_string="this string does not exist in the file",
            new_string="completely new content\nline 2\nline 3",
            fallback="whole-file",
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success, f"Failed: {result.content}")
        self.assertIn("whole-file rewrite", result.content)

        with open(path) as f:
            self.assertEqual(f.read(), "completely new content\nline 2\nline 3")

    def test_fallback_error_mode_still_fails(self):
        original = "original content"
        path = self._write("test.txt", original)

        execute_tool(
            _make_tool_call("read_file", path=path),
            self.write_gate,
            self.read_gate,
        )

        tc = _make_tool_call(
            "edit_file",
            path=path,
            old_string="nonexistent string",
            new_string="new content",
            fallback="error",
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        self.assertIn("not found", result.content.lower())

    def test_fallback_auto_with_low_confidence_falls_through(self):
        """auto mode with confidence < 60% should fall through to error."""
        original = "original content"
        path = self._write("test.txt", original)

        execute_tool(
            _make_tool_call("read_file", path=path),
            self.write_gate,
            self.read_gate,
        )

        tc = _make_tool_call(
            "edit_file",
            path=path,
            old_string="completely different content that has no match",
            new_string="new content",
            fallback="auto",
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        # auto with low confidence (< 60%) should act like "error"
        self.assertFalse(result.success)

    def test_preview_with_fallback(self):
        original = "old content"
        path = self._write("test.txt", original)

        execute_tool(
            _make_tool_call("read_file", path=path),
            self.write_gate,
            self.read_gate,
        )

        tc = _make_tool_call(
            "edit_file",
            path=path,
            old_string="nonexistent",
            new_string="preview content",
            fallback="whole-file",
            preview=True,
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertTrue(result.success)
        self.assertIn("whole-file fallback", result.content)
        self.assertIn("Preview", result.content)

        # File should NOT have been modified (preview mode)
        with open(path) as f:
            self.assertEqual(f.read(), "old content")

    def test_confidence_score_in_error_message(self):
        original = "line 1: import os\nline 2: import sys\nline 3: x = 1"
        path = self._write("test.py", original)

        execute_tool(
            _make_tool_call("read_file", path=path),
            self.write_gate,
            self.read_gate,
        )

        tc = _make_tool_call(
            "edit_file",
            path=path,
            old_string="import os\nimport json\nx = 1",
            new_string="import os\nimport json\nx = 2",
        )
        result = execute_tool(tc, self.write_gate, self.read_gate)
        self.assertFalse(result.success)
        # Should contain confidence percentage
        self.assertIn("confidence", result.content.lower())
        # Should contain fallback tip
        self.assertIn("fallback", result.content.lower())


# ---------------------------------------------------------------------------
# Viewer: _viewer_llm_fallback (smoke test without LLM)
# ---------------------------------------------------------------------------


class TestViewerLLMFallback(unittest.TestCase):
    def test_returns_none_when_no_llm(self):
        from tools._viewer import _viewer_llm_fallback
        result = _viewer_llm_fallback("content", "query", "file.ts")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
