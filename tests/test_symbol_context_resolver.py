"""Tests for core/symbol_context_resolver.py — query config and context resolution."""

from __future__ import annotations

import unittest

from core.symbol_context_resolver import (
    _get_query_config,
    _get_used_identifiers,
)


class TestGetQueryConfig(unittest.TestCase):
    """Test language query config lookup."""

    def test_python_config(self):
        config = _get_query_config(".py")
        self.assertIsNotNone(config)
        self.assertIn("context_query", config)
        self.assertIn("import_capture_name", config)
        self.assertIn("class_capture_name", config)

    def test_typescript_config(self):
        config = _get_query_config(".ts")
        self.assertIsNotNone(config)
        self.assertIn("context_query", config)

    def test_tsx_config(self):
        config = _get_query_config(".tsx")
        self.assertIsNotNone(config)

    def test_javascript_config(self):
        config = _get_query_config(".js")
        self.assertIsNotNone(config)

    def test_jsx_config(self):
        config = _get_query_config(".jsx")
        self.assertIsNotNone(config)

    def test_mjs_config(self):
        config = _get_query_config(".mjs")
        self.assertIsNotNone(config)

    def test_cjs_config(self):
        config = _get_query_config(".cjs")
        self.assertIsNotNone(config)

    def test_unknown_extension(self):
        config = _get_query_config(".rb")
        self.assertIsNone(config)

    def test_case_insensitive(self):
        config = _get_query_config(".PY")
        self.assertIsNotNone(config)
        self.assertIn("context_query", config)

    def test_no_dot_prefix(self):
        config = _get_query_config("py")
        self.assertIsNone(config)


class TestGetUsedIdentifiers(unittest.TestCase):
    """Test identifier extraction from tree-sitter nodes."""

    def setUp(self):
        """Skip tests if tree-sitter is not available."""
        try:
            from core.tree_sitter_parser import _get_parser_for_ext
            self.parser = _get_parser_for_ext(".py")
        except (ImportError, Exception):
            self.parser = None

    def test_extracts_identifiers_from_python_function(self):
        if self.parser is None:
            self.skipTest("tree-sitter not available")
        from core.tree_sitter_parser import run_query
        code = b"def foo(a, b):\n    return a + b\n"
        tree = self.parser.parse(code)
        # Find the function_definition node
        fn_node = tree.root_node.child(0)
        ids = _get_used_identifiers(fn_node)
        self.assertIn("foo", ids)
        self.assertIn("a", ids)
        self.assertIn("b", ids)

    def test_extracts_identifiers_from_call(self):
        if self.parser is None:
            self.skipTest("tree-sitter not available")
        code = b"result = some_function(arg1, arg2)\n"
        tree = self.parser.parse(code)
        root_node = tree.root_node
        ids = _get_used_identifiers(root_node)
        self.assertIn("result", ids)
        self.assertIn("some_function", ids)
        self.assertIn("arg1", ids)
        self.assertIn("arg2", ids)

    def test_empty_node_returns_empty_set(self):
        # Mock a node with no children and no identifier type
        class MockNode:
            type = "module"
            child_count = 0
            def child(self, i):
                return None
        node = MockNode()
        ids = _get_used_identifiers(node)
        self.assertEqual(ids, set())

    def test_bytes_text_decoded(self):
        class MockChild:
            type = "identifier"
            text = b"my_var"
            child_count = 0
            def child(self, i):
                return None
        class MockNode:
            type = "expression_statement"
            child_count = 1
            def child(self, i):
                return MockChild()
        node = MockNode()
        ids = _get_used_identifiers(node)
        self.assertIn("my_var", ids)


if __name__ == "__main__":
    unittest.main()
