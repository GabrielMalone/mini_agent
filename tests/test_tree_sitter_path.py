"""
Tests that verify tree-sitter is actually exercised — not silently falling back to regex.

The existing tests only check output correctness. These tests verify the CODE PATH:
tree-sitter must be called and must return real captures. If tree-sitter is broken
but the regex fallback masks it, these tests FAIL.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.safety import ReadSafetyGate, WriteSafetyGate
from core import tree_sitter_parser as tsp

try:
    import tree_sitter_typescript  # noqa: F401
    _TS_AVAILABLE = True
except ImportError:
    _TS_AVAILABLE = False

_TS_SKIP = "tree_sitter_typescript not installed"

TS_GENERIC_ARROW = """\
const handleClick = <T>(event: MouseEvent<T>) => {
    console.log(event);
};
"""

TS_METHOD_CONTENT = """\
class MyService {
    get data(): string {
        return this._data;
    }
}
"""


class TestRunQueryIsCalled(unittest.TestCase):
    """Verify run_query() is invoked for Python (and TS, if available)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        Path(path).write_text(content)
        return path

    def test_python_extract_uses_run_query_not_fallback(self):
        path = self._write("mod.py", "def foo():\n    pass\n\nclass Bar:\n    pass\n")
        with patch.object(tsp, "run_query", wraps=tsp.run_query) as spy:
            with patch.object(tsp, "_extract_with_fallback") as mock_fb:
                result = tsp.extract_symbols(path)
                self.assertIsNotNone(result)
                spy.assert_called_once()
                mock_fb.assert_not_called()

    def test_run_query_returns_non_empty_captures(self):
        parser = tsp._get_parser_for_ext(".py")
        self.assertIsNotNone(parser, "tree-sitter Python parser not available")
        tree = parser.parse(b"def foo():\n    pass\n")
        captures = tsp.run_query(parser.language, tsp._PYTHON_QUERY, tree.root_node)
        self.assertIsInstance(captures, list)
        self.assertGreater(len(captures), 0, "run_query returned empty - tree-sitter silently failing")

    @unittest.skipUnless(_TS_AVAILABLE, _TS_SKIP)
    def test_typescript_extract_uses_run_query_not_fallback(self):
        path = self._write("comp.ts", "function hello(): void {}\nclass World {}\n")
        with patch.object(tsp, "run_query", wraps=tsp.run_query) as spy:
            with patch.object(tsp, "_extract_with_fallback") as mock_fb:
                result = tsp.extract_symbols(path)
                self.assertIsNotNone(result)
                spy.assert_called_once()
                mock_fb.assert_not_called()

    @unittest.skipUnless(_TS_AVAILABLE, _TS_SKIP)
    def test_run_query_returns_non_empty_captures_ts(self):
        parser = tsp._get_parser_for_ext(".ts")
        self.assertIsNotNone(parser, "tree-sitter TS parser not available")
        tree = parser.parse(b"function hello(): void {}")
        captures = tsp.run_query(parser.language, tsp._TS_QUERY, tree.root_node)
        self.assertIsInstance(captures, list)
        self.assertGreater(len(captures), 0, "run_query returned empty - tree-sitter silently failing")


class TestFallbackNotCalled(unittest.TestCase):
    """_extract_with_fallback must not be invoked when tree-sitter is working."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        Path(path).write_text(content)
        return path

    def test_python_fallback_not_called(self):
        path = self._write("lib.py", "def add(a, b):\n    return a + b\n")
        with patch.object(tsp, "_extract_with_fallback") as mock_fb:
            result = tsp.extract_symbols(path)
            defs, calls, imports = result
            self.assertGreater(len(defs), 0)
            mock_fb.assert_not_called()

    @unittest.skipUnless(_TS_AVAILABLE, _TS_SKIP)
    def test_typescript_fallback_not_called(self):
        path = self._write("util.ts", 'export function greet(name: string): string {\n    return "hi";\n}\n')
        with patch.object(tsp, "_extract_with_fallback") as mock_fb:
            result = tsp.extract_symbols(path)
            defs, calls, imports = result
            self.assertGreater(len(defs), 0)
            mock_fb.assert_not_called()


class TestPythonFeaturesRegexCannotMatch(unittest.TestCase):
    """Python constructs that regex misses - tree-sitter must capture them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        Path(path).write_text(content)
        return path

    def _get_def_names(self, filepath):
        result = tsp.extract_symbols(filepath)
        self.assertIsNotNone(result)
        defs, _, _ = result
        return {d["name"] for d in defs}

    def test_async_function_detected(self):
        path = self._write("async_mod.py", "async def fetch_data():\n    pass\n")
        names = self._get_def_names(path)
        self.assertIn("fetch_data", names, "async def not detected - regex cannot match this")

    def test_decorated_function_detected(self):
        path = self._write("dec.py", "@staticmethod\ndef my_method():\n    pass\n")
        names = self._get_def_names(path)
        self.assertIn("my_method", names)


@unittest.skipUnless(_TS_AVAILABLE, _TS_SKIP)
class TestTypeScriptFeaturesRegexCannotMatch(unittest.TestCase):
    """TypeScript constructs that regex misses - tree-sitter must capture them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        Path(path).write_text(content)
        return path

    def _get_def_names(self, filepath):
        result = tsp.extract_symbols(filepath)
        self.assertIsNotNone(result)
        defs, _, _ = result
        return {d["name"] for d in defs}

    def test_generic_arrow_function_detected(self):
        path = self._write("generic.ts", TS_GENERIC_ARROW)
        names = self._get_def_names(path)
        self.assertIn("handleClick", names, "Generic arrow not detected - tree-sitter broken")

    def test_method_definition_detected(self):
        path = self._write("service.ts", TS_METHOD_CONTENT)
        names = self._get_def_names(path)
        self.assertIn("data", names, "Method 'data' not detected - tree-sitter broken")


class TestAstToolsUsesTreeSitter(unittest.TestCase):
    """get_file_skeleton / get_function must call run_query, not regex."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root)
        self.wg = WriteSafetyGate(self.ws_root)

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        Path(path).write_text(content)
        return path

    def test_get_file_skeleton_calls_run_query(self):
        from tools import ast_tools as at
        path = self._write("mod.py", "def foo():\n    pass\n\nclass Bar:\n    def m(self):\n        pass\n")
        with patch.object(at, "run_query", wraps=at.run_query) as spy:
            r = at._get_file_skeleton({"path": path}, self.wg, self.rg)
            self.assertTrue(r.success, r.content)
            spy.assert_called()

    def test_get_function_calls_run_query(self):
        from tools import ast_tools as at
        path = self._write("mod.py", "def foo():\n    return 1\n\ndef bar():\n    return 2\n")
        with patch.object(at, "run_query", wraps=at.run_query) as spy:
            r = at._get_function({"path": path, "name": "foo"}, self.wg, self.rg)
            self.assertTrue(r.success, r.content)
            spy.assert_called()

    @unittest.skipUnless(_TS_AVAILABLE, _TS_SKIP)
    def test_get_file_skeleton_calls_run_query_ts(self):
        from tools import ast_tools as at
        path = self._write("comp.ts", "function hello(): void {}\nclass World {}\n")
        with patch.object(at, "run_query", wraps=at.run_query) as spy:
            r = at._get_file_skeleton({"path": path}, self.wg, self.rg)
            self.assertTrue(r.success, r.content)
            spy.assert_called()


class TestFallbackRegexGaps(unittest.TestCase):
    """Document and verify real gaps where regex fallback fails."""

    def test_python_regex_misses_async_def(self):
        from core.tree_sitter_parser import _PY_DEF_RE
        line = "async def fetch_data():"
        self.assertIsNone(_PY_DEF_RE.match(line),
                          "_PY_DEF_RE now matches async def - regex improved, update test")

    def test_python_regex_misses_decorated_def(self):
        """_PY_DEF_RE sees the 'def' line but misses its decorator line above."""
        from core.tree_sitter_parser import _PY_DEF_RE
        # Regex correctly matches 'def my_method'
        m = _PY_DEF_RE.match("def my_method():")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "my_method")
        # But it silently ignores @decorator on the line above
        m2 = _PY_DEF_RE.match("@staticmethod")
        self.assertIsNone(m2, "Regex ignores decorator lines - decorator info is lost")

    def test_typescript_regex_misses_generic_arrow(self):
        from core.tree_sitter_parser import _TS_ARROW_RE
        line = "const handleClick = <T>(event: MouseEvent<T>) => {"
        self.assertIsNone(_TS_ARROW_RE.match(line),
                          "_TS_ARROW_RE now matches generic arrows - regex improved, update test")


if __name__ == "__main__":
    unittest.main()
