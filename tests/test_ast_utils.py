"""Tests for core/ast_utils.py — resolve_call_name and get_name."""

from __future__ import annotations

import ast
import unittest

from core.ast_utils import resolve_call_name, get_name


class TestResolveCallName(unittest.TestCase):
    """Test call name resolution from AST Call nodes."""

    def test_simple_name(self):
        tree = ast.parse("foo()")
        call = tree.body[0].value
        self.assertEqual(resolve_call_name(call), "foo")

    def test_attribute_access(self):
        tree = ast.parse("obj.method()")
        call = tree.body[0].value
        self.assertEqual(resolve_call_name(call), "method")

    def test_chained_attribute(self):
        tree = ast.parse("a.b.c()")
        call = tree.body[0].value
        self.assertEqual(resolve_call_name(call), "c")

    def test_subscript_call(self):
        tree = ast.parse("foo[int]()")
        call = tree.body[0].value
        self.assertEqual(resolve_call_name(call), "foo")

    def test_subscript_attribute(self):
        tree = ast.parse("obj.attr[int]()")
        call = tree.body[0].value
        self.assertEqual(resolve_call_name(call), "attr")

    def test_lambda_call_returns_none(self):
        tree = ast.parse("(lambda x: x)()")
        call = tree.body[0].value
        self.assertIsNone(resolve_call_name(call))


class TestGetName(unittest.TestCase):
    """Test best-effort name extraction from arbitrary AST nodes."""

    def test_name_node(self):
        tree = ast.parse("x")
        node = tree.body[0].value
        self.assertEqual(get_name(node), "x")

    def test_attribute_node(self):
        tree = ast.parse("module.Cls")
        node = tree.body[0].value
        self.assertEqual(get_name(node), "module.Cls")

    def test_deep_attribute(self):
        tree = ast.parse("a.b.c.d")
        node = tree.body[0].value
        self.assertEqual(get_name(node), "a.b.c.d")

    def test_subscript_generic(self):
        tree = ast.parse("Generic[T]")
        node = tree.body[0].value
        self.assertEqual(get_name(node), "Generic")

    def test_call_base_class(self):
        tree = ast.parse("BaseClass(args)")
        node = tree.body[0].value
        self.assertEqual(get_name(node), "BaseClass")

    def test_starred_unwrap(self):
        tree = ast.parse("*base")
        node = tree.body[0].value
        self.assertEqual(get_name(node), "base")

    def test_constant_string(self):
        tree = ast.parse('"hello"')
        node = tree.body[0].value
        self.assertEqual(get_name(node), "hello")

    def test_constant_number_returns_none(self):
        tree = ast.parse("42")
        node = tree.body[0].value
        self.assertIsNone(get_name(node))

    def test_none_returns_none(self):
        tree = ast.parse("None")
        node = tree.body[0].value
        self.assertIsNone(get_name(node))

    def test_binop_returns_none(self):
        tree = ast.parse("a + b")
        node = tree.body[0].value
        self.assertIsNone(get_name(node))


if __name__ == "__main__":
    unittest.main()
