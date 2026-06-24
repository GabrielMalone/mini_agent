#!/usr/bin/env python3
"""workspace_scanner.py -- Single-pass workspace file discovery.

Replaces the multiple redundant ``os.walk`` calls scattered across
knowledge_graph, symbol indexer, semantic indexer, codebase_map, and
search_ast.  Registers file handlers that run during a single traversal.

Usage::

    from core.workspace_scanner import walk_workspace, Handler

    def my_handler(fpath: str, ext: str, root: str) -> None:
        ...

    walk_workspace(".", [Handler(exts=[".py", ".ts"], fn=my_handler)])
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

# Directories always skipped
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".eggs", "*.egg-info",
})

# Extensions treated as source (all others skipped)
_SRC_EXTS: frozenset[str] = frozenset({
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
})


@dataclass
class Handler:
    """A file handler registered for workspace scanning.

    Args:
        exts: File extensions to match (e.g. ``[".py", ".ts"]``).
              If empty, matches all source extensions.
        fn: Callback receiving ``(fpath, ext, root)``.
    """
    exts: list[str] = field(default_factory=list)
    fn: Callable[[str, str, str], None] | None = None


def walk_workspace(
    root: str,
    handlers: list[Handler],
    *,
    skip_dirs: frozenset[str] | None = None,
    src_exts: frozenset[str] | None = None,
) -> int:
    """Walk *root* once, dispatching source files to registered *handlers*.

    Returns the number of files visited.
    """
    skip = skip_dirs if skip_dirs is not None else _SKIP_DIRS
    exts = src_exts if src_exts is not None else _SRC_EXTS

    # Pre-compute handler dispatch: ext -> list of callbacks
    dispatch: dict[str, list[Callable[[str, str, str], None]]] = {}
    for h in handlers:
        if h.fn is None:
            continue
        target_exts = h.exts if h.exts else list(exts)
        for ext in target_exts:
            dispatch.setdefault(ext, []).append(h.fn)

    file_count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]

        for fname in filenames:
            ext = os.path.splitext(fname)[1]
            if ext not in exts:
                continue
            fns = dispatch.get(ext)
            if not fns:
                continue
            fpath = os.path.join(dirpath, fname)
            for fn in fns:
                try:
                    fn(fpath, ext, root)
                except Exception:
                    pass  # One handler failing shouldn't kill the scan
            file_count += 1

    return file_count
