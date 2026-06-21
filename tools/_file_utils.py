#!/usr/bin/env python3
"""Shared utilities for file operations -- Unicode normalisation, backups, cache."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import threading
import time

from tools.result import ToolResult
from tools import clear_tool_cache
from tools import _TOOL_CONTEXT

# Thread-local: current sub-agent task_id (set by agent_ops before tool execution)
_current_agent_id: threading.local = threading.local()

_WINDOWS = platform.system() == "Windows"

# ---------------------------------------------------------------------------
# Windows-safe file read via _worker subprocess
# ---------------------------------------------------------------------------
# On Windows, ``open()`` / ``CreateFileW`` can block indefinitely inside
# kernel minifilter drivers (antivirus, backup agents, etc.).  Python
# threads have no way to kill a thread stuck in a kernel I/O call.
# The _worker subprocess isolates the I/O so the OS can kill it with
# TerminateProcess if it doesn't respond within the timeout.

_WORKER_READ_TIMEOUT = 30  # seconds for a single file read


def _read_file_windows_worker(
    resolved: str,
    offset: int,
    limit: int,
    line_numbers: bool,
) -> ToolResult:
    """Read a file via the _worker subprocess with a hard timeout.

    Falls back to direct open() if the worker fails for non-hang reasons.

    Uses _communicate_windows() on Windows to avoid proc.communicate() hangs.
    """
    if _WINDOWS:
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools._worker",
                    "read",
                    resolved,
                    str(offset),
                    str(limit),
                    str(line_numbers),
                ],
                capture_output=True,
                text=True,
                timeout=_WORKER_READ_TIMEOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            stdout, stderr = proc.stdout, proc.stderr
            import json

            data = json.loads(stdout.strip())
            if data.get("ok"):
                return ToolResult(success=True, content=data["content"])
            else:
                return ToolResult(
                    success=False,
                    content=data.get("content", "Worker read failed"),
                )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                content=f"File read timed out after {_WORKER_READ_TIMEOUT}s "
                f"(possibly blocked by antivirus or filter driver). "
                f"Try excluding the project directory from real-time scanning.",
            )
        except Exception:
            pass
    else:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools._worker",
                    "read",
                    resolved,
                    str(offset),
                    str(limit),
                    str(line_numbers),
                ],
                capture_output=True,
                text=True,
                timeout=_WORKER_READ_TIMEOUT,
            )
            import json

            data = json.loads(result.stdout.strip())
            if data.get("ok"):
                return ToolResult(success=True, content=data["content"])
            else:
                return ToolResult(
                    success=False,
                    content=data.get("content", "Worker read failed"),
                )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                content=f"File read timed out after {_WORKER_READ_TIMEOUT}s "
                f"(possibly blocked by antivirus or filter driver). "
                f"Try excluding the project directory from real-time scanning.",
            )
        except Exception:
            pass

    # Fallback: direct open (may hang on Windows but we already tried)
    return _read_file_direct(resolved, offset, limit, line_numbers)


def _read_file_direct(
    resolved: str,
    offset: int,
    limit: int,
    line_numbers: bool,
    hash_lines: bool = False,
    include_anchors: bool = False,
) -> ToolResult:
    """Direct file read -- used on Unix and as fallback on Windows."""
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            collected: list[str] = []
            total_lines = 0
            for lineno, line in enumerate(f):
                total_lines = lineno + 1
                if lineno + 1 < offset:
                    continue
                if len(collected) < limit:
                    stripped = line.rstrip("\n")
                    if include_anchors:
                        # Will be formatted with anchors after the full read
                        collected.append(stripped)
                    elif hash_lines:
                        h = hashlib.sha256(stripped.rstrip().encode()).hexdigest()[:3]
                        stripped = f"{total_lines}:{h}| {stripped}"
                        collected.append(stripped)
                    elif line_numbers:
                        stripped = f"{total_lines}: {stripped}"
                        collected.append(stripped)
                    else:
                        collected.append(stripped)
                if len(collected) >= limit and lineno + 1 >= offset + limit:
                    break
    except Exception as e:
        hint = ""
        if isinstance(e, FileNotFoundError) or "No such file" in str(e):
            hint = "\nHint: Check the path spelling. Try list_directory to see available files."
        return ToolResult(
            success=False, content=f"Error reading '{resolved}': {e}{hint}"
        )

    if offset > total_lines:
        return ToolResult(
            success=False,
            content=f"Offset {offset} exceeds file length ({total_lines} lines).",
        )

    full_content = "\n".join(collected)
    lines_after_offset = total_lines - offset + 1

    if lines_after_offset > limit:
        truncated = "\n".join(collected[:limit])
        msg = (
            f"{truncated}\n"
            f"... (truncated at {limit} lines -- {lines_after_offset} total in selection. "
            f"Use a higher limit or offset to see more.)"
        )
        return ToolResult(success=True, content=msg)

    return ToolResult(success=True, content=full_content)


# ---------------------------------------------------------------------------
# Unicode & quote normalization maps (used by edit_file matching)
# ---------------------------------------------------------------------------

# Curly/smart quotes -> ASCII straight quotes
_QUOTE_NORMALIZE_MAP: dict[int, int | None] = {
    0x2018: ord("'"),  # ' left single
    0x2019: ord("'"),  # ' right single
    0x201A: ord("'"),  # , single low-9
    0x201B: ord("'"),  # ' single high-reversed
    0x201C: ord('"'),  # " left double
    0x201D: ord('"'),  # " right double
    0x201E: ord('"'),  # ,, double low-9
    0x201F: ord('"'),  # " double high-reversed
    0x2039: ord("'"),  # < single left-pointing angle
    0x203A: ord("'"),  # > single right-pointing angle
    0x00AB: ord('"'),  # << left-pointing double angle
    0x00BB: ord('"'),  # >> right-pointing double angle
}

# Unicode whitespace -> ASCII space (or None = remove)
_UNICODE_WHITESPACE_MAP: dict[int, int | None] = {
    0x00A0: ord(" "),  # non-breaking space
    0x2002: ord(" "),  # en space
    0x2003: ord(" "),  # em space
    0x2007: ord(" "),  # figure space
    0x2008: ord(" "),  # punctuation space
    0x2009: ord(" "),  # thin space
    0x200A: ord(" "),  # hair space
    0x202F: ord(" "),  # narrow non-breaking space
    0x205F: ord(" "),  # medium mathematical space
    0x3000: ord(" "),  # ideographic space
    0x00AD: None,  # soft hyphen -> remove
    0x200B: None,  # zero-width space -> remove
    0x200C: None,  # zero-width non-joiner -> remove
    0x200D: None,  # zero-width joiner -> remove
    0xFEFF: None,  # BOM / zero-width no-break space -> remove
    0x2060: None,  # word joiner -> remove
}

# Build fast translation tables (Python str.translate)
_QUOTE_TRANS_TABLE: dict[int, int] = {}
_UNICODE_WS_TRANS_TABLE: dict[int, int | None] = {}


def _normalize_quotes(s: str) -> str:
    """Convert curly/smart quotes to ASCII straight quotes."""
    return s.translate(_QUOTE_TRANS_TABLE)


def _normalize_unicode_whitespace(s: str) -> str:
    """Replace Unicode whitespace chars with ASCII space; remove zero-width chars."""
    return s.translate(_UNICODE_WS_TRANS_TABLE)


def _canonicalize_for_match(s: str) -> str:
    """Full canonicalization for matching: normalize Unicode ws, then quotes."""
    return _normalize_quotes(_normalize_unicode_whitespace(s))


# ---------------------------------------------------------------------------
# Read-before-edit tracking -- set of resolved_path values that have been
# read_file'd during this session.  Edit/replace operations check this to
# ensure the model has seen the current file content.
# ---------------------------------------------------------------------------

_READ_FILES: set[str] = set()

# ---------------------------------------------------------------------------
# ACI (Agent-Computer Interface) upgrade: syntax validation before applying
# edits.  Catch broken Python syntax before the edit cascades into a series
# of compounding failures.  This is the SWE-agent linter-in-edit pattern.
# ---------------------------------------------------------------------------


def _validate_python_syntax(content: str, filepath: str) -> str | None:
    """Return an error message if *content* is not valid Python, else None.

    Uses ``compile()`` for fast in-process validation.  Only checks .py files.
    """
    if not filepath.endswith(".py"):
        return None
    try:
        compile(content, filepath, "exec")
    except SyntaxError as e:
        # Build a helpful pointer line
        lines = content.split("\n")
        lineno = e.lineno or 1
        pointer = f"  line {lineno}: {lines[lineno - 1][:100] if lineno <= len(lines) else '?'}"
        return (
            f"SyntaxError in {filepath}: {e.msg} at line {lineno}\n"
            f"{pointer}\n"
            f"Fix the syntax error before applying. If unsure, read the file "
            f"with offset near line {lineno} first."
        )
    return None


import subprocess as _ruff_subprocess


def _run_ruff_check(content: str, filepath: str) -> str | None:
    """Run ruff on *content* via stdin; return error string or None if clean.

    Selects E (pycodestyle errors) + F (pyflakes) — high-signal, near-zero
    false-positive rules. Skips silently if ruff is not installed or times out.
    """
    try:
        proc = _ruff_subprocess.run(
            ["ruff", "check", "--select=E,F", "--stdin-filename", filepath, "-"],
            input=content,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return None  # ruff not installed — skip
    except _ruff_subprocess.TimeoutExpired:
        return None  # don't block on lint

    if proc.returncode == 0:
        return None

    # ruff outputs diagnostics to stdout
    output = (proc.stdout + proc.stderr).strip()
    if not output:
        return None

    return (
        f"ruff lint errors in {filepath}:\n{output}\n"
        f"Fix the lint errors before applying. Set MINI_AGENT_LINT_ON_EDIT=0 to disable."
    )


def _finalize_edit(
    resolved: str,
    original: str,
    updated: str,
    workspace_root: str,
    *,
    edit_text: str = "",
) -> tuple[bool, str | None]:
    """Shared post-edit pipeline: validate syntax, backup, write, track, re-index.

    Consolidates the duplicated write logic from _apply_single_edit,
    _edit_file_anchored, and _edit_lines into a single function.

    Returns (success, error_message_or_None).
    """
    # 1. Syntax validation for .py files (guard: skip if original already broken)
    if resolved.endswith(".py"):
        try:
            compile(original, resolved, "exec")
        except SyntaxError:
            pass  # Original isn't valid Python -- skip gate
        else:
            syntax_error = _validate_python_syntax(updated, resolved)
            if syntax_error:
                return (False, syntax_error)

    # 2. Lint gate: run ruff on .py files (opt-in via MINI_AGENT_LINT_ON_EDIT=1)
    if resolved.endswith(".py") and os.environ.get("MINI_AGENT_LINT_ON_EDIT") == "1":
        lint_error = _run_ruff_check(updated, resolved)
        if lint_error:
            return (False, lint_error)

    # 3. Backup before write
    _backup_before_write(resolved)

    # 4. Write to disk
    try:
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(updated)
    except Exception as e:
        return (False, str(e))

    # 4. Tracking
    from tools import add_modified_file
    from core.file_context_tracker import get_tracker

    add_modified_file(resolved)
    get_tracker().mark_file_edited(resolved)
    clear_tool_cache()
    _FILE_CACHE.pop(resolved, None)
    _READ_FILES.add(resolved)

    # 5. Re-index .py files
    if resolved.endswith(".py"):
        try:
            from tools.search_ops import _reindex_file

            _reindex_file(resolved, workspace_root)
        except Exception:
            pass

    # 6. Knowledge graph invalidation
    try:
        from core.knowledge_graph import invalidate_file

        invalidate_file(resolved, workspace_root)
    except Exception:
        pass

    # 7. Auto-advance plan
    _auto_advance_plan(resolved, edit_text)

    return (True, None)


# Build fast translation tables at import time
for _cp, _replacement in _QUOTE_NORMALIZE_MAP.items():
    _QUOTE_TRANS_TABLE[_cp] = _replacement

# Unicode ws table: map cp -> replacement (or delete if None via str.maketrans)
# str.translate with a dict can map to None to delete characters
_UNICODE_WS_TRANS_TABLE.update(
    {cp: repl for cp, repl in _UNICODE_WHITESPACE_MAP.items() if repl is not None}
)
# Zero-width chars: map to None to delete
for _cp, _repl in _UNICODE_WHITESPACE_MAP.items():
    if _repl is None:
        _UNICODE_WS_TRANS_TABLE[_cp] = None


# ---------------------------------------------------------------------------
# Session undo -- backs up files before modification
# ---------------------------------------------------------------------------

_BACKUPS: dict[str, str] = {}  # resolved_path -> backup path

# Cross-turn file content cache -- avoids re-reading files whose mtime hasn't changed.
# Key: resolved path (str), Value: (content: str, mtime: float)
# Capped at _FILE_CACHE_MAX entries; oldest entries are evicted (LRU via insertion order).
_FILE_CACHE: dict[str, tuple[str, float]] = {}
_FILE_CACHE_MAX = 50


# ---------------------------------------------------------------------------
# Auto plan advancement -- after a successful write/edit, check if any
# incomplete plan step's keywords appear in the file path or edit content,
# and auto-complete it.
# ---------------------------------------------------------------------------

# Words that indicate a "read/observe" step rather than a "write/create" step.
# Auto-advance skips steps that start with these verbs, since the trigger is a
# file write/edit -- a "Read" step shouldn't auto-complete from a write action.
_AUTO_ADVANCE_READ_VERBS = frozenset(
    {
        "read",
        "review",
        "inspect",
        "check",
        "examine",
        "audit",
        "look",
        "view",
        "open",
        "browse",
        "scan",
        "search",
    }
)

_AUTO_ADVANCE_MIN_MATCH_WORDS = 2  # require at least 2 step-words in the haystack


def _auto_advance_plan(file_path: str, edit_text: str = "") -> None:
    """Check plan steps against file_path and edit_text; auto-complete matches.

    To avoid false positives, each step must have at least
    *{_AUTO_ADVANCE_MIN_MATCH_WORDS}* of its content-words
    appear as *whole words* (on word boundaries) in the combined
    file-path + edit-text haystack.  Steps whose first word is a
    "read" verb (e.g. "Read config.py") are skipped entirely
    because a write/edit trigger cannot logically complete a
    read-only step.
    """
    steps = getattr(_TOOL_CONTEXT, "_plan_steps", None)
    done = getattr(_TOOL_CONTEXT, "_plan_done", None)
    if not steps or done is None:
        return
    # Build a set of *whole* tokens from the file path + edit text.
    # Split on non-alphanumeric boundaries so "main" won't false-match
    # inside "domain.py".
    import re as _re

    _token_pat = _re.compile(r"[a-zA-Z0-9]+")
    haystack_tokens = set(_token_pat.findall((file_path + " " + edit_text).lower()))
    incomplete_indices = [i for i, _ in enumerate(steps) if i not in done]
    for idx in incomplete_indices:
        step_text = steps[idx].lower()
        step_tokens = _token_pat.findall(step_text)
        if not step_tokens:
            continue
        # Skip read-only / inspection steps
        if step_tokens[0] in _AUTO_ADVANCE_READ_VERBS:
            continue
        # Collect meaningful content words (4+ chars, skip first word which
        # is typically the action verb).
        content_words = [w for w in step_tokens[1:] if len(w) >= 4]
        if not content_words:
            # Fallback: use all tokens except the first
            content_words = step_tokens[1:] if len(step_tokens) > 1 else step_tokens
        # Count how many content words appear in the haystack (whole-word match).
        # Require at least _AUTO_ADVANCE_MIN_MATCH_WORDS matches, or all
        # available content words if there are fewer (handles simple steps
        # like "Create main.py" which only have 1 content word).
        matched = sum(1 for w in content_words if w in haystack_tokens)
        needed = min(_AUTO_ADVANCE_MIN_MATCH_WORDS, len(content_words))
        if matched >= needed:
            done.add(idx)
    _TOOL_CONTEXT._plan_done = done
    if incomplete_indices and any(i in done for i in incomplete_indices):
        _TOOL_CONTEXT._plan_last_advanced_turn = getattr(
            _TOOL_CONTEXT, "_turn_count", 0
        )

    # Persist to memory if any steps were auto-completed
    if incomplete_indices:
        try:
            from tools.agent_todos import _maybe_persist_plan

            _maybe_persist_plan()
        except ImportError:
            pass


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
# Absolute maximum (safety cap) -- never return more than this.
_ABSOLUTE_MAX_LINES = 1000


# ---------------------------------------------------------------------------
# Hash-anchor helpers -- stable word anchors for reliable edit targeting.
# Uses AnchorStateManager from core/anchor_manager.py for anchor reconciliation
# across edits.  Each line gets a unique word (e.g. "Apple§def foo():") that
# persists even when other lines shift around it.
#
# The old "hash_lines" mode (42:a1f|content) is preserved for backward compat
# but "include_anchors" is the new hot path.
# ---------------------------------------------------------------------------



# Backward-compat: old hash-line helpers still referenced by tests
def _line_hash(line: str) -> str:
    """3-char hex hash of a line (legacy helper)."""
    return hashlib.sha256(line.rstrip().encode()).hexdigest()[:3]


def _compute_line_hashes(content: str) -> list[str]:
    """Compute hashes for every line (legacy helper)."""
    return [_line_hash(line) for line in content.split("\n")]


# ---------------------------------------------------------------------------
# Line hashing (used by read_file and edit_file)
# ---------------------------------------------------------------------------


def _line_hash(line: str) -> str:
    """3-char hex hash of a line (legacy helper)."""
    return hashlib.sha256(line.rstrip().encode()).hexdigest()[:3]


def _compute_line_hashes(content: str) -> list[str]:
    """Compute hashes for every line (legacy helper)."""
    return [_line_hash(line) for line in content.split("\n")]
