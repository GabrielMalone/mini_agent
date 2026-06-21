"""Test that replace_symbol handles non-ASCII characters correctly.

Tree-sitter returns byte offsets into UTF-8 encoded bytes, but replace_symbol
was applying them directly to the Python string. For ASCII files this works
(because 1 char == 1 byte), but any non-ASCII character before the target
symbol causes the byte offset to diverge from the character index.
"""

import os
import tempfile
import unittest

from tools.ast_ops import replace_symbol


class TestReplaceSymbolNonAscii(unittest.TestCase):
    """Test replace_symbol with files containing multibyte UTF-8 characters."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_and_replace(self, content: str, symbol_name: str, new_text: str) -> str:
        path = os.path.join(self.tmpdir, "test_file.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        result = replace_symbol(path, symbol_name, new_text)
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), result

    def test_non_ascii_before_target(self):
        """A non-ASCII character before the target function should not corrupt the splice."""
        content = '# -*- coding: utf-8 -*-\n# Café comment\nx = 1\n\ndef hello():\n    return "world"\n\ndef goodbye():\n    return "farewell"\n'
        new_source, result = self._write_and_replace(content, "goodbye", 'def goodbye():\n    return "adieu"\n')

        self.assertIn("Successfully replaced", result)
        self.assertIn("adieu", new_source)
        self.assertIn("Café", new_source)
        self.assertIn('def hello()', new_source)  # hello should be untouched
        # Both functions should parse
        import ast
        ast.parse(new_source)

    def test_multiple_non_ascii_before_target(self):
        """Multiple multibyte characters should accumulate offset correctly."""
        # 10 multibyte chars before target: each is 2 bytes in UTF-8
        content = '# 北京 東京 Москва İstanbul 🌍\ndef target_func():\n    pass\n'
        new_source, result = self._write_and_replace(
            content, "target_func",
            'def target_func():\n    return 42\n'
        )

        self.assertIn("Successfully replaced", result)
        self.assertIn("return 42", new_source)
        self.assertIn("北京", new_source)
        self.assertIn("🌍", new_source)  # 4-byte emoji
        import ast
        ast.parse(new_source)

    def test_ascii_only_still_works(self):
        """ASCII-only files should still work correctly (regression check)."""
        content = 'def foo():\n    return 1\n\ndef bar():\n    return 2\n'
        new_source, result = self._write_and_replace(
            content, "bar",
            'def bar():\n    return 99\n'
        )

        self.assertIn("Successfully replaced", result)
        self.assertIn("return 99", new_source)
        self.assertIn("return 1", new_source)
        import ast
        ast.parse(new_source)

    def test_method_replacement_with_non_ascii(self):
        """Replacing a method in a class with non-ASCII in the module docstring."""
        content = '"""Module: résumé parser"""\n\nclass Parser:\n    def parse(self):\n        pass\n'
        # NOTE: replace_symbol preserves the whitespace *before* the AST node
        # (the 4-space indent before `def` is part of the class body, not the
        # function_definition node).  So the replacement text must NOT include
        # the parent indentation.
        new_source, result = self._write_and_replace(
            content, "Parser.parse",
            'def parse(self):\n        return "done"\n'
        )

        self.assertIn("Successfully replaced", result)
        self.assertIn('return "done"', new_source)
        self.assertIn("résumé", new_source)
        import ast
        ast.parse(new_source)


if __name__ == "__main__":
    unittest.main()
