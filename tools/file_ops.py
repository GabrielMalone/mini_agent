#!/usr/bin/env python3
"""file_ops.py -- file/directory tools for mini_agent.

Tools: read_file, write_file, edit_file, edit_lines, list_directory, file_info, init

Heavy lifting delegated to:
    _file_utils.py  -- Unicode normalisation, backups, cache, read helpers
    _edit_ops.py    -- edit_file / edit_lines with fuzzy matching
"""

from __future__ import annotations

import os
import stat as stat_module
import time

from core.safety import ReadSafetyGate, WriteSafetyGate
from tools.result import ToolResult
from tools import _register, _summarize, clear_tool_cache

# Shared utilities (triggers @_register for nothing; just helpers)
from tools._file_utils import (
    _read_file_direct,
    _validate_python_syntax,
    _auto_advance_plan,
    _backup_before_write,
    _run_ruff_check,
    _lint_error_set,
    _changed_lines_in_updated,
    _READ_FILES,
    _FILE_CACHE,
    _FILE_CACHE_MAX,
    _DEFAULT_READ_LINES,
    _ABSOLUTE_MAX_LINES,
)

from core.anchor_manager import (
    AnchorStateManager,
    content_hash as anchor_content_hash,
    format_lines_for_model,
)

from core.file_context_tracker import get_tracker

# Edit operations (triggers @_register for edit_file, edit_lines)
# Also re-exports symbols that external consumers import from tools.file_ops directly
# (shell_ops, ast_tools, agent_ops, tests)
from tools._edit_ops import (  # noqa: F401
    _edit_file,
    _edit_file_anchored,
    _edit_file_summary,
    _edit_lines,
    _edit_lines_summary,
    _normalize_line,
    _fuzzy_find,
    _find_closest_lines,
    _line_match,
    _apply_single_edit,
)


@_register("read_file")
def _read_file(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    # Multi-file support: accept "paths" array or "path" single
    paths = args.get("paths", None)
    if paths is not None:
        if not isinstance(paths, list) or len(paths) == 0:
            return ToolResult(
                success=False,
                content="'paths' must be a non-empty array of file paths.",
            )
        results: list[str] = []
        for p in paths:
            single_args = {**args, "path": p}
            del single_args["paths"]
            r = _read_file(single_args, _wg, rg)
            if r.content:
                results.append(r.content)
        return ToolResult(success=True, content="\n\n".join(results))

    path = args["path"]
    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Read blocked by safety layer: {safety_result.reason}",
        )
    resolved = safety_result.resolved_path

    # Apply offset and limit
    offset = args.get("offset", 0)
    if offset < 0:
        offset = 0
    limit = args.get("limit", _DEFAULT_READ_LINES)
    if limit < 1:
        limit = _DEFAULT_READ_LINES
    limit = min(limit, _ABSOLUTE_MAX_LINES)
    line_numbers = args.get("line_numbers", False)
    hash_lines = args.get("hash_lines", False)
    include_anchors = args.get("include_anchors", False)

    # Cross-turn cache: if file mtime hasn't changed and no special formatting
    if (
        offset == 0
        and limit == _DEFAULT_READ_LINES
        and not line_numbers
        and not hash_lines
        and not include_anchors
    ):
        try:
            current_mtime = os.path.getmtime(resolved)
            if resolved in _FILE_CACHE:
                cached_content, cached_mtime = _FILE_CACHE[resolved]
                if cached_mtime == current_mtime:
                    return ToolResult(success=True, content=cached_content)
        except OSError:
            pass  # fall through to normal read on stat error

    result = _read_file_direct(
        resolved,
        offset,
        limit,
        line_numbers,
        hash_lines=hash_lines,
        include_anchors=include_anchors,
    )

    if not result.success:
        return result

    full_content = result.content

    # Anchor formatting: if include_anchors=True, reconcile anchors and
    # prepend "Apple§" to each line.  Uses the full file for proper anchoring
    # even when offset/limit are specified.
    if include_anchors:
        try:
            raw_lines = full_content.split("\n")
            anchors = AnchorStateManager.reconcile(resolved, raw_lines)
            # Compute content hash for change detection
            fhash = anchor_content_hash(full_content)
            if offset > 0 or (limit and limit < len(raw_lines)):
                # Slice to requested range but keep anchors
                start = offset
                end = min(len(raw_lines), offset + limit) if limit else len(raw_lines)
                sliced = raw_lines[start:end]
                sliced_anchors = anchors[start:end]
                formatted = format_lines_for_model(sliced, sliced_anchors, reveal=True)
                full_content = f"[File Hash: {fhash}]\n[Lines {start + 1}-{end} of {len(raw_lines)}]\n{formatted}"
            else:
                formatted = format_lines_for_model(raw_lines, anchors, reveal=True)
                full_content = f"[File Hash: {fhash}]\n{formatted}"
        except Exception:
            pass  # Fall through with unformatted content on error
    else:
        # Content hash stamp for change detection across turns.
        # When hash_lines or line_numbers are active, the per-line formatting
        # already provides per-line hashes; we still stamp the whole-file hash
        # as a quick "did this file change?" signal.
        try:
            fhash = anchor_content_hash(full_content)
            if not full_content.startswith(
                "[File Hash:"
            ) and not full_content.startswith("[Lines"):
                full_content = f"[File Hash: {fhash}]\n{full_content}"
        except Exception:
            pass

    # Cache full file content for cross-turn reuse (only when reading from offset 0
    # AND the read was not truncated -- avoid caching partial content).
    if offset == 0 and "... (truncated at " not in full_content:
        try:
            current_mtime = os.path.getmtime(resolved)
            # Evict oldest entry if at capacity
            if len(_FILE_CACHE) >= _FILE_CACHE_MAX and resolved not in _FILE_CACHE:
                _FILE_CACHE.pop(next(iter(_FILE_CACHE)), None)
            _FILE_CACHE[resolved] = (full_content, current_mtime)
        except OSError:
            pass

    # Track this file as read for read-before-edit enforcement
    _READ_FILES.add(resolved)
    get_tracker().mark_file_read(resolved)

    return ToolResult(success=True, content=full_content)


@_summarize("read_file")
def _read_file_summary(args: dict) -> str:
    paths = args.get("paths", None)
    if paths:
        n = len(paths)
        preview = ", ".join(paths[:3])
        suffix = "..." if n > 3 else ""
        return f"read_file({n} files: {preview}{suffix})"
    path = args.get("path", "?")
    offset = args.get("offset", 0)
    limit = args.get("limit")
    # Show line range for clarity
    if limit and offset > 0:
        return f"read_file({path}, lines {offset}-{offset + limit})"
    elif limit:
        return f"read_file({path}, limit={limit})"
    elif offset > 0:
        return f"read_file({path}, offset={offset})"
    return f"read_file({path})"


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


@_register("write_file")
def _write_file(args: dict, wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    content = args["content"]
    safety_result = wg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=(
                f"Write blocked by safety layer: {safety_result.reason}\n"
                f"Hint: Use a path inside the workspace ({wg.workspace_root}) or enable unrestricted mode."
            ),
        )
    # Auto-read guard: instead of rejecting writes to unseen .py files,
    # silently mark as read and fall through.  The syntax validation
    # block below reads the file anyway, so the guard's purpose (ensure
    # agent has current content) is satisfied transparently -- no wasted
    # turn cycle forcing a read_file + re-write roundtrip.
    _resolved = safety_result.resolved_path
    if (
        _resolved.endswith(".py")
        and os.path.isfile(_resolved)
        and _resolved not in _READ_FILES
    ):
        _READ_FILES.add(_resolved)
    try:
        # Generate diff preview before writing
        diff = wg.generate_diff("write_file", args)
        parent = os.path.dirname(safety_result.resolved_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _backup_before_write(safety_result.resolved_path)
        # --- ACI upgrade: syntax validation for .py files ---
        # Only gate if the existing file was already valid Python. If the file
        # doesn't even compile now (e.g. prose in a .py test fixture), skip.
        syntax_error = None
        original_is_valid = False
        _prev: str = ""
        if safety_result.resolved_path.endswith(".py"):
            try:
                with open(safety_result.resolved_path, "r", encoding="utf-8") as _f:
                    _prev = _f.read()
                compile(_prev, safety_result.resolved_path, "exec")
            except (FileNotFoundError, SyntaxError):
                pass  # No existing file, or existing content isn't valid Python
            else:
                original_is_valid = True
                syntax_error = _validate_python_syntax(
                    content, safety_result.resolved_path
                )
        if syntax_error:
            return ToolResult(
                success=False,
                content=(
                    f"Syntax validation failed -- file NOT written to prevent broken code.\n"
                    f"{syntax_error}"
                ),
            )
        # --- Lint gate: run ruff on .py files (opt-out via MINI_AGENT_LINT_ON_EDIT=0). ---
        # Mirrors _try_apply_edit in _file_utils.py. Only block if the write
        # *introduced* new (code, line) errors vs the pre-existing file.
        if original_is_valid and os.environ.get("MINI_AGENT_LINT_ON_EDIT") != "0":
            original_errors = _lint_error_set(_prev, safety_result.resolved_path)
            updated_errors = _lint_error_set(content, safety_result.resolved_path)
            changed_lines = _changed_lines_in_updated(_prev, content)
            new_errors = {(code, line) for (code, line) in updated_errors
                          if line in changed_lines and (code, line) not in original_errors}
            if new_errors:
                lint_error = _run_ruff_check(content, safety_result.resolved_path)
                return ToolResult(
                    success=False,
                    content=(
                        f"Lint check failed -- file NOT written to prevent style regressions.\n"
                        f"{lint_error}"
                    ),
                )
        with open(safety_result.resolved_path, "w", encoding="utf-8") as f:
            f.write(content)
        from tools import add_modified_file

        add_modified_file(safety_result.resolved_path)
        clear_tool_cache()
        # Invalidate cross-turn file cache
        _FILE_CACHE.pop(safety_result.resolved_path, None)
        # Track as read for read-before-edit enforcement (agent wrote it, knows content)
        _READ_FILES.add(safety_result.resolved_path)
        get_tracker().mark_file_edited(safety_result.resolved_path)
        # Keep symbol index fresh for newly written .py files
        if path.endswith(".py"):
            from tools.search_ops import _reindex_file

            _reindex_file(safety_result.resolved_path, wg.workspace_root)
        # Keep knowledge graph fresh
        try:
            from core.knowledge_graph import invalidate_file

            invalidate_file(safety_result.resolved_path, wg.workspace_root)
        except Exception:
            pass  # Non-critical: graph invalidation is best-effort
        # Auto plan advancement (file path only -- full content is too noisy)
        _auto_advance_plan(safety_result.resolved_path)
        return ToolResult(
            success=True,
            content=f"OK: wrote {len(content)} bytes to {safety_result.resolved_path}",
            diff_preview=diff.preview_text if diff.changed else None,
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f"Error writing '{safety_result.resolved_path}': {e}",
        )

@_summarize("write_file")
def _write_file_summary(args: dict) -> str:
    path = args.get("path", "?")
    # Multi-file batch mode
    paths = args.get("paths", [])
    if paths:
        return f"write_file({len(paths)} files: {', '.join(paths[:3])}{'...' if len(paths) > 3 else ''})"
    content = args.get("content", "")
    byte_count = len(content)
    # Show line count alongside byte count for better edit visibility
    lines = content.split("\n")
    line_count = len(lines)
    first_line = lines[0][:80].strip()
    truncated_first = "..." if len(lines[0]) > 80 else ""
    has_more_lines = "..." if line_count > 1 else ""
    # Build size label: use lines if multiline, bytes for short content
    if line_count > 1:
        size = f"{line_count} lines, {byte_count}B"
    else:
        size = f"{byte_count}B"
    return f'write_file({path}, {size}) "{first_line}{truncated_first}{has_more_lines}"'


# ---------------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------------


@_register("list_directory")
def _list_directory(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"List blocked by safety layer: {safety_result.reason}",
        )
    try:
        rows: list[str] = []
        with os.scandir(safety_result.resolved_path) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                prefix = "d" if entry.is_dir(follow_symlinks=False) else "f"
                rows.append(f"  [{prefix}] {entry.name}")
        if not rows:
            content = f"{safety_result.resolved_path}  (empty)"
        else:
            content = f"{safety_result.resolved_path}\n" + "\n".join(rows)
        return ToolResult(success=True, content=content)
    except Exception as e:
        return ToolResult(
            success=False, content=f"Error listing '{safety_result.resolved_path}': {e}"
        )


@_summarize("list_directory")
def _list_directory_summary(args: dict) -> str:
    return f"list_directory({args.get('path', '?')})"


# ---------------------------------------------------------------------------
# file_info
# ---------------------------------------------------------------------------


@_register("file_info")
def _file_info(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"File info blocked by safety layer: {safety_result.reason}",
        )
    resolved = safety_result.resolved_path
    try:
        st = os.stat(resolved)
        parts = [
            f"path: {resolved}",
            f"size: {st.st_size} bytes",
            f"mode: {stat_module.filemode(st.st_mode)}",
            f"modified: {time.ctime(st.st_mtime)}",
        ]
        if stat_module.S_ISDIR(st.st_mode):
            parts.append("type: directory")
            # Gather child count and total recursive size
            child_count = 0
            total_size = 0
            try:
                with os.scandir(resolved) as entries:
                    for entry in entries:
                        child_count += 1
                        try:
                            total_size += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
            except PermissionError:
                pass
            parts.append(f"children: {child_count}")
            parts.append(f"total_children_size: {total_size} bytes")
        else:
            parts.append("type: file")
        return ToolResult(success=True, content="\n".join(parts))
    except FileNotFoundError:
        return ToolResult(success=True, content=f"path: {resolved}\nexists: no")
    except Exception as e:
        return ToolResult(success=False, content=f"Error stating '{resolved}': {e}")


@_summarize("file_info")
def _file_info_summary(args: dict) -> str:
    return f"file_info({args.get('path', '?')})"


@_register("init")
@_summarize("init")
def _init_rules(args: dict, _wg, read_gate: ReadSafetyGate) -> ToolResult:
    """Analyze the workspace and auto-generate .mini_agent.rules + .mini_agent.toml
    and seed project_knowledge with auto-detected learnings."""
    try:
        import subprocess
        import time

        workspace = read_gate.workspace_root
        rules_path = os.path.join(workspace, ".mini_agent.rules")
        toml_path = os.path.join(workspace, ".mini_agent.toml")
        created: list[str] = []
        knowledge: list[
            tuple[str, str, str, int]
        ] = []  # (summary, category, detail, importance)

        # --- Recursive scan for Python files ---
        py_files_all: list[str] = []
        test_files: list[str] = []
        for root, dirs, files in os.walk(workspace):
            # Skip hidden dirs, venvs, node_modules, __pycache__
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d
                not in (
                    "node_modules",
                    "venv",
                    ".venv",
                    "__pycache__",
                    "dist",
                    "build",
                    ".git",
                )
            ]
            for f in files:
                if f.endswith(".py"):
                    full = os.path.join(root, f)
                    py_files_all.append(full)
                    if f.startswith("test_") or f.endswith("_test.py"):
                        test_files.append(full)

        py_files = sorted(py_files_all)

        # --- .mini_agent.rules ---
        rules = [
            f"# Auto-generated by /init on {time.strftime('%Y-%m-%d')}",
            f"# Workspace: {workspace}",
            "",
            "## Code Style",
            "- Use type hints on all public functions.",
            "- Prefer dataclasses for structured data.",
            "- No magic numbers; use named constants.",
            "- Keep modules small and single-purpose.",
            "",
            "## Testing",
            "- Run tests with: python -m pytest -q",
            "",
            "## Module Map",
        ]
        for pf in py_files[:25]:
            rules.append(f"  {os.path.basename(pf)}  # auto-detected")
        with open(rules_path, "w", encoding="utf-8") as f:
            f.write("\n".join(rules))
        created.append(
            f".mini_agent.rules ({len(rules)} lines, {len(py_files)} modules)"
        )

        # --- .mini_agent.toml (if missing) ---
        if not os.path.isfile(toml_path):
            toml = [
                "# Auto-generated by /init on " + time.strftime("%Y-%m-%d"),
                "",
                "[agent]",
                '# model = "deepseek-v4-pro"',
                "# max_messages = 500",
                "# max_tokens = 200000",
                "# stream = false",
                "# allow_overwrites = false",
                "# unrestricted = false",
            ]
            with open(toml_path, "w", encoding="utf-8") as f:
                f.write("\n".join(toml))
            created.append(".mini_agent.toml (template)")
        else:
            created.append(".mini_agent.toml (already exists, skipped)")

        # --- Auto-detect workspace learnings for project_knowledge ---
        # 1. Module count
        if py_files:
            knowledge.append(
                (
                    f"Workspace has {len(py_files)} Python module(s)",
                    "workspace",
                    f"Total .py files: {len(py_files)}. Test files: {len(test_files)}.",
                    2,
                )
            )
        if test_files:
            knowledge.append(
                (
                    f"{len(test_files)} test file(s) detected",
                    "testing",
                    f"Test files: {', '.join(os.path.basename(t) for t in test_files[:10])}.",
                    3,
                )
            )

        # 2. Import-based framework detection (sample first 20 files)
        frameworks: dict[str, str] = {}
        known_frameworks = {
            "fastapi": "web",
            "flask": "web",
            "django": "web",
            "starlette": "web",
            "pytest": "testing",
            "unittest": "testing",
            "torch": "ml",
            "tensorflow": "ml",
            "jax": "ml",
            "transformers": "ml",
            "pandas": "data",
            "numpy": "data",
            "polars": "data",
            "click": "cli",
            "typer": "cli",
            "argparse": "cli",
            "sqlalchemy": "database",
            "sqlite3": "database",
            "pydantic": "validation",
            "dataclasses": "data",
            "rich": "ui",
            "textual": "ui",
        }
        sample = py_files[: min(20, len(py_files))]
        for pf in sample:
            try:
                with open(pf, encoding="utf-8", errors="replace") as f:
                    content = f.read(4096)
                for line in content.split("\n")[:80]:
                    line_stripped = line.strip()
                    if line_stripped.startswith(("import ", "from ")):
                        for kw, cat in known_frameworks.items():
                            if kw in line_stripped and kw not in frameworks:
                                frameworks[kw] = cat
            except Exception:
                pass
        for framework, cat in sorted(frameworks.items()):
            knowledge.append(
                (
                    f"Uses {framework} ({cat})",
                    "dependencies",
                    f"Detected import of {framework} in workspace source.",
                    2,
                )
            )

        # 3. Git repo detection
        if os.path.isdir(os.path.join(workspace, ".git")):
            try:
                result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                branch = result.stdout.strip()
                git_info = f"branch: {branch}" if branch else "git repo detected"
            except Exception:
                git_info = "git repo detected"
            knowledge.append(
                (
                    f"Git repository: {git_info}",
                    "workspace",
                    "Project is version-controlled with git.",
                    2,
                )
            )

        # 4. Language detection (look for non-Python files)
        other_exts: set[str] = set()
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d
                not in (
                    "node_modules",
                    "venv",
                    ".venv",
                    "__pycache__",
                    "dist",
                    "build",
                    ".git",
                )
            ]
            for f in files:
                _, ext = os.path.splitext(f)
                if (
                    ext
                    and ext != ".py"
                    and ext not in (".pyc", ".pyo", ".pyd", ".so", ".dylib")
                ):
                    other_exts.add(ext)
            if len(other_exts) >= 10:
                break
        if other_exts:
            knowledge.append(
                (
                    f"Multi-language: {', '.join(sorted(other_exts)[:10])}",
                    "workspace",
                    f"Non-Python file types detected: {', '.join(sorted(other_exts))}.",
                    1,
                )
            )

        # --- Store knowledge to project_knowledge table ---
        from tools import _TOOL_CONTEXT

        memory_store = getattr(_TOOL_CONTEXT, "_memory_store", None)
        if memory_store and knowledge:
            stored = 0
            for summary, category, detail, importance in knowledge:
                existing = memory_store.find_knowledge(category, summary)
                if existing:
                    memory_store.bump_knowledge(existing["id"])
                else:
                    memory_store.add_knowledge(summary, category, detail, importance)
                stored += 1
            created.append(f"{stored} project learnings")

        return ToolResult(
            success=True, content=f"Initialized workspace: {', '.join(created)}."
        )
    except Exception as e:
        return ToolResult(success=False, content=f"/init failed: {e}")
