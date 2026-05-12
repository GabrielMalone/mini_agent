#!/usr/bin/env python3
"""
file_ops.py — file/directory tools for mini_agent.

Tools: read_file, write_file, edit_file, list_directory, file_info
"""

import os
import stat as stat_module
import time

from safety import ReadSafetyGate, WriteSafetyGate
from tools import _register, _summarize, ToolResult, _TOOL_CONTEXT


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

# Maximum lines returned by read_file (tool result sent to the LLM).
# Beyond this, content is truncated with a note to use offset/limit.
_MAX_READ_LINES = 300


@_register("read_file")
def _read_file(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Read blocked by safety layer: {safety_result.reason}",
        )
    try:
        with open(safety_result.resolved_path, "r") as f:
            content = f.read()
    except Exception as e:
        hint = ""
        if isinstance(e, FileNotFoundError) or "No such file" in str(e):
            hint = "\nHint: Check the path spelling. Try list_directory to see available files."
        return ToolResult(success=False, content=f"Error reading '{safety_result.resolved_path}': {e}{hint}")

    lines = content.split("\n")
    if len(lines) > _MAX_READ_LINES:
        truncated = "\n".join(lines[:_MAX_READ_LINES])
        msg = (
            f"{truncated}\n"
            f"… (truncated at {_MAX_READ_LINES} lines — {len(lines)} total. "
            f"Use edit_file with a specific line range if you need more detail.)"
        )
        return ToolResult(success=True, content=msg)

    return ToolResult(success=True, content=content)


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
                f"Hint: Use a path inside the workspace ({wg.workspace_root})."
            ),
        )
    try:
        parent = os.path.dirname(safety_result.resolved_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(safety_result.resolved_path, "w") as f:
            f.write(content)
        from tools import _MODIFIED_FILES
        _MODIFIED_FILES.add(args["path"])
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
    safety_result = wg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Edit blocked by safety layer: {safety_result.reason}",
        )
    try:
        with open(safety_result.resolved_path, "r") as f:
            original = f.read()
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
        updated = original.replace(old, new, 1)
        with open(safety_result.resolved_path, "w") as f:
            f.write(updated)

        # Build a short unified diff for verification
        import difflib
        diff_lines = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        ))
        diff_text = "".join(diff_lines)
        if len(diff_text) > 2000:
            diff_text = diff_text[:2000] + "\n… (diff truncated)"

        from tools import _MODIFIED_FILES
        _MODIFIED_FILES.add(args["path"])
        return ToolResult(
            success=True,
            content=(
                f"OK: replaced 1 occurrence in {safety_result.resolved_path}\n"
                f"```diff\n{diff_text}```"
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
        entries = os.listdir(safety_result.resolved_path)
        rows: list[str] = []
        for name in sorted(entries):
            full = os.path.join(safety_result.resolved_path, name)
            prefix = "d" if os.path.isdir(full) else "f"
            rows.append(f"  [{prefix}] {name}")
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
    scratchpad_path = _TOOL_CONTEXT.get("scratchpad_path", "")
    if scratchpad_path:
        try:
            import sqlite3
            conn = sqlite3.connect(scratchpad_path)
            conn.execute(
                "INSERT OR REPLACE INTO scratchpad (id, content) VALUES (1, ?)",
                (content_text,),
            )
            conn.commit()
            conn.close()
            _TOOL_CONTEXT["_scratchpad_updated"] = True
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
        _TOOL_CONTEXT.get("workspace", "."), ".mini_agent_scratchpad.md"
    )
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
