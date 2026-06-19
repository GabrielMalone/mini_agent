#!/usr/bin/env python3
"""Shared AST helpers used across codebase_map, knowledge_graph, search_ops,
and tree_sitter_parser to avoid duplicated call-name / name resolution logic."""

from __future__ import annotations

import ast as _ast
from typing import Any


def resolve_call_name(node: Any) -> str | None:
    """Resolve a Call node's function name.

    Handles simple names (foo()), attribute access (obj.method()),
    and subscript calls (foo[int]()).
    """
    func = node.func
    if isinstance(func, _ast.Name):
        return func.id
    if isinstance(func, _ast.Attribute):
        return func.attr
    # Subscript: foo[int]() -> "foo"
    if isinstance(func, _ast.Subscript):
        inner = func.value
        if isinstance(inner, _ast.Name):
            return inner.id
        if isinstance(inner, _ast.Attribute):
            return inner.attr
    return None


def get_name(node: Any) -> str | None:
    """Extract a best-effort name from an arbitrary AST node.

    Used as a fallback for ``ast.unparse`` (Python < 3.9) when resolving
    inheritance bases.  Handles:

    * ``Name``          → ``"SomeClass"``
    * ``Attribute``     → ``"module.Cls"``  (joins dotted path)
    * ``Subscript``     → ``"Generic"``     (extracts base, drops ``[T]``)
    * ``Call``          → ``"BaseClass"``   (extracts callable, drops ``(args)``)
    * ``Starred``       → unwraps ``*base``
    * ``Constant``      → ``str(node.value)``  (e.g. string literals in type hints)
    * everything else   → ``None``
    """
    if isinstance(node, _ast.Name):
        return node.id
    if isinstance(node, _ast.Attribute):
        # Reconstruct dotted path: a.b.c
        parts: list[str] = []
        current: Any = node
        while isinstance(current, _ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, _ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    if isinstance(node, _ast.Subscript):
        # Generic[T] → "Generic"
        return get_name(node.value)
    if isinstance(node, _ast.Call):
        # BaseClass(args) → "BaseClass"
        return get_name(node.func)
    if isinstance(node, _ast.Starred):
        return get_name(node.value)
    if isinstance(node, _ast.Constant):
        return str(node.value) if isinstance(node.value, str) else None
    return None
