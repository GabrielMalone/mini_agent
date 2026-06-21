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
    _web_search,
)
from tools.ast_tools import _get_file_skeleton, _get_function
from tools.lsp import _lsp_definition, _lsp_references, _lsp_hover, _lsp_diagnostics


class TestFindSymbolCorrectness(unittest.TestCase):
    """Verify find_symbol returns sane, correct results."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.wg = WriteSafetyGate(self.root, allow_overwrites=True)
        self.rg = ReadSafetyGate(self.root)
        # Write a test file with known symbols
        src = os.path.join(self.root, "test_mod.py")
        with open(src, "w") as f:
            f.write(
                "def add_numbers(a, b):\n"
                "    return a + b\n\n"
                "def subtract_numbers(a, b):\n"
                "    return a - b\n\n"
                "class Calculator:\n"
                "    def compute(self, x, y):\n"
                "        return add_numbers(x, y)\n"
            )
        # Also write a second file
        src2 = os.path.join(self.root, "utils.py")
        with open(src2, "w") as f:
            f.write(
                "from test_mod import add_numbers\n\n"
                "def calculate_total(items):\n"
                "    return sum(items)\n"
            )
        # Set up context for tools that need it
        from tools.context import set_context
        set_context(workspace=self.root, _read_gate=self.rg)

    def test_exact_symbol_match(self):
        """find_symbol should find an exact symbol by name."""
        result = _find_symbol({"name": "add_numbers"}, self.wg, self.rg)
        self.assertTrue(result.success)
        self.assertIn("add_numbers", result.content)
        # Should find exactly the definition, not extras
        self.assertIn("test_mod.py", result.content)

    def test_no_match_returns_graceful(self):
        """find_symbol should return success with a 'not found' message."""
        result = _find_symbol({"name": "nonexistent_xyzzy"}, self.wg, self.rg)
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
        result = _get_file_skeleton(
            {"paths": ["test_mod.py"]}, self.wg, self.rg
        )
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
            {"file_path": os.path.join(self.root, "test_mod.py"), "line": 1, "character": 5},
            self.wg,
            self.rg,
        )
        # May succeed or fail depending on pylsp availability, but shouldn't crash
        self.assertIsNotNone(result)

    def test_lsp_references_on_existing_file(self):
        result = _lsp_references(
            {"file_path": os.path.join(self.root, "test_mod.py"), "line": 1, "character": 5},
            self.wg,
            self.rg,
        )
        self.assertIsNotNone(result)

    def test_lsp_hover_on_existing_file(self):
        result = _lsp_hover(
            {"file_path": os.path.join(self.root, "test_mod.py"), "line": 1, "character": 5},
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


if __name__ == "__main__":
    unittest.main()
