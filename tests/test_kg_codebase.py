#!/usr/bin/env python3
"""Unit tests for knowledge_graph.py and codebase_map.py.

IMPORTANT: All tests use 'import core.knowledge_graph as kg' (NOT 'from ... import _GRAPH')
because setUp replaces the module-level _GRAPH dict.  A from-import would capture a stale
reference to the old dict, making tests see the wrong state.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestKnowledgeGraphDataStructures(unittest.TestCase):
    """Test Edge and Entity dataclasses."""

    def test_edge_creation(self):
        from core.knowledge_graph import Edge
        e = Edge(source="foo", target="bar", kind="calls", filepath="a.py", line=10)
        self.assertEqual(e.source, "foo")
        self.assertEqual(e.target, "bar")
        self.assertEqual(e.kind, "calls")
        self.assertEqual(e.filepath, "a.py")
        self.assertEqual(e.line, 10)

    def test_edge_defaults(self):
        from core.knowledge_graph import Edge
        e = Edge(source="a", target="b", kind="imports")
        self.assertIsNone(e.filepath)
        self.assertIsNone(e.line)

    def test_entity_creation(self):
        from core.knowledge_graph import Entity
        ent = Entity(name="my_func", kind="def", filepath="mod.py", line=42)
        self.assertEqual(ent.name, "my_func")
        self.assertEqual(ent.kind, "def")
        self.assertEqual(ent.filepath, "mod.py")
        self.assertEqual(ent.line, 42)
        self.assertEqual(ent.edges_out, [])
        self.assertEqual(ent.edges_in, [])

    def test_entity_edges_are_independent(self):
        from core.knowledge_graph import Entity, Edge
        ent = Entity(name="X", kind="class")
        e1 = Edge("X", "Y", "calls")
        e2 = Edge("Z", "X", "inherits")
        ent.edges_out.append(e1)
        ent.edges_in.append(e2)
        self.assertEqual(len(ent.edges_out), 1)
        self.assertEqual(len(ent.edges_in), 1)
        self.assertEqual(ent.edges_out[0].target, "Y")
        self.assertEqual(ent.edges_in[0].source, "Z")


class TestKnowledgeGraphBuild(unittest.TestCase):
    """Test building the knowledge graph from real Python files.

    Uses module-level access (kg._GRAPH, not from-import) to avoid stale references.
    """

    def setUp(self):
        import core.knowledge_graph as kg
        self._kg = kg
        self._orig_graph = dict(kg._GRAPH)
        self._orig_built = kg._GRAPH_BUILT
        self._orig_workspace = kg._GRAPH_WORKSPACE
        kg._GRAPH.clear()
        kg._GRAPH_BUILT = False
        kg._GRAPH_WORKSPACE = ""
        self.root = tempfile.mkdtemp()
        src = os.path.join(self.root, "mylib")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "core.py"), "w") as f:
            f.write(
                "def helper(x):\n"
                "    return x * 2\n\n"
                "def process(data):\n"
                "    result = helper(data)\n"
                "    return result\n\n"
                "class Worker:\n"
                "    def run(self):\n"
                "        return process(10)\n"
            )
        with open(os.path.join(src, "main.py"), "w") as f:
            f.write(
                "from mylib.core import helper, Worker\n\n"
                "def main():\n"
                "    w = Worker()\n"
                "    return helper(5)\n"
            )

    def tearDown(self):
        kg = self._kg
        kg._GRAPH = self._orig_graph
        kg._GRAPH_BUILT = self._orig_built
        kg._GRAPH_WORKSPACE = self._orig_workspace

    def test_build_creates_entities(self):
        kg = self._kg
        kg.build_knowledge_graph(self.root)
        self.assertTrue(len(kg._GRAPH) > 0, "Graph should have entities after build")

    def test_build_finds_function_definitions(self):
        kg = self._kg
        kg.build_knowledge_graph(self.root)
        found = {name for name in kg._GRAPH if "helper" in name or "process" in name or "Worker" in name}
        self.assertTrue(len(found) > 0, f"Should find helper/process/Worker, got: {found}")

    def test_build_adds_module_entities(self):
        kg = self._kg
        kg.build_knowledge_graph(self.root)
        modules = [e for e in kg._GRAPH.values() if e.kind == "module"]
        self.assertTrue(len(modules) >= 2, f"Expected >= 2 modules, got {len(modules)}")

    def test_build_is_idempotent(self):
        kg = self._kg
        kg.build_knowledge_graph(self.root)
        count1 = len(kg._GRAPH)
        kg.build_knowledge_graph(self.root)
        count2 = len(kg._GRAPH)
        self.assertEqual(count1, count2, "Second build should be a no-op")

    def test_build_handles_empty_directory(self):
        kg = self._kg
        empty = tempfile.mkdtemp()
        kg.build_knowledge_graph(empty)
        self.assertTrue(isinstance(kg._GRAPH, dict))

    def test_invalidate_file_removes_edges(self):
        kg = self._kg
        kg.build_knowledge_graph(self.root)
        core_path = os.path.join(self.root, "mylib", "core.py")
        kg.invalidate_file(core_path, self.root)
        # Mark not built so next build actually runs
        kg._GRAPH_BUILT = False
        kg.build_knowledge_graph(self.root)
        self.assertTrue(len(kg._GRAPH) > 0)

    def test_add_edge_creates_nodes(self):
        kg = self._kg
        kg._GRAPH.clear()
        kg._add_edge("A", "B", "calls", "test.py", 1)
        self.assertIn("A", kg._GRAPH)
        self.assertIn("B", kg._GRAPH)
        self.assertEqual(kg._GRAPH["A"].edges_out[0].target, "B")
        self.assertEqual(kg._GRAPH["B"].edges_in[0].source, "A")

    def test_add_edge_kind_is_preserved(self):
        kg = self._kg
        kg._GRAPH.clear()
        kg._add_edge("X", "Y", "inherits", "mod.py", 5)
        self.assertEqual(kg._GRAPH["X"].edges_out[0].kind, "inherits")

    def test_skip_call_names_not_added(self):
        kg = self._kg
        kg._GRAPH.clear()
        source = "def foo():\n    print('hello')\n    len([1,2])\n"
        kg._extract_python_graph(source, "test.py", "test")
        builtins = {"print", "len"}
        edges_to_builtins = []
        for ent in kg._GRAPH.values():
            for e in ent.edges_out:
                if e.target in builtins:
                    edges_to_builtins.append(e)
        self.assertEqual(len(edges_to_builtins), 0,
                         f"Builtins should not create call edges: {edges_to_builtins}")

    def test_python_graph_extracts_class_methods(self):
        kg = self._kg
        kg._GRAPH.clear()
        source = (
            "class MyClass:\n"
            "    def method1(self):\n"
            "        pass\n"
            "    def method2(self):\n"
            "        self.method1()\n"
        )
        kg._extract_python_graph(source, "test.py", "test")
        names = set(kg._GRAPH.keys())
        self.assertIn("MyClass", names)
        has_method = any("method1" in n or "method2" in n for n in names)
        self.assertTrue(has_method, f"No methods found in {names}")


class TestCodebaseMapDataStructures(unittest.TestCase):
    """Test FileSymbols and ModuleGroup dataclasses."""

    def test_file_symbols_creation(self):
        from core.codebase_map import FileSymbols
        fs = FileSymbols(
            path="src/main.py",
            classes=["MyClass"],
            functions=["main", "helper"],
            imports_internal=["src.utils"],
            imports_external=["os", "json"],
            has_main=True,
            is_test=False,
            line_count=42,
        )
        self.assertEqual(fs.path, "src/main.py")
        self.assertEqual(fs.classes, ["MyClass"])
        self.assertEqual(fs.functions, ["main", "helper"])
        self.assertEqual(fs.imports_internal, ["src.utils"])
        self.assertTrue(fs.has_main)
        self.assertFalse(fs.is_test)
        self.assertEqual(fs.line_count, 42)

    def test_file_symbols_defaults(self):
        from core.codebase_map import FileSymbols
        fs = FileSymbols(path="x.py")
        self.assertEqual(fs.classes, [])
        self.assertEqual(fs.functions, [])
        self.assertFalse(fs.has_main)
        self.assertFalse(fs.is_test)
        self.assertEqual(fs.line_count, 0)

    def test_module_group_creation(self):
        from core.codebase_map import ModuleGroup, FileSymbols
        fs = FileSymbols(path="a/b.py", functions=["f1"])
        mg = ModuleGroup(prefix="a", files=[fs])
        self.assertEqual(mg.prefix, "a")
        self.assertEqual(len(mg.files), 1)
        self.assertEqual(mg.files[0].functions, ["f1"])


class TestCodebaseMapExtraction(unittest.TestCase):
    """Test Python symbol extraction."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        pkg = os.path.join(self.root, "mypkg")
        os.makedirs(pkg, exist_ok=True)
        with open(os.path.join(pkg, "__init__.py"), "w") as f:
            f.write("# package init\n")
        with open(os.path.join(pkg, "core.py"), "w") as f:
            f.write(
                "import os\n"
                "import json\n"
                "from mypkg.utils import helper\n\n"
                "DEFAULT_TIMEOUT = 30\n\n"
                "def process(items):\n"
                "    return [helper(i) for i in items]\n\n"
                "class Engine:\n"
                "    def start(self):\n"
                "        pass\n\n"
                "if __name__ == '__main__':\n"
                "    process([1, 2])\n"
            )
        with open(os.path.join(pkg, "utils.py"), "w") as f:
            f.write("def helper(x):\n    return x * 2\n")

    def test_extract_python_symbols_finds_functions(self):
        from core.codebase_map import _extract_python_symbols
        filepath = os.path.join(self.root, "mypkg", "core.py")
        fs = _extract_python_symbols(filepath, "mypkg/core.py", {"mypkg"})
        self.assertIsNotNone(fs)
        self.assertIn("process", fs.functions)
        self.assertIn("Engine", fs.classes)

    def test_extract_python_symbols_finds_imports(self):
        from core.codebase_map import _extract_python_symbols
        filepath = os.path.join(self.root, "mypkg", "core.py")
        fs = _extract_python_symbols(filepath, "mypkg/core.py", {"mypkg"})
        self.assertIsNotNone(fs)
        self.assertTrue(any("os" in imp or "json" in imp for imp in fs.imports_external))
        self.assertTrue(any("mypkg" in imp for imp in fs.imports_internal))

    def test_extract_python_symbols_returns_none_for_missing(self):
        from core.codebase_map import _extract_python_symbols
        fs = _extract_python_symbols("/nonexistent/path.py", "path.py", set())
        self.assertIsNone(fs)

    def test_build_codebase_map(self):
        from core.codebase_map import build_codebase_map
        result = build_codebase_map(self.root)
        self.assertIsNotNone(result)
        self.assertTrue(len(result) > 0)

    def test_build_codebase_map_handles_empty(self):
        from core.codebase_map import build_codebase_map
        empty = tempfile.mkdtemp()
        result = build_codebase_map(empty)
        self.assertIsNotNone(result)

    def test_is_internal_import(self):
        from core.codebase_map import _is_internal_import
        self.assertTrue(_is_internal_import("mypkg.core", {"mypkg"}))
        self.assertTrue(_is_internal_import("mypkg", {"mypkg"}))
        self.assertFalse(_is_internal_import("os", {"mypkg"}))
        self.assertFalse(_is_internal_import("", {"mypkg"}))
        self.assertFalse(_is_internal_import("numpy", set()))

    def test_map_cache_populated_after_build(self):
        import core.codebase_map as cm
        with cm._MAP_CACHE_LOCK:
            cm._MAP_CACHE.clear()
        cm.build_codebase_map(self.root)
        with cm._MAP_CACHE_LOCK:
            self.assertTrue(len(cm._MAP_CACHE) > 0,
                            f"Cache should be populated after build, got {len(cm._MAP_CACHE)} entries")


if __name__ == "__main__":
    unittest.main()
