#!/usr/bin/env python3
"""
file_ops.py — file/directory tools for mini_agent.

Tools: read_file, write_file, edit_file, list_directory, file_info
"""

import os
import stat as stat_module
import shutil
import time

from safety import ReadSafetyGate, WriteSafetyGate
from tools import clear_tool_cache
from tools import _register, _summarize, ToolResult, _TOOL_CONTEXT, CTX_SCRATCHPAD_PATH, CTX_SCRATCHPAD_UPDATED
from tools import _FILE_RESERVATIONS

# Thread-local: current sub-agent task_id (set by agent_ops before tool execution)
import threading
_current_agent_id: threading.local = threading.local()


# ---------------------------------------------------------------------------
# Session undo — backs up files before modification
# ---------------------------------------------------------------------------

_BACKUPS: dict[str, str] = {}  # resolved_path -> backup path


def _backup_before_write(resolved_path: str) -> None:
    """Save a backup of *resolved_path* if it exists and hasn't already been backed up."""
    if resolved_path in _BACKUPS:
        return  # already backed up
    if not os.path.isfile(resolved_path):
        return  # nothing to back up
    backup_dir = os.path.join(os.path.dirname(resolved_path), ".mini_agent_backups")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    fname = os.path.basename(resolved_path)
    backup_path = os.path.join(backup_dir, f"{fname}.{timestamp}.bak")
    shutil.copy2(resolved_path, backup_path)
    _BACKUPS[resolved_path] = backup_path


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

# Default maximum lines returned by read_file when no limit is given.
_DEFAULT_READ_LINES = 300
# Absolute maximum (safety cap) — never return more than this.
_ABSOLUTE_MAX_LINES = 1000


@_register("read_file")
def _read_file(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Read blocked by safety layer: {safety_result.reason}",
        )
    # Apply offset and limit
    offset = args.get("offset", 0)
    if offset < 0:
        offset = 0
    limit = args.get("limit", _DEFAULT_READ_LINES)
    if limit < 1:
        limit = _DEFAULT_READ_LINES
    limit = min(limit, _ABSOLUTE_MAX_LINES)
    line_numbers = args.get("line_numbers", False)

    try:
        with open(safety_result.resolved_path, "r") as f:
            # Use enumerate + early break to avoid reading the whole file
            collected: list[str] = []
            total_lines = 0
            for lineno, line in enumerate(f):
                total_lines = lineno + 1
                if lineno < offset:
                    continue
                if len(collected) < limit:
                    stripped = line.rstrip("\n")
                    if line_numbers:
                        stripped = f"{total_lines}: {stripped}"
                    collected.append(stripped)
                # Keep iterating to count total lines if we might need truncation message
                # but stop once we've gone well past what we need (limit + 1 is enough
                # to know whether we truncated)
                if len(collected) >= limit and lineno >= offset + limit:
                    break
    except Exception as e:
        hint = ""
        if isinstance(e, FileNotFoundError) or "No such file" in str(e):
            hint = "\nHint: Check the path spelling. Try list_directory to see available files."
        return ToolResult(success=False, content=f"Error reading '{safety_result.resolved_path}': {e}{hint}")

    if offset >= total_lines:
        return ToolResult(success=False, content=f"Offset {offset} exceeds file length ({total_lines} lines).")

    lines_after_offset = total_lines - offset
    if lines_after_offset > limit:
        truncated = "\n".join(collected[:limit])
        msg = (
            f"{truncated}\n"
            f"… (truncated at {limit} lines — {lines_after_offset} total in selection. "
            f"Use a higher limit or offset to see more.)"
        )
        return ToolResult(success=True, content=msg)

    return ToolResult(success=True, content="\n".join(collected))


@_summarize("read_file")
def _read_file_summary(args: dict) -> str:
    return f"read_file({args.get('path', '?')})"


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
    # File reservation check — prevent sub-agent collisions
    agent_id = getattr(_current_agent_id, "task_id", None)
    if agent_id is not None:
        existing = _FILE_RESERVATIONS.get(path)
        if existing is not None and existing != agent_id:
            return ToolResult(
                success=False,
                content=(
                    f"Write blocked: '{path}' is reserved by agent '{existing[:8]}'. "
                    f"Hint: Coordinate with the parent — only one agent should write to a file."
                ),
            )
    try:
        parent = os.path.dirname(safety_result.resolved_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _backup_before_write(safety_result.resolved_path)
        with open(safety_result.resolved_path, "w") as f:
            f.write(content)
        from tools import _MODIFIED_FILES
        _MODIFIED_FILES.add(safety_result.resolved_path)
        clear_tool_cache()
        # Keep symbol index fresh for newly written .py files
        if path.endswith(".py"):
            from tools.search_ops import _reindex_file
            _reindex_file(safety_result.resolved_path, wg.workspace_root)
        return ToolResult(
            success=True,
            content=f"OK: wrote {len(content)} bytes to {safety_result.resolved_path}",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f"Error writing '{safety_result.resolved_path}': {e}",
        )


@_summarize("write_file")
def _write_file_summary(args: dict) -> str:
    path = args.get("path", "?")
    content = args.get("content", "")
    preview = content[:60].replace("\n", "\\n")
    if len(content) > 60:
        preview += "…"
    return f"write_file({path}, {len(content)}B → \"{preview}\")"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

@_register("edit_file")
def _edit_file(args: dict, wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    old = args["old_string"]
    new = args["new_string"]
    count = args.get("count", 1)  # 1 = first occurrence, -1 = all occurrences
    safety_result = wg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Edit blocked by safety layer: {safety_result.reason}",
        )
    # File reservation check — prevent sub-agent collisions
    agent_id = getattr(_current_agent_id, "task_id", None)
    if agent_id is not None:
        existing = _FILE_RESERVATIONS.get(path)
        if existing is not None and existing != agent_id:
            return ToolResult(
                success=False,
                content=(
                    f"Edit blocked: '{path}' is reserved by agent '{existing[:8]}'. "
                    f"Hint: Coordinate with the parent — only one agent should edit a file."
                ),
            )
    try:
        with open(safety_result.resolved_path, "r") as f:
            original = f.read()
        _backup_before_write(safety_result.resolved_path)
        if old not in original:
            # Search for similar substrings to help the agent self-correct
            candidates: list[str] = []
            old_first_line = old.split("\n")[0].strip()
            for lineno, line in enumerate(original.split("\n"), 1):
                if old_first_line and old_first_line[:30] in line:
                    candidates.append(f"  line {lineno}: {line.rstrip()[:120]}")
                if len(candidates) >= 3:
                    break
            hint = (
                f"Edit failed: old_string not found in '{safety_result.resolved_path}'.\n"
                f"Hint: The string must match exactly — check whitespace, indentation, "
                f"and line endings. Try read_file first to verify the exact text."
            )
            if candidates:
                hint += "\nSimilar lines found (did you mean one of these?):\n" + "\n".join(candidates)
            return ToolResult(success=False, content=hint)

        if count == -1:
            # Replace all occurrences
            occurrences = original.count(old)
            updated = original.replace(old, new)
            replaced = occurrences
        elif count >= 1:
            # Replace first N occurrences
            updated = original.replace(old, new, count)
            replaced = min(count, original.count(old))
        else:
            return ToolResult(success=False, content=f"Invalid count: {count}. Use a positive integer or -1 (all).")

        with open(safety_result.resolved_path, "w") as f:
            f.write(updated)

        from tools import _MODIFIED_FILES
        _MODIFIED_FILES.add(safety_result.resolved_path)
        clear_tool_cache()

        # Short summary: no full diff on success (saves context tokens)
        added = updated.count("\n") - original.count("\n")
        label = f"{replaced} occurrence(s)" if replaced > 1 else "1 occurrence"
        return ToolResult(
            success=True,
            content=(
                f"OK: replaced {label} in {safety_result.resolved_path}"
                + (f" (+{added} lines)" if added > 0 else f" ({added} lines)" if added < 0 else "")
            ),
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f"Error editing '{safety_result.resolved_path}': {e}",
        )


@_summarize("edit_file")
def _edit_file_summary(args: dict) -> str:
    path = args.get("path", "?")
    old = args.get("old_string", "")
    preview = old[:40].replace("\n", "\\n")
    if len(old) > 40:
        preview += "…"
    return f"edit_file({path}, \"{preview}\")"


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
        return ToolResult(success=False, content=f"Error listing '{safety_result.resolved_path}': {e}")


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


# ---------------------------------------------------------------------------
# write_scratchpad
# ---------------------------------------------------------------------------

@_register("write_scratchpad")
def _write_scratchpad(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Write content to the agent's persistent working scratchpad."""
    import os as _os
    content_text = args["content"]

    # Find the MemoryStore instance via _TOOL_CONTEXT
    # The scratchpad is stored in the SQLite DB alongside messages
    scratchpad_path = _TOOL_CONTEXT.scratchpad_path or ""
    if scratchpad_path:
        try:
            import sqlite3
            conn = sqlite3.connect(scratchpad_path)
            # Ensure the table exists (DB may not have been through MemoryStore.__init__)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scratchpad ("
                "id INTEGER PRIMARY KEY CHECK (id = 1),"
                "content TEXT NOT NULL DEFAULT ''"
                ")"
            )
            conn.execute("INSERT OR IGNORE INTO scratchpad (id, content) VALUES (1, '')")
            conn.execute(
                "INSERT OR REPLACE INTO scratchpad (id, content) VALUES (1, ?)",
                (content_text,),
            )
            conn.commit()
            conn.close()
            _TOOL_CONTEXT._scratchpad_updated = True
            return ToolResult(
                success=True,
                content=f"Scratchpad updated ({len(content_text)} chars).",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                content=f"Failed to update scratchpad: {e}",
            )

    # Fallback: store in a file
    fallback = _os.path.join(
        _TOOL_CONTEXT.workspace or ".", ".mini_agent_scratchpad.md"
    )
    sr = _wg.check(fallback)
    if not sr.allowed:
        return ToolResult(success=False, content=f"Scratchpad blocked: {sr.reason}")
    try:
        with open(fallback, "w") as f:
            f.write(content_text)
        return ToolResult(
            success=True,
            content=f"Scratchpad updated ({len(content_text)} chars).",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f"Failed to update scratchpad: {e}",
        )


@_summarize("write_scratchpad")
def _write_scratchpad_summary(args: dict) -> str:
    content = args.get("content", "")
    preview = content[:60].replace("\n", " ")
    if len(content) > 60:
        preview += "…"
    return f"write_scratchpad(…{len(content)} chars → \"{preview}\")"


# ---------------------------------------------------------------------------
# diff tool — show unstaged changes via git
# ---------------------------------------------------------------------------

@_register("diff")
def _diff(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    """Show unstaged changes (git diff) for the workspace or a specific file."""
    import subprocess
    path = args.get("path", "")
    cmd = ["git", "-C", rg.workspace_root, "diff"]
    if path:
        cmd.append(path)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return ToolResult(success=False, content=r.stderr or "git diff failed")
        if not r.stdout.strip():
            return ToolResult(success=True, content="No unstaged changes.")
        return ToolResult(success=True, content=r.stdout.rstrip())
    except FileNotFoundError:
        return ToolResult(success=False, content="git not found")
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, content="diff timed out")
    except Exception as e:
        return ToolResult(success=False, content=f"Error running diff: {e}")


@_summarize("diff")
def _diff_summary(args: dict) -> str:
    path = args.get("path", "")
    if path:
        return f"diff({path})"
    return "diff()"


# ---------------------------------------------------------------------------
# restore_file — session undo
# ---------------------------------------------------------------------------


@_register("restore_file")
def _restore_file(args: dict, wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Restore a file from its session backup (undo the last write/edit)."""
    path = args["path"]
    safety_result = wg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Restore blocked by safety layer: {safety_result.reason}",
        )
    resolved = safety_result.resolved_path

    if resolved not in _BACKUPS:
        return ToolResult(
            success=False,
            content=f"No backup available for '{resolved}'. Only files modified this session can be restored.",
            hint="No backup exists. Either the file hasn't been modified this session, or it was already restored.",
        )

    backup_path = _BACKUPS[resolved]
    try:
        shutil.copy2(backup_path, resolved)
        del _BACKUPS[resolved]
        from tools import _MODIFIED_FILES
        _MODIFIED_FILES.discard(safety_result.resolved_path)
        return ToolResult(
            success=True,
            content=f"Restored '{resolved}' from backup ({os.path.basename(backup_path)}).",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f"Error restoring '{resolved}': {e}",
        )


@_summarize("restore_file")
def _restore_file_summary(args: dict) -> str:
    return f"restore_file({args.get('path', '?')})"


# ---------------------------------------------------------------------------
# plan / plan_status tools — structured task tracking
# ---------------------------------------------------------------------------

CTX_PLAN_STEPS = "_plan_steps"       # list[str]
CTX_PLAN_DONE = "_plan_done"         # set[int] — 0-indexed completed step indices


@_register("plan")
def _plan(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Declare a structured task plan."""
    steps = args["steps"]
    if not isinstance(steps, list) or not steps:
        return ToolResult(
            success=False,
            content="Plan must have at least one step.",
            hint="Provide a non-empty array of step descriptions.",
        )
    _TOOL_CONTEXT._plan_steps = steps
    _TOOL_CONTEXT._plan_done = set()
    lines = [f"Plan ({len(steps)} steps):"]
    for i, step in enumerate(steps, 1):
        lines.append(f"  [{i}] {step}")
    return ToolResult(success=True, content="\n".join(lines))


@_summarize("plan")
def _plan_summary(args: dict) -> str:
    steps = args.get("steps", [])
    return f"plan({len(steps)} steps: {steps[0][:40] if steps else '?'}…)"


@_register("plan_status")
def _plan_status(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Mark a step complete or report status."""
    step = args.get("step")
    steps = _TOOL_CONTEXT._plan_steps
    done = _TOOL_CONTEXT._plan_done

    if not steps:
        return ToolResult(success=True, content="No active plan.")

    if step is not None:
        idx = step - 1  # 1-indexed → 0-indexed
        if idx < 0 or idx >= len(steps):
            return ToolResult(
                success=False,
                content=f"Invalid step {step}. Plan has {len(steps)} steps.",
                hint=f"Step must be between 1 and {len(steps)}.",
            )
        done.add(idx)
        _TOOL_CONTEXT._plan_done = done

    lines = [f"Plan ({len(done)}/{len(steps)} complete):"]
    for i, s in enumerate(steps, 1):
        mark = "✓" if (i - 1) in done else "○"
        lines.append(f"  [{mark}] {i}. {s}")
    all_done = len(done) == len(steps)
    if all_done:
        lines.append("  All steps complete!")
    return ToolResult(success=True, content="\n".join(lines))


@_summarize("plan_status")
def _plan_status_summary(args: dict) -> str:
    step = args.get("step")
    if step is not None:
        return f"plan_status(complete step {step})"
    return "plan_status()"
