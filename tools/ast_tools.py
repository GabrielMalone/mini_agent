#!/usr/bin/env python3
"""
ast_tools.py -- AST-based file tools inspired by Dirac.

Tools:
    get_file_skeleton  -- structural outline with anchors (strips implementation)
    get_function       -- retrieve specific function bodies with anchors
    replace_symbol     -- replace AST nodes (functions/classes) by byte range
"""

from __future__ import annotations

import os
from typing import Any

from tools.result import ToolResult
from tools import _register, _summarize
from tools._file_utils import _FILE_CACHE
from core.file_context_tracker import get_tracker
from core.safety import WriteSafetyGate, ReadSafetyGate


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_tree_sitter_parser(ext: str) -> tuple[Any, Any, Any] | None:
    """Load tree-sitter parser + language + query for extension.

    Returns (parser, language, query_fn) or None if not available.
    """
    try:
        from tree_sitter import Language, Parser
    except ImportError:
        return None

    if ext in (".py",):
        try:
            import tree_sitter_python as tsp
            lang = Language(tsp.language())
        except (ImportError, AttributeError, OSError):
            return None
    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        try:
            import tree_sitter_typescript as tsts
            if ext in (".ts", ".tsx"):
                lang = Language(tsts.language_typescript())
            else:
                lang = Language(tsts.language_tsx() if ext == ".tsx" else tsts.language_typescript())
        except (ImportError, AttributeError, OSError):
            return None
    else:
        return None

    parser = Parser(lang)
    return (parser, lang, None)


def _extract_definitions(
    source: str, parser: Any, lang: Any, ext: str,
) -> list[dict]:
    """Extract top-level and nested definitions with line ranges.

    Returns list of {kind, name, start_line, end_line, start_byte, end_byte}
    """
    tree = parser.parse(source.encode("utf-8"))

    if ext == ".py":
        query_str = """
        (function_definition
            name: (identifier) @function.name
        ) @function.def
        (class_definition
            name: (identifier) @class.name
        ) @class.def
        """
    else:
        query_str = """
        (function_declaration
            name: (identifier) @function.name
        ) @function.def
        (method_definition
            name: (property_identifier) @function.name
        ) @function.def
        (class_declaration
            name: (identifier) @class.name
        ) @class.def
        (arrow_function
            name: (identifier)? @function.name
        ) @function.def
        """

    try:
        query = lang.query(query_str)
    except Exception:
        return _extract_with_regex(source, ext)

    captures = query.captures(tree.root_node)

    definitions: list[dict] = []
    seen: set[str] = set()

    # tree-sitter v0.23+ returns dict[str, list[Node]]; v0.22 returns list[tuple[Node, str]]
    if isinstance(captures, dict):
        items: list[tuple[Any, str]] = [
            (node, tag)
            for tag, nodes in captures.items()
            for node in nodes
        ]
    else:
        items = captures

    for node, tag in items:
        if tag in ("function.def", "class.def"):
            name_node = None
            for child in node.children:
                if child.type in ("identifier", "property_identifier"):
                    name_node = child
                    break

            if not name_node:
                continue

            name = name_node.text.decode("utf-8") if name_node.text else ""
            if not name:
                continue

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            start_byte = node.start_byte
            end_byte = node.end_byte

            key = f"{start_line}:{name}"
            if key in seen:
                continue
            seen.add(key)

            kind = "class" if "class" in tag else "def"
            definitions.append({
                "kind": kind,
                "name": name,
                "start_line": start_line,
                "end_line": end_line,
                "start_byte": start_byte,
                "end_byte": end_byte,
            })

    if not definitions:
        return _extract_with_regex(source, ext)

    return definitions


def _extract_with_regex(source: str, ext: str) -> list[dict]:
    """Fallback regex-based definition extraction."""
    import re

    definitions: list[dict] = []
    lines = source.split("\n")

    if ext == ".py":
        pattern = re.compile(
            r"^\s*(?:async\s+)?(def|class)\s+(\w+)",
            re.MULTILINE,
        )
    else:
        pattern = re.compile(
            r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+(\w+)",
            re.MULTILINE,
        )

    for match in pattern.finditer(source):
        if ext == ".py":
            kind = match.group(1)
            name = match.group(2)
        else:
            kind = "function"
            name = match.group(1)

        start_line = source[:match.start()].count("\n") + 1

        # Find end line by counting braces or indentation
        end_line = start_line  # simplified
        definitions.append({
            "kind": kind,
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
            "start_byte": match.start(),
            "end_byte": match.end(),
        })

    return definitions


def _format_skeleton(
    definitions: list[dict],
    lines: list[str],
    anchors: list[str],
    include_anchors: bool,
) -> str:
    """Format definitions as a structural skeleton."""
    if not definitions:
        return "(no definitions found)"

    from core.anchor_manager import format_line_for_model

    output: list[str] = []
    last_end = -1

    for d in definitions:
        start = d["start_line"] - 1
        end = d["end_line"] - 1

        # Single representative line
        if start < len(lines):
            line = lines[start]
            anchor = anchors[start] if start < len(anchors) else f"L{start+1}"
            formatted = format_line_for_model(line, anchor, include_anchors)
            output.append(formatted)

        # If there are skipped lines between definitions, note it
        if last_end >= 0 and start > last_end + 1:
            skipped = start - last_end - 1
            if skipped > 0:
                # Find the last anchor of previous block and first of next
                if output:
                    output[-1] = output[-1] + f"  # ... ({skipped} lines omitted) ..."

        last_end = end

    return "\n".join(output)


# ---------------------------------------------------------------------------
# get_file_skeleton
# ---------------------------------------------------------------------------

@_register("get_file_skeleton")
def _get_file_skeleton(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    """Read the structural outline of files by extracting class/function definitions.

    Args:
        paths: list of file paths
        include_anchors: if True, prefix each line with its stable anchor word
    """
    from core.anchor_manager import AnchorStateManager

    paths = args.get("paths", [args.get("path", "")])
    if isinstance(paths, str):
        paths = [paths]

    include_anchors = args.get("include_anchors", False)

    results: list[str] = []

    for path in paths:
        safety_result = rg.check(path)
        if not safety_result.allowed:
            results.append(f"--- {path} ---\nBlocked: {safety_result.reason}")
            continue

        resolved = safety_result.resolved_path

        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            results.append(f"--- {path} ---\nError: {e}")
            continue

        ext = os.path.splitext(path)[1].lower()
        ts_result = _get_tree_sitter_parser(ext)

        if ts_result:
            parser, lang, _ = ts_result
            definitions = _extract_definitions(content, parser, lang, ext)
        else:
            definitions = _extract_with_regex(content, ext)

        lines = content.split("\n")
        anchors = AnchorStateManager.reconcile(resolved, lines)

        skeleton = _format_skeleton(definitions, lines, anchors, include_anchors)

        def_count = sum(1 for d in definitions if d["kind"] == "def")
        class_count = sum(1 for d in definitions if d["kind"] == "class")
        summary = f"{def_count} function(s), {class_count} class(es)"

        results.append(
            f"--- {path} [{summary}] ---\n{skeleton}"
        )

    return ToolResult(success=True, content="\n\n".join(results))


@_summarize("get_file_skeleton")
def _get_file_skeleton_summary(args: dict) -> str:
    paths = args.get("paths", [args.get("path", "?")])
    if isinstance(paths, list):
        return f"get_file_skeleton(paths={paths})"
    return f"get_file_skeleton({paths})"


# ---------------------------------------------------------------------------
# get_function
# ---------------------------------------------------------------------------

@_register("get_function")
def _get_function(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    """Retrieve specific function/class bodies with stable anchors.

    Args:
        path: file path
        names: list of function/class names to retrieve
        include_anchors: if True, prefix lines with anchors
    """
    from core.anchor_manager import AnchorStateManager, format_line_for_model

    path = args.get("path", "")
    names = args.get("names", [args.get("name", "")])
    if isinstance(names, str):
        names = [names]
    include_anchors = args.get("include_anchors", False)

    if not names:
        return ToolResult(success=False, content="'names' is required (list of function/class names)")

    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(success=False, content=f"Read blocked: {safety_result.reason}")

    resolved = safety_result.resolved_path

    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return ToolResult(success=False, content=f"Error reading '{path}': {e}")

    ext = os.path.splitext(path)[1].lower()
    ts_result = _get_tree_sitter_parser(ext)

    if ts_result:
        parser, lang, _ = ts_result
        definitions = _extract_definitions(content, parser, lang, ext)
    else:
        definitions = _extract_with_regex(content, ext)

    lines = content.split("\n")
    anchors = AnchorStateManager.reconcile(resolved, lines)

    results: list[str] = []
    found: set[str] = set()

    for d in definitions:
        if d["name"] in names:
            start = d["start_line"] - 1
            end = d["end_line"]
            body_lines = lines[start:end]
            body_anchors = anchors[start:end]

            formatted = "\n".join(
                format_line_for_model(line, body_anchors[i] if i < len(body_anchors) else f"L{start+i+1}", include_anchors)
                for i, line in enumerate(body_lines)
            )

            results.append(
                f"--- {path}::{d['name']} ({d['kind']}, lines {d['start_line']}-{d['end_line']}) ---\n{formatted}"
            )
            found.add(d["name"])

    missing = [n for n in names if n not in found]
    if missing:
        results.append(f"Not found: {', '.join(missing)}")

    return ToolResult(success=True, content="\n\n".join(results))


@_summarize("get_function")
def _get_function_summary(args: dict) -> str:
    names = args.get("names", [args.get("name", "?")])
    return f"get_function({args.get('path', '?')}, names={names})"


# ---------------------------------------------------------------------------
# replace_symbol
# ---------------------------------------------------------------------------

@_register("replace_symbol")
def _replace_symbol(args: dict, wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Replace one or more symbols (functions/classes) by their AST byte range.

    Much more reliable than string matching -- uses tree-sitter to locate
    the exact AST node, then replaces its byte range with the provided text.

    Args:
        replacements: list of {path, symbol, text, type?}
          or single: {path, symbol, text, type?}
    """
    replacements = args.get("replacements", None)
    if replacements is None:
        # Single replacement mode
        path = args.get("path", "")
        symbol = args.get("symbol", "")
        text = args.get("text", "")
        if not path or not symbol:
            return ToolResult(success=False, content="'path' and 'symbol' are required")
        replacements = [{"path": path, "symbol": symbol, "text": text, "type": args.get("type")}]

    if not isinstance(replacements, list) or not replacements:
        return ToolResult(success=False, content="'replacements' must be a non-empty array")

    from tools import add_modified_file, clear_tool_cache

    results: list[str] = []

    for r in replacements:
        path = r.get("path", "")
        symbol = r.get("symbol", "")
        text = r.get("text", "")
        symbol_type = r.get("type")

        safety_result = wg.check(path)
        if not safety_result.allowed:
            results.append(f"[FAIL] {path}: blocked: {safety_result.reason}")
            continue

        resolved = safety_result.resolved_path

        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            results.append(f"[FAIL] {path}: read error: {e}")
            continue

        ext = os.path.splitext(path)[1].lower()
        ts_result = _get_tree_sitter_parser(ext)
        if not ts_result:
            results.append(f"[FAIL] {path}: tree-sitter not available for {ext}")
            continue

        parser, lang, _ = ts_result
        definitions = _extract_definitions(content, parser, lang, ext)

        # Find matching definition
        match = None
        for d in definitions:
            if d["name"] == symbol:
                if symbol_type and d["kind"] != symbol_type:
                    continue
                match = d
                break

        if not match:
            results.append(
                f"[FAIL] {path}: symbol '{symbol}' not found. "
                f"Available: {[d['name'] for d in definitions]}"
            )
            continue

        # Replace byte range
        new_content = (
            content[:match["start_byte"]] +
            text +
            content[match["end_byte"]:]
        )

        # Backup and write
        from tools.file_ops import _backup_before_write
        _backup_before_write(resolved)

        try:
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            results.append(f"[FAIL] {path}: write error: {e}")
            continue

        add_modified_file(resolved)
        clear_tool_cache()
        _FILE_CACHE.pop(resolved, None)
        get_tracker().mark_file_edited(resolved)

        # Keep symbol index fresh for edited .py files
        if resolved.endswith(".py"):
            try:
                from tools.search_ops import _reindex_file
                _reindex_file(resolved, wg.workspace_root)
            except Exception:
                pass

        # Keep knowledge graph fresh
        try:
            from core.knowledge_graph import invalidate_file
            invalidate_file(resolved, wg.workspace_root)
        except Exception:
            pass

        # Reconcile anchors
        from core.anchor_manager import AnchorStateManager
        AnchorStateManager.reconcile(resolved, new_content.split("\n"))

        results.append(
            f"[OK] {path}: replaced '{symbol}' ({match['kind']}, "
            f"lines {match['start_line']}-{match['end_line']})"
        )

    return ToolResult(success=True, content="\n".join(results))


@_summarize("replace_symbol")
def _replace_symbol_summary(args: dict) -> str:
    replacements = args.get("replacements", None)
    if replacements:
        return f"replace_symbol({len(replacements)} replacement(s))"
    return f"replace_symbol({args.get('path', '?')}, {args.get('symbol', '?')})"


# ---------------------------------------------------------------------------
# rename_symbol
# ---------------------------------------------------------------------------


@_register("rename_symbol")
def _rename_symbol(args: dict, wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Rename ALL occurrences of a symbol inside specified files or directories.

    Uses tree-sitter AST to identify symbols precisely -- more accurate than
    a simple search-and-replace because it understands Python/TypeScript
    language structure.

    Args:
        existing_symbol: Exact name of the symbol to rename.
        new_symbol: New name for the symbol.
        paths: Array of file or directory paths to rename in.
    """
    from tools.ast_ops import rename_symbol
    from tools import add_modified_file, clear_tool_cache

    existing_symbol = args.get("existing_symbol", "")
    new_symbol = args.get("new_symbol", "")

    if not existing_symbol or not new_symbol:
        return ToolResult(
            success=False,
            content="Both 'existing_symbol' and 'new_symbol' are required.",
        )

    paths = args.get("paths", [])
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        return ToolResult(
            success=False,
            content="'paths' must be a non-empty array of file or directory paths.",
        )

    # Expand directories to files
    expanded_files: list[str] = []
    for p in paths:
        # Safety check
        safety_result = wg.check(p)
        resolved = safety_result.resolved_path
        if os.path.isdir(resolved):
            for root, _dirs, files in os.walk(resolved):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                        expanded_files.append(os.path.join(root, f))
        elif os.path.isfile(resolved):
            ext = os.path.splitext(resolved)[1].lower()
            if ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                expanded_files.append(resolved)

    if not expanded_files:
        return ToolResult(
            success=False,
            content=f"No source files found in: {paths}",
        )

    results: list[str] = []
    total_files = 0
    total_occurrences = 0
    total_renamed = 0

    for file_path in expanded_files:
        # Safety check
        safety_result = wg.check(file_path)
        if not safety_result.allowed:
            results.append(f"[SKIP] {file_path}: blocked: {safety_result.reason}")
            continue

        try:
            renamed_count, found_count = rename_symbol(
                file_path, existing_symbol, new_symbol
            )
        except Exception as e:
            results.append(f"[FAIL] {file_path}: error: {e}")
            continue

        if found_count > 0:
            total_files += 1
            total_occurrences += found_count
            total_renamed += renamed_count
            add_modified_file(file_path)
            results.append(
                f"[OK] {file_path}: renamed {renamed_count} of {found_count} "
                f"occurrence(s) of '{existing_symbol}' -> '{new_symbol}'"
            )
        else:
            results.append(f"[--] {file_path}: no occurrences of '{existing_symbol}'")

    clear_tool_cache()

    summary = (
        f"Renamed '{existing_symbol}' -> '{new_symbol}' across "
        f"{total_files} file(s): {total_renamed}/{total_occurrences} occurrence(s)."
    )

    return ToolResult(success=True, content=summary + "\n\n" + "\n".join(results))


@_summarize("rename_symbol")
def _rename_symbol_summary(args: dict) -> str:
    return (
        f"rename_symbol("
        f"'{args.get('existing_symbol', '?')}' -> "
        f"'{args.get('new_symbol', '?')}', "
        f"paths={args.get('paths', [])})"
    )
