"""Lint all Python source files by compiling them.

Catches syntax errors that ``run_tests`` would miss if the broken module
is never imported by a test.
"""

import os
import py_compile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))  # project root, not tests/
SOURCES = [
    "core/llm.py",
    "core/prompt.py",
    "terminal.py",
    "core/config.py",
    "core/safety.py",
    "memory/memory.py",
    "tools/__init__.py",
    "tools/file_ops.py",
    "tools/shell_ops.py",
    "tools/search_ops.py",
]

# Expanded coverage: all critical modules that could silently break
EXTENDED_SOURCES = [
    "core/ast_utils.py",
    "core/anchor_manager.py",
    "core/compaction.py",
    "core/constants.py",
    "core/file_context_tracker.py",
    "core/repair.py",
    "core/semantic_memory.py",
    "core/symbol_context_resolver.py",
    "core/tree_sitter_parser.py",
    "core/knowledge_graph.py",
    "core/prefix.py",
    "memory/memory_prune.py",
    "memory/session.py",
    "memory/observations.py",
    "memory/session_summaries.py",
    "tools/result.py",
    "tools/error_hints.py",
    "tools/failure_learning.py",
    "tools/schema.py",
    "tools/tool_graph.py",
    "tools/skills.py",
    "tools/memory_core.py",
    "tools/memory_consolidation.py",
    "tools/context.py",
    "tools/ast_tools.py",
    "tools/json_repair.py",
    "tools/trajectory.py",
    "api.py",
    "retry.py",
    "stream.py",
    "interject.py",
    "logging_setup.py",
]


class TestLintSources(unittest.TestCase):
    def test_all_sources_compile(self):
        for path in SOURCES:
            full = os.path.join(ROOT, path)
            py_compile.compile(full, doraise=True)

    def test_extended_sources_compile(self):
        """Compile-check the broader module set to catch silent breakage."""
        for path in EXTENDED_SOURCES:
            full = os.path.join(ROOT, path)
            py_compile.compile(full, doraise=True)

    def test_no_circular_imports(self):
        """Import all core modules to catch circular import errors."""
        modules = [
            "core.llm",
            "core.prompt",
            "core.config",
            "core.safety",
            "core.ast_utils",
            "core.anchor_manager",
            "core.file_context_tracker",
            "core.semantic_memory",
            "core.symbol_context_resolver",
            "core.tree_sitter_parser",
            "core.knowledge_graph",
            "memory.memory",
            "memory.memory_prune",
            "memory.session",
            "tools.result",
            "tools.file_ops",
            "tools.shell_ops",
            "tools.search_ops",
            "tools.schema",
        ]
        import importlib
        for mod in modules:
            importlib.import_module(mod)

    def test_tool_dispatch_imports(self):
        """Ensure tools/__init__.py dispatch table loads cleanly."""
        from tools import TOOLS
        self.assertIsInstance(TOOLS, (dict, list))
        self.assertGreater(len(TOOLS), 10)

    def test_all_source_files_exist(self):
        """Verify all listed source files actually exist on disk."""
        all_sources = SOURCES + EXTENDED_SOURCES
        for path in all_sources:
            full = os.path.join(ROOT, path)
            self.assertTrue(os.path.isfile(full), f"Missing: {full}")
