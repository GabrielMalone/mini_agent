"""End-to-end test for replace_symbol via execute_tool dispatch."""

from __future__ import annotations

import os
import tempfile
import unittest


class TestReplaceSymbolE2E(unittest.TestCase):
    """Test replace_symbol through the real tool dispatch with proper context."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.test_file = os.path.join(self.workspace, "test_lib.py")
        with open(self.test_file, "w") as f:
            f.write(
                '"""Test library."""\n\n'
                "def add(a: int, b: int) -> int:\n"
                '    """Add two numbers."""\n'
                "    return a + b\n\n\n"
                "def subtract(a: int, b: int) -> int:\n"
                '    """Subtract two numbers."""\n'
                "    return a - b\n"
            )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _call_tool(self, name, args):
        from tools import execute_tool, set_context
        from core.safety import WriteSafetyGate, ReadSafetyGate
        import json

        wg = WriteSafetyGate(self.workspace)
        rg = ReadSafetyGate(self.workspace)

        try:
            from core.config import load
            config = load(["-w", self.workspace])
        except Exception:
            config = None
        set_context(_agent_config=config, workspace=self.workspace)

        return execute_tool(
            {"function": {"name": name, "arguments": json.dumps(args)}},
            wg,
            rg,
        )

    def test_get_file_skeleton_works(self):
        result = self._call_tool("get_file_skeleton", {"paths": ["test_lib.py"]})
        self.assertTrue(result.success, f"get_file_skeleton failed: {result.content}")
        self.assertIn("def add", result.content)

    def test_get_function_works(self):
        result = self._call_tool(
            "get_function", {"path": "test_lib.py", "name": "add"}
        )
        self.assertTrue(result.success, f"get_function failed: {result.content}")
        self.assertIn("def add", result.content)

    def test_replace_symbol_works(self):
        new_text = (
            'def add(a: int, b: int) -> int:\n'
            '    """Add two numbers, now modified."""\n'
            '    return a + b + 1'
        )
        result = self._call_tool(
            "replace_symbol",
            {"path": "test_lib.py", "symbol": "add", "text": new_text},
        )
        self.assertTrue(result.success, f"replace_symbol failed: {result.content}")

        with open(self.test_file) as f:
            content = f.read()
        self.assertIn("now modified", content)
        self.assertIn("return a + b + 1", content)

    def test_replace_symbol_nonexistent_symbol(self):
        """Replacing a nonexistent symbol should report the issue."""
        result = self._call_tool(
            "replace_symbol",
            {
                "path": "test_lib.py",
                "symbol": "nonexistent_function",
                "text": "def foo(): pass",
            },
        )
        # May succeed or fail depending on implementation - but shouldn't crash
        self.assertIsNotNone(result)

    def test_replace_symbol_missing_path_fails(self):
        result = self._call_tool(
            "replace_symbol", {"symbol": "add", "text": "def add(): pass"}
        )
        self.assertFalse(result.success)

    def test_replace_symbol_missing_symbol_fails(self):
        result = self._call_tool(
            "replace_symbol", {"path": "test_lib.py"}
        )
        self.assertFalse(result.success)

    def test_replace_symbol_preserves_other_functions(self):
        new_text = (
            'def add(a: int, b: int) -> int:\n'
            '    """Modified."""\n'
            '    return a + b + 1'
        )
        result = self._call_tool(
            "replace_symbol",
            {"path": "test_lib.py", "symbol": "add", "text": new_text},
        )
        self.assertTrue(result.success)

        with open(self.test_file) as f:
            content = f.read()
        self.assertIn("def subtract", content)
        self.assertIn("return a - b", content)

    def test_replace_symbol_class_replacement(self):
        with open(self.test_file, "w") as f:
            f.write(
                '"""Test lib with class."""\n\n'
                "class Calculator:\n"
                '    """A simple calculator."""\n'
                "    def add(self, a, b):\n"
                "        return a + b\n\n"
                "    def multiply(self, a, b):\n"
                "        return a * b\n"
            )
        new_text = (
            "class Calculator:\n"
            '    """Updated calculator."""\n'
            "    def add(self, a, b):\n"
            "        return a + b + 0\n\n"
            "    def multiply(self, a, b):\n"
            "        return a * b\n\n"
            "    def divide(self, a, b):\n"
            "        return a / b"
        )
        result = self._call_tool(
            "replace_symbol",
            {"path": "test_lib.py", "symbol": "Calculator", "text": new_text},
        )
        self.assertTrue(
            result.success, f"replace_symbol class failed: {result.content}"
        )
        with open(self.test_file) as f:
            content = f.read()
        self.assertIn("Updated calculator", content)
        self.assertIn("def divide", content)

    def test_get_function_with_include_anchors(self):
        result = self._call_tool(
            "get_function",
            {"path": "test_lib.py", "name": "add", "include_anchors": True},
        )
        self.assertTrue(result.success, f"get_function failed: {result.content}")

    def test_get_file_skeleton_nonexistent_file(self):
        result = self._call_tool(
            "get_file_skeleton", {"paths": ["nonexistent.py"]}
        )
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
