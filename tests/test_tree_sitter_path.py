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
        captures = tsp.run_query(parser.language, tsp._TSX_QUERY, tree.root_node)
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


class TestParserLanguageSelection(unittest.TestCase):
    """_get_tree_sitter_parser must select the correct grammar for each extension.

    Regressions: .tsx was using language_typescript() (unreachable else branch),
    .jsx had no dedicated JS parser. These tests lock in the correct mappings.
    """

    def _parser_lang_for(self, ext: str):
        """Return the Language object from _get_tree_sitter_parser for `ext`."""
        from tools.ast_tools import _get_tree_sitter_parser
        result = _get_tree_sitter_parser(ext)
        self.assertIsNotNone(result, f"No parser returned for {ext}")
        _parser, lang, _query = result
        return lang

    def test_ts_uses_typescript_language(self):
        """Regression: .tsx was also using language_typescript() via unreachable else."""
        import tree_sitter_typescript as tsts
        from tree_sitter import Language
        lang = self._parser_lang_for(".ts")
        expected = Language(tsts.language_typescript())
        self.assertEqual(lang, expected, ".ts must use language_typescript()")

    def test_tsx_uses_tsx_language(self):
        """REGRESSION: .tsx was using language_typescript() instead of language_tsx()."""
        import tree_sitter_typescript as tsts
        from tree_sitter import Language
        lang = self._parser_lang_for(".tsx")
        expected = Language(tsts.language_tsx())
        self.assertEqual(lang, expected, ".tsx must use language_tsx()")

    @unittest.skipUnless(_TS_AVAILABLE, _TS_SKIP)
    def test_js_uses_javascript_language(self):
        """REGRESSION: .js/.jsx were parsed with tree-sitter-typescript (wrong grammar)."""
        import tree_sitter_javascript as tsjs
        from tree_sitter import Language
        lang = self._parser_lang_for(".js")
        expected = Language(tsjs.language())
        self.assertEqual(lang, expected, ".js must use tree-sitter-javascript")

    @unittest.skipUnless(_TS_AVAILABLE, _TS_SKIP)
    def test_jsx_uses_javascript_language(self):
        """REGRESSION: .jsx was using tree-sitter-typescript."""
        import tree_sitter_javascript as tsjs
        from tree_sitter import Language
        lang = self._parser_lang_for(".jsx")
        expected = Language(tsjs.language())
        self.assertEqual(lang, expected, ".jsx must use tree-sitter-javascript")


@unittest.skipUnless(_TS_AVAILABLE, _TS_SKIP)
class TestTsxDefinitionsArrowFunctions(unittest.TestCase):
    """_extract_definitions must find arrow-function consts in TSX via variable_declarator.

    REGRESSION: the TSX query used (arrow_function name: (identifier)?) which
    tree-sitter-typescript does not support. It must use
    (variable_declarator name: ... value: (arrow_function)) instead.
    """

    def setUp(self):
        from tools.ast_tools import _get_tree_sitter_parser
        result = _get_tree_sitter_parser(".tsx")
        self.assertIsNotNone(result)
        self.parser, self.lang, _ = result

    def test_arrow_const_found(self):
        """A `const f = () => {}` arrow function is detected."""
        from tools.ast_tools import _extract_definitions
        source = "const handleClick = () => {\n  console.log('click');\n};\n"
        defs = _extract_definitions(source, self.parser, self.lang, ".tsx")
        names = {d["name"] for d in defs}
        self.assertIn("handleClick", names,
                      "arrow const not found — variable_declarator pattern regression")

    def test_arrow_const_with_type_annotations(self):
        """Arrow with TS type annotations: `const fn = (x: number): string => { ... }`."""
        from tools.ast_tools import _extract_definitions
        source = "const format = (n: number): string => {\n  return n.toString();\n};\n"
        defs = _extract_definitions(source, self.parser, self.lang, ".tsx")
        names = {d["name"] for d in defs}
        self.assertIn("format", names,
                      "annotated arrow const not found — tree-sitter TSX regression")

    def test_function_declaration_still_works(self):
        """Regular `function foo() {}` declarations still detected alongside arrows."""
        from tools.ast_tools import _extract_definitions
        source = (
            "function helper(): void {}\n"
            "const onClick = () => {\n  helper();\n};\n"
        )
        defs = _extract_definitions(source, self.parser, self.lang, ".tsx")
        names = {d["name"] for d in defs}
        self.assertIn("helper", names)
        self.assertIn("onClick", names,
                      "arrow const not found alongside function declaration")


class TestGetFileSkeletonTsxEndToEnd(unittest.TestCase):
    """get_file_skeleton must return results for real .tsx files (not silently empty).

    REGRESSION: TerminalBlock.tsx returned "0 function(s), 0 class(es)" because
    (a) TSX grammar was wrong and (b) variable_declarator pattern was missing.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root)
        self.wg = WriteSafetyGate(self.ws_root)

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        Path(path).write_text(content)
        return path

    @unittest.skipUnless(_TS_AVAILABLE, _TS_SKIP)
    def test_tsx_with_arrow_const(self):
        """A minimal .tsx file with an arrow const returns results, not empty."""
        from tools.ast_tools import _get_file_skeleton
        path = self._write("Component.tsx", (
            "import React from 'react';\n"
            "const MyComponent = () => {\n"
            "  return <div>Hello</div>;\n"
            "};\n"
            "export default MyComponent;\n"
        ))
        r = _get_file_skeleton({"path": path}, self.wg, self.rg)
        self.assertTrue(r.success, r.content)
        self.assertIn("MyComponent", r.content,
                      "arrow const not in skeleton — TSX regression")
        # Summary line must NOT say "0 function(s)"
        self.assertNotIn("0 function(s)", r.content,
                        "0 functions reported — tree-sitter TSX path is broken")

    @unittest.skipUnless(_TS_AVAILABLE, _TS_SKIP)
    def test_tsx_mixed_declarations(self):
        """file with function declaration + arrow const — both detected."""
        from tools.ast_tools import _get_file_skeleton
        path = self._write("Mixed.tsx", (
            "function helper(): string { return 'ok'; }\n"
            "const onClick = () => { helper(); };\n"
        ))
        r = _get_file_skeleton({"path": path}, self.wg, self.rg)
        self.assertTrue(r.success, r.content)
        self.assertIn("helper", r.content)
        self.assertIn("onClick", r.content,
                      "mixed: arrow const not detected alongside function declaration")
        self.assertNotIn("0 function(s)", r.content)

    def test_python_still_works(self):
        """Sanity: Python files still return results (no regression from TSX fix)."""
        from tools.ast_tools import _get_file_skeleton
        path = self._write("mod.py", "def foo():\n    pass\n\nclass Bar:\n    pass\n")
        r = _get_file_skeleton({"path": path}, self.wg, self.rg)
        self.assertTrue(r.success, r.content)
        self.assertIn("foo", r.content)
        self.assertIn("Bar", r.content)


if __name__ == "__main__":
    unittest.main()
