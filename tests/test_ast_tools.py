"""Tests for tools/ast_tools.py."""
from __future__ import annotations
import os
import tempfile
import unittest
from pathlib import Path
from core.safety import ReadSafetyGate, WriteSafetyGate
from tools.ast_tools import _get_file_skeleton, _get_function, _replace_symbol, _rename_symbol
from tools.result import ToolResult

class TestGetFileSkeleton(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root); self.wg = WriteSafetyGate(self.ws_root)
    def test_python_file(self):
        f = os.path.join(self.tmp, 'mod.py')
        Path(f).write_text('def foo():\n    pass\n\ndef bar(x):\n    return x\n')
        r = _get_file_skeleton({'path': f}, self.wg, self.rg)
        self.assertTrue(r.success, r.content); self.assertIn('foo', r.content)
    def test_class(self):
        f = os.path.join(self.tmp, 'mod.py')
        Path(f).write_text('class MyClass:\n    def method(self):\n        pass\n')
        r = _get_file_skeleton({'path': f}, self.wg, self.rg)
        self.assertTrue(r.success, r.content)
    def test_nonexistent(self):
        r = _get_file_skeleton({'path': '/nonexistent/file.py'}, self.wg, self.rg)
        self.assertIsInstance(r, ToolResult)
    def test_with_anchors(self):
        f = os.path.join(self.tmp, 'mod.py')
        Path(f).write_text('def foo():\n    pass\n')
        r = _get_file_skeleton({'path': f, 'include_anchors': True}, self.wg, self.rg)
        self.assertTrue(r.success, r.content)

class TestGetFunction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root); self.wg = WriteSafetyGate(self.ws_root)
        self.mod = os.path.join(self.tmp, 'mod.py')
        Path(self.mod).write_text('def foo():\n    return 1\n\ndef bar(x):\n    return x*2\n\nclass MyClass:\n    def m(self):\n        return 3\n')
    def test_by_name(self):
        r = _get_function({'path': self.mod, 'name': 'foo'}, self.wg, self.rg)
        self.assertTrue(r.success, r.content); self.assertIn('foo', r.content)
    def test_class(self):
        r = _get_function({'path': self.mod, 'name': 'MyClass', 'type': 'class'}, self.wg, self.rg)
        self.assertTrue(r.success, r.content)
    def test_with_anchors(self):
        r = _get_function({'path': self.mod, 'name': 'bar', 'include_anchors': True}, self.wg, self.rg)
        self.assertTrue(r.success, r.content); self.assertIn('bar', r.content)
    def test_nonexistent(self):
        r = _get_function({'path': self.mod, 'name': 'nonexistent'}, self.wg, self.rg)
        self.assertIsInstance(r, ToolResult)

class TestReplaceSymbol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root); self.wg = WriteSafetyGate(self.ws_root)
        self.mod = os.path.join(self.tmp, 'mod.py')
    def test_single(self):
        Path(self.mod).write_text('def old():\n    return 1\n')
        r = _replace_symbol({'path': self.mod, 'symbol': 'old', 'text': 'def new():\n    return 2\n'}, self.wg, self.rg)
        self.assertTrue(r.success, r.content)
    def test_nonexistent(self):
        Path(self.mod).write_text('def real():\n    pass\n')
        r = _replace_symbol({'path': self.mod, 'symbol': 'nonexistent', 'text': 'def x():\n    pass\n'}, self.wg, self.rg)
        self.assertIsInstance(r, ToolResult)
    def test_batch(self):
        Path(self.mod).write_text('def foo():\n    return 1\n\ndef bar():\n    return 2\n')
        r = _replace_symbol({'replacements': [{'path': self.mod, 'symbol': 'foo', 'text': 'def foo2():\n    return 10\n'}, {'path': self.mod, 'symbol': 'bar', 'text': 'def bar2():\n    return 20\n'}]}, self.wg, self.rg)
        self.assertTrue(r.success, r.content)

class TestRenameSymbol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root); self.wg = WriteSafetyGate(self.ws_root)
        self.mod = os.path.join(self.tmp, 'mod.py')
    def test_rename(self):
        Path(self.mod).write_text('def old():\n    pass\n')
        r = _rename_symbol({'path': self.mod, 'symbol': 'old', 'new_name': 'new'}, self.wg, self.rg)
        self.assertIsInstance(r, ToolResult)

if __name__ == '__main__':
    unittest.main()


class TestReplaceSymbolWithDecorators(unittest.TestCase):
    """replace_symbol must include decorators in the replaced byte range."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root)
        self.wg = WriteSafetyGate(self.ws_root)
        self.mod = os.path.join(self.tmp, "mod.py")

    def test_decorated_class_with_decorator_in_replacement(self):
        """Replacing a decorated class should work when decorator is included."""
        Path(self.mod).write_text(
            "@dataclass\nclass Old:\n    x: int = 0\n"
        )
        r = _replace_symbol(
            {
                "path": self.mod,
                "symbol": "Old",
                "text": "@dataclass\nclass New:\n    y: str = 'bar'\n",
            },
            self.wg,
            self.rg,
        )
        self.assertTrue(r.success, r.content)
        content = Path(self.mod).read_text()
        self.assertIn("@dataclass", content)
        self.assertIn("class New:", content)
        self.assertNotIn("class Old:", content)
        # Should NOT have duplicate decorator
        self.assertEqual(content.count("@dataclass"), 1)

    def test_decorated_function_with_decorator_in_replacement(self):
        """Replacing a decorated function should work when decorator is included."""
        Path(self.mod).write_text(
            "@staticmethod\ndef old_func():\n    return 1\n"
        )
        r = _replace_symbol(
            {
                "path": self.mod,
                "symbol": "old_func",
                "text": "@staticmethod\ndef new_func():\n    return 2\n",
            },
            self.wg,
            self.rg,
        )
        self.assertTrue(r.success, r.content)
        content = Path(self.mod).read_text()
        self.assertIn("@staticmethod", content)
        self.assertIn("def new_func():", content)
        self.assertEqual(content.count("@staticmethod"), 1)

    def test_decorated_class_without_decorator_strips_it(self):
        """Without decorator in replacement, decorator is removed (full control)."""
        Path(self.mod).write_text(
            "@property\ndef old_prop(self):\n    return self._x\n"
        )
        r = _replace_symbol(
            {
                "path": self.mod,
                "symbol": "old_prop",
                "text": "def new_prop(self):\n    return self._y\n",
            },
            self.wg,
            self.rg,
        )
        self.assertTrue(r.success, r.content)
        content = Path(self.mod).read_text()
        self.assertNotIn("@property", content)
        self.assertIn("def new_prop(self):", content)
