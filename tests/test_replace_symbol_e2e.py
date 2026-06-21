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
            f.write("""\"\"\"Test library.\"\"\"

def add(a: int, b: int) -> int:
    \"\"\"Add two numbers.\"\"\"
    return a + b


def subtract(a: int, b: int) -> int:
    \"\"\"Subtract two numbers.\"\"\"
    return a - b
""")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.workspace, ignore_errors=True)

    def _call_tool(self, name, args):
        from tools import execute_tool, set_context
        from core.safety import WriteSafetyGate, ReadSafetyGate

        wg = WriteSafetyGate(self.workspace)
        rg = ReadSafetyGate(self.workspace)

        # Set up context minimally
        try:
            from core.config import load

            config = load(["-w", self.workspace])
        except Exception:
            config = None
        set_context(_agent_config=config, workspace=self.workspace)

        import json

        return execute_tool(
            {"function": {"name": name, "arguments": json.dumps(args)}},
            wg,
            rg,
        )

    def test_get_file_skeleton_works(self):
        """get_file_skeleton should find definitions via tree-sitter."""
        result = self._call_tool("get_file_skeleton", {"paths": ["test_lib.py"]})
        self.assertTrue(result.success, f"get_file_skeleton failed: {result.content}")
        self.assertIn("def add", result.content)

    def test_get_function_works(self):
        """get_function should retrieve a specific function body."""
        result = self._call_tool("get_function", {"path": "test_lib.py", "name": "add"})
        self.assertTrue(result.success, f"get_function failed: {result.content}")
        self.assertIn("def add", result.content)

    def test_replace_symbol_works(self):
        """replace_symbol should replace a function and write the result."""
        result = self._call_tool(
            "replace_symbol",
            {
                "path": "test_lib.py",
                "symbol": "add",
                "text": 'def add(a: int, b: int) -> int:\n    """Add two numbers, now modified."""\n    return a + b + 1',
            },
        )
        self.assertTrue(result.success, f"replace_symbol failed: {result.content}")

        # Verify file was actually modified
        with open(self.test_file) as f:
            content = f.read()
        self.assertIn("now modified", content)
        self.assertIn("return a + b + 1", content)


if __name__ == "__main__":
    unittest.main()
