#!/usr/bin/env python3
"""Tests for the mini_agent search system correctness."""

from __future__ import annotations

import os
import tempfile
import unittest

from core.safety import ReadSafetyGate, WriteSafetyGate
from tools.search_ops import (
    _find_symbol,
    _find_usages,
    _find_callers,
    _find_callees,
    _find_related,
    _search_ast,
)
from tools.ast_tools import _get_file_skeleton, _get_function
from tools.lsp import _lsp_definition, _lsp_references, _lsp_hover, _lsp_diagnostics


class TestFindSymbolCorrectness(unittest.TestCase):
    """Verify find_symbol returns correct results."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.wg = WriteSafetyGate(self.root, allow_overwrites=True)
        self.rg = ReadSafetyGate(self.root)
        src = os.path.join(self.root, "test_mod.py")
        with open(src, "w") as f:
            f.write(
                "def add_numbers(a, b):\n"
                "    return a + b\n\n"
                "class Calculator:\n"
                "    def multiply(self, x, y):\n"
                "        return x * y\n"
            )
        from tools.context import set_context

        set_context(workspace=self.root, _read_gate=self.rg)

    def test_exact_symbol_match(self):
        """find_symbol should locate a symbol by exact name."""
        result = _find_symbol({"name": "add_numbers"}, self.wg, self.rg)
        self.assertTrue(result.success)
        self.assertIn("test_mod.py", result.content)

    def test_no_match_returns_graceful(self):
        """find_symbol should return a graceful message on no match."""
        result = _find_symbol({"name": "nonexistent_func_xyz"}, self.wg, self.rg)
        self.assertTrue(result.success)
        self.assertIn("No symbols", result.content)

    def test_case_insensitive_substring(self):
        """find_symbol should do case-insensitive substring matching as fallback."""
        result = _find_symbol({"name": "ADD"}, self.wg, self.rg)
        self.assertTrue(result.success)
        self.assertIn("add_numbers", result.content)

    def test_empty_name_rejected(self):
        """find_symbol should reject empty name."""
        result = _find_symbol({"name": ""}, self.wg, self.rg)
        self.assertFalse(result.success)

    def test_missing_name_rejected(self):
        """find_symbol should reject missing name param."""
        result = _find_symbol({}, self.wg, self.rg)
        self.assertFalse(result.success)


class TestFindUsagesCorrectness(unittest.TestCase):
    """Verify find_usages returns sane results."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.wg = WriteSafetyGate(self.root, allow_overwrites=True)
        self.rg = ReadSafetyGate(self.root)
        src = os.path.join(self.root, "test_mod.py")
        with open(src, "w") as f:
            f.write(
                "def helper(x):\n"
                "    return x * 2\n\n"
                "def process(data):\n"
                "    result = helper(1)\n"
                "    result = helper(2)\n"
                "    return result\n"
            )
        from tools.context import set_context

        set_context(workspace=self.root, _read_gate=self.rg)

    def test_finds_usages(self):
        """find_usages should find references to a defined symbol."""
        result = _find_usages({"name": "helper"}, self.wg, self.rg)
        self.assertTrue(result.success)
        # Should find usages in process function
        self.assertIn("test_mod.py", result.content)


class TestCallerCalleeTools(unittest.TestCase):
    """Verify find_callers/find_callees use correct parameter name."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.wg = WriteSafetyGate(self.root, allow_overwrites=True)
        self.rg = ReadSafetyGate(self.root)
        from tools.context import set_context

        set_context(workspace=self.root, _read_gate=self.rg)

    def test_find_callers_uses_name_param(self):
        """Schema says 'name' - implementation should use 'name' not 'symbol'."""
        result = _find_callers({"name": "some_func"}, self.wg, self.rg)
        # Should not error about missing 'symbol' parameter
        self.assertNotIn("Missing required parameter: 'symbol'", result.content)

    def test_find_callees_uses_name_param(self):
        """Schema says 'name' - implementation should use 'name' not 'symbol'."""
        result = _find_callees({"name": "some_func"}, self.wg, self.rg)
        self.assertNotIn("Missing required parameter: 'symbol'", result.content)

    def test_find_related_uses_name_param(self):
        """Schema says 'name' - implementation should use 'name' not 'symbol'."""
        result = _find_related({"name": "some_func"}, self.wg, self.rg)
        self.assertNotIn("Missing required parameter: 'symbol'", result.content)

    def test_caller_empty_name_rejected(self):
        result = _find_callers({"name": ""}, self.wg, self.rg)
        self.assertFalse(result.success)

    def test_callee_empty_name_rejected(self):
        result = _find_callees({"name": ""}, self.wg, self.rg)
        self.assertFalse(result.success)


class TestASTTools(unittest.TestCase):
    """Verify AST-based tools work correctly."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.wg = WriteSafetyGate(self.root, allow_overwrites=True)
        self.rg = ReadSafetyGate(self.root)
        src = os.path.join(self.root, "test_mod.py")
        with open(src, "w") as f:
            f.write(
                "def outer():\n"
                "    pass\n\n"
                "class MyClass:\n"
                "    def method(self):\n"
                "        pass\n"
            )
        from tools.context import set_context

        set_context(workspace=self.root, _read_gate=self.rg)

    def test_get_file_skeleton_finds_defs(self):
        result = _get_file_skeleton({"paths": ["test_mod.py"]}, self.wg, self.rg)
        self.assertTrue(result.success)
        content = str(result.content)
        self.assertIn("outer", content)
        self.assertIn("class", content.lower())
        self.assertIn("method", content)

    def test_get_function_finds_by_name(self):
        result = _get_function(
            {"path": "test_mod.py", "name": "outer"}, self.wg, self.rg
        )
        self.assertTrue(result.success)

    def test_get_function_nonexistent(self):
        result = _get_function(
            {"path": "test_mod.py", "name": "nonexistent_xyz"}, self.wg, self.rg
        )
        # Returns success=True with a "not found" message (not an error)
        self.assertTrue(result.success)
        self.assertIn("not found", str(result.content).lower())


class TestLSPTools(unittest.TestCase):
    """Verify LSP tools handle errors gracefully."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.wg = WriteSafetyGate(self.root, allow_overwrites=True)
        self.rg = ReadSafetyGate(self.root)
        src = os.path.join(self.root, "test_mod.py")
        with open(src, "w") as f:
            f.write("def foo():\n    pass\n")
        from tools.context import set_context

        set_context(workspace=self.root, _read_gate=self.rg)
        # Set LSP root
        from tools.lsp import set_lsp_root

        set_lsp_root(self.root)

    def tearDown(self):
        from tools.lsp import shutdown_lsp

        shutdown_lsp()

    def test_lsp_definition_on_existing_file(self):
        """LSP definition should work on a real Python file or return error."""
        result = _lsp_definition(
            {
                "file_path": os.path.join(self.root, "test_mod.py"),
                "line": 1,
                "character": 5,
            },
            self.wg,
            self.rg,
        )
        # May succeed or fail depending on pylsp availability, but shouldn't crash
        self.assertIsNotNone(result)

    def test_lsp_references_on_existing_file(self):
        result = _lsp_references(
            {
                "file_path": os.path.join(self.root, "test_mod.py"),
                "line": 1,
                "character": 5,
            },
            self.wg,
            self.rg,
        )
        self.assertIsNotNone(result)

    def test_lsp_hover_on_existing_file(self):
        result = _lsp_hover(
            {
                "file_path": os.path.join(self.root, "test_mod.py"),
                "line": 1,
                "character": 5,
            },
            self.wg,
            self.rg,
        )
        self.assertIsNotNone(result)

    def test_lsp_diagnostics_on_existing_file(self):
        result = _lsp_diagnostics(
            {"file_path": os.path.join(self.root, "test_mod.py")},
            self.wg,
            self.rg,
        )
        self.assertIsNotNone(result)


class TestWorkspaceScanner(unittest.TestCase):
    """Verify workspace_scanner walks files correctly."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        # Create a small file tree
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "src", "__pycache__"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "node_modules"), exist_ok=True)
        with open(os.path.join(self.root, "src", "main.py"), "w") as f:
            f.write("def foo():\n    pass\n")
        with open(os.path.join(self.root, "src", "utils.js"), "w") as f:
            f.write("function bar() {}\n")
        with open(os.path.join(self.root, "src", "style.css"), "w") as f:
            f.write("body { color: red; }\n")
        with open(os.path.join(self.root, "src", "__pycache__", "main.cpython-312.pyc"), "w") as f:
            f.write("cache")
        with open(os.path.join(self.root, "node_modules", "lodash.js"), "w") as f:
            f.write("// lodash")

    def test_walks_skips_hidden_and_cache_dirs(self):
        """walk_workspace should skip __pycache__, node_modules, .dirs."""
        from core.workspace_scanner import walk_workspace, Handler

        seen: list[str] = []

        def record(fpath: str, ext: str, root: str) -> None:
            seen.append(os.path.relpath(fpath, root))

        walk_workspace(self.root, [Handler(fn=record)])
        self.assertIn("src/main.py", seen)
        self.assertIn("src/utils.js", seen)
        # __pycache__ and node_modules should be skipped
        self.assertNotIn(
            os.path.join("src", "__pycache__", "main.cpython-312.pyc"), seen
        )
        self.assertNotIn("node_modules/lodash.js", seen)
        # CSS should not be included (not a source extension)
        self.assertNotIn("src/style.css", seen)

    def test_handler_ext_filter(self):
        """Handlers with exts should only receive matching files."""
        from core.workspace_scanner import walk_workspace, Handler

        seen: list[str] = []

        def record(fpath: str, ext: str, root: str) -> None:
            seen.append(ext)

        walk_workspace(self.root, [Handler(exts=[".py"], fn=record)])
        # Only .py files should be dispatched
        self.assertTrue(all(e == ".py" for e in seen))
        self.assertGreater(len(seen), 0)

    def test_handler_exception_doesnt_kill_scan(self):
        """A handler that raises should not stop the walk."""
        from core.workspace_scanner import walk_workspace, Handler

        seen: list[str] = []

        def exploding(fpath: str, ext: str, root: str) -> None:
            raise RuntimeError("boom")

        def recorder(fpath: str, ext: str, root: str) -> None:
            seen.append(os.path.relpath(fpath, root))

        # Both handlers registered; exploding one shouldn't prevent recorder
        walk_workspace(self.root, [Handler(fn=exploding), Handler(fn=recorder)])
        self.assertIn("src/main.py", seen)
        self.assertIn("src/utils.js", seen)

    def test_walk_workspace_src_exts_override(self):
        """Custom src_exts should control which files are visited."""
        from core.workspace_scanner import walk_workspace, Handler

        seen: list[str] = []

        def record(fpath: str, ext: str, root: str) -> None:
            seen.append(ext)

        # Only .css files
        walk_workspace(
            self.root,
            [Handler(fn=record)],
            src_exts=frozenset({".css"}),
        )
        self.assertTrue(all(e == ".css" for e in seen))
        self.assertIn(".css", seen)


class TestSearchAST(unittest.TestCase):
    """Verify search_ast finds structural patterns."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.wg = WriteSafetyGate(self.root, allow_overwrites=True)
        self.rg = ReadSafetyGate(self.root)
        src = os.path.join(self.root, "test_mod.py")
        with open(src, "w") as f:
            f.write(
                "def foo():\n"
                "    try:\n"
                "        x = 1\n"
                "    except ValueError:\n"
                "        pass\n"
                "\n"
                "async def bar():\n"
                "    await baz()\n"
                "\n"
                "@decorator\n"
                "def decorated():\n"
                "    pass\n"
                "\n"
                "for i in range(10):\n"
                "    print(i)\n"
                "\n"
                "while True:\n"
                "    break\n"
                "\n"
                "if x:\n"
                "    pass\n"
                "else:\n"
                "    pass\n"
                "\n"
                "with open('f') as f:\n"
                "    pass\n"
                "\n"
                "l = lambda x: x * 2\n"
                "\n"
                "class MyClass:\n"
                "    pass\n"
                "\n"
                "import os\n"
                "from sys import path\n"
            )
        from tools.context import set_context

        set_context(workspace=self.root, _read_gate=self.rg)

    def _search(self, pattern: str) -> str:
        result = _search_ast(
            {"pattern": pattern, "path": self.root}, self.wg, self.rg
        )
        self.assertTrue(result.success, f"search_ast failed: {result.content}")
        return str(result.content)

    def test_try_except(self):
        out = self._search("try_except")
        self.assertIn("try", out)

    def test_async_function(self):
        out = self._search("async_function")
        self.assertIn("bar", out)

    def test_decorator(self):
        out = self._search("decorator")
        self.assertIn("decorator", out)

    def test_for_loop(self):
        out = self._search("for_loop")
        self.assertIn("for", out)

    def test_while_loop(self):
        out = self._search("while_loop")
        self.assertIn("while", out)

    def test_if_else(self):
        out = self._search("if_else")
        self.assertIn("if", out)

    def test_with_block(self):
        out = self._search("with_block")
        self.assertIn("with", out)

    def test_lambda(self):
        out = self._search("lambda")
        self.assertIn("lambda", out)

    def test_class_def(self):
        out = self._search("class_def")
        self.assertIn("MyClass", out)

    def test_function_def(self):
        out = self._search("function_def")
        self.assertIn("foo", out)
        self.assertIn("bar", out)

    def test_import(self):
        out = self._search("import")
        self.assertIn("os", out)
        self.assertIn("path", out)

    def test_unknown_pattern_rejected(self):
        result = _search_ast(
            {"pattern": "nonexistent_pattern", "path": self.root},
            self.wg,
            self.rg,
        )
        self.assertFalse(result.success)
        self.assertIn("Unknown pattern", str(result.content))


if __name__ == "__main__":
    unittest.main()
