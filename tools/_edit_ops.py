#!/usr/bin/env python3
"""Edit operations -- edit_file, edit_lines with fuzzy matching."""
from __future__ import annotations

import difflib
import os
import re
import sys

from tools.result import ToolResult, ErrorClass
from tools import _register, _summarize, _TOOL_CONTEXT
from tools._file_utils import (
    _canonicalize_for_match, _normalize_quotes, _normalize_unicode_whitespace,
    _validate_python_syntax, _finalize_edit, _auto_advance_plan, _backup_before_write,
    _READ_FILES, _BACKUPS, _FILE_CACHE,
    _compute_line_hashes,
)

from core.file_context_tracker import get_tracker

# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

_EditResult = tuple[str, ToolResult]  # (path, result)


def _normalize_line(s: str) -> str:
    """Collapse whitespace: Unicode ws->space, tabs->spaces, strip, collapse multiple spaces."""
    s = _normalize_unicode_whitespace(s)
    return ' '.join(s.replace('\t', '    ').split())



def _find_closest_lines(content_lines: list[str], search_lines: list[str]) -> dict | None:
    """Find the closest matching region in the file for diagnostic diff.

    Uses normalized content comparison (pass 4 style) with a sliding window.
    Returns {'line': int, 'lines': list[str], 'diff_hint': str} or None.
    """
    n_search = len(search_lines)
    n_content = len(content_lines)
    if n_search == 0 or n_content < n_search:
        return None

    norm_search = [_normalize_line(s) for s in search_lines]
    best_score = -1
    best_idx = 0

    # Score each window: count how many lines match (after normalization)
    for i in range(n_content - n_search + 1):
        window = content_lines[i:i + n_search]
        norm_window = [_normalize_line(w) for w in window]
        score = sum(1 for a, b in zip(norm_search, norm_window) if a == b)
        if score > best_score:
            best_score = score
            best_idx = i

    match_ratio = best_score / n_search if n_search > 0 else 0

    # Build a diff hint showing what's different
    diff_parts = []
    norm_content_window = [_normalize_line(l) for l in content_lines[best_idx:best_idx + n_search]]
    for j in range(n_search):
        if norm_search[j] != norm_content_window[j]:
            diff_parts.append(
                f"line {j+1}: expected '{norm_search[j][:40]}' "
                f"got '{norm_content_window[j][:40]}'"
            )

    return {
        'line': best_idx + 1,
        'lines': content_lines[best_idx:best_idx + n_search],
        'diff_hint': '; '.join(diff_parts[:5]) if diff_parts else '',
        'match_ratio': match_ratio,
        'matched_lines': best_score,
    }


def _fuzzy_find(content: str, search: str) -> tuple[int, int] | None:
    """Cascading 5-pass match for edit_file.

    1. Exact substring match.
    2. Quote-normalized match (curly->straight quotes).
    3. Trailing-whitespace-tolerant.
    4. Indentation-tolerant (full strip).
    5. Normalized-content fuzzy match (Unicode ws->space, tabs->spaces, collapsed whitespace)
       with confidence scoring (requires >=95% normalized line matches).
    """
    if not search or not content:
        return None

    # -- Line-ending normalization: CRLF -> LF -------
    content_lf = content.replace('\r\n', '\n').replace('\r', '\n')
    search_lf = search.replace('\r\n', '\n').replace('\r', '\n')

    # Pass 1: exact substring (against LF-normalized content)
    idx = content_lf.find(search_lf)
    if idx != -1:
        # Map back to original content offsets (CR removal may shift)
        return _map_lf_offset_to_original(content, search_lf, idx)

    content_lines = content_lf.split('\n')
    search_lines = search_lf.split('\n')
    if search_lines and search_lines[-1] == '':
        search_lines.pop()
    if not search_lines:
        return None

    # Pass 2: quote normalization -- try matching after normalizing curly quotes
    result = _quote_normalized_match(content_lf, search_lf, content)
    if result is not None:
        return result

    # Pass 3-4: trailing-whitespace-tolerant, then full indent-tolerant
    for trim in ('right', 'all'):
        result = _line_match(content_lines, search_lines, trim, content_lf)
        if result is not None:
            return _map_lf_region_to_original(content, result[0], result[1])

    # Pass 5: normalize whitespace on every line, then try to match
    # with confidence scoring (>=95% threshold)
    return _fuzzy_find_closest(content_lf, search_lines, content_lines, content)


def _map_lf_offset_to_original(
    original: str, search: str, lf_idx: int,
) -> tuple[int, int]:
    """Map an LF-normalized match offset back to original content offsets."""
    # Walk original content counting chars; skip CR bytes
    orig_pos = 0
    lf_pos = 0
    while lf_pos < lf_idx and orig_pos < len(original):
        if original[orig_pos] == '\r':
            orig_pos += 1
            if orig_pos < len(original) and original[orig_pos] == '\n':
                orig_pos += 1
            lf_pos += 1  # \r alone maps to \n
        else:
            orig_pos += 1
            lf_pos += 1
    start = orig_pos
    # Now find end -- search_len chars in LF space
    remaining = len(search)
    while remaining > 0 and orig_pos < len(original):
        if original[orig_pos] == '\r':
            orig_pos += 1
            if orig_pos < len(original) and original[orig_pos] == '\n':
                orig_pos += 1
        else:
            orig_pos += 1
        remaining -= 1
    return (start, orig_pos)


def _map_lf_region_to_original(
    original: str, lf_start: int, lf_end: int,
) -> tuple[int, int]:
    """Map an LF-normalized region [lf_start, lf_end) back to original offsets."""
    start = _map_lf_offset_to_original(original, "x" * (lf_end - lf_start), lf_start)[0]
    _, end = _map_lf_offset_to_original(original, "x" * (lf_end - lf_start), lf_start)
    return (start, end)


def _quote_normalized_match(
    content_lf: str, search_lf: str, original: str,
) -> tuple[int, int] | None:
    """Pass 2: try matching after normalizing curly/smart quotes to ASCII.

    Returns (start, end) in *original* content offsets.
    """
    norm_content = _normalize_quotes(content_lf)
    norm_search = _normalize_quotes(search_lf)
    idx = norm_content.find(norm_search)
    if idx != -1:
        # Map the normalized offset back through the LF content to original
        # Since quote normalization doesn't change string length (1 cp -> 1 byte
        # in these cases), the offsets are the same as LF offsets.
        return _map_lf_offset_to_original(original, search_lf, idx)
    return None


def _preserve_indentation(
    old_str: str, new_str: str, file_region: str,
) -> str:
    """Preserve the file's indentation style when applying a replacement.

    Captures the leading whitespace of each line in the matched file region
    and applies the same indentation *relative changes* to the new_string lines.
    If old_str has N lines with indentation I?...I? and new_str has M lines with
    indentation J?...J?, then for each new line k at position k in the new block:
      - if k < N: apply (J? - I?) offset relative to file's I?
      - if k >= N: apply (J??? - I???) offset relative to file's last I

    This handles the common case where the model outputs refactored code with
    spaces instead of tabs (or vice versa) and we want to match the file's style.
    """
    old_lines = old_str.split('\n')
    new_lines = new_str.split('\n')
    file_lines = file_region.split('\n')

    # Extract leading whitespace from each line
    def _leading_ws(s: str) -> str:
        m = re.match(r'^([ \t]*)', s)
        return m.group(1) if m else ''

    old_indents = [_leading_ws(l) for l in old_lines]
    new_indents = [_leading_ws(l) for l in new_lines]
    file_indents = [_leading_ws(l) for l in file_lines]

    # If all old indents are empty or single-line, no preservation needed
    if not any(old_indents) or len(old_lines) <= 1:
        return new_str

    result_lines: list[str] = []
    for k, new_line in enumerate(new_lines):
        new_ws = new_indents[k] if k < len(new_indents) else ''
        new_content = new_line[len(new_ws):]  # rest of line after indentation

        if k < len(old_indents) and k < len(file_indents):
            old_ws = old_indents[k]
            file_ws = file_indents[k]
            # Compute the relative indentation change from old->new
            if old_ws:
                # New wanted more/less indentation relative to old baseline
                if new_ws.startswith(old_ws):
                    # New has old prefix + extra: apply extra to file's indent
                    extra = new_ws[len(old_ws):]
                    result_lines.append(file_ws + extra + new_content)
                elif old_ws.startswith(new_ws):
                    # New wants less indent than old: reduce file's indent
                    remove = len(old_ws) - len(new_ws)
                    if len(file_ws) >= remove:
                        result_lines.append(file_ws[remove:] + new_content)
                    else:
                        result_lines.append(new_content)
                else:
                    # Totally different indent style: use file's indent + relative diff
                    # Count indent "levels" (tabs=1 level, 2+ spaces=1 level)
                    old_levels = _count_indent_levels(old_ws)
                    new_levels = _count_indent_levels(new_ws)
                    level_diff = new_levels - old_levels
                    new_file_levels = _count_indent_levels(file_ws) + level_diff
                    new_file_indent = _indent_from_levels(new_file_levels, file_ws)
                    result_lines.append(new_file_indent + new_content)
            else:
                # Old had no indent; apply new indent relative to file's indent
                if new_ws:
                    result_lines.append(file_ws + new_ws + new_content)
                else:
                    result_lines.append(file_ws + new_content)
        elif k < len(new_indents):
            # Extra lines beyond old: use last old->file diff
            last_idx = len(old_indents) - 1
            if last_idx >= 0 and last_idx < len(file_indents):
                old_last = old_indents[last_idx]
                file_last = file_indents[last_idx]
                level_diff = _count_indent_levels(new_indents[k]) - _count_indent_levels(old_last) if old_last else _count_indent_levels(new_indents[k])
                new_levels = _count_indent_levels(file_last) + level_diff
                result_lines.append(_indent_from_levels(new_levels, file_last) + new_content)
            else:
                result_lines.append(new_line)
        else:
            result_lines.append(new_line)

    return '\n'.join(result_lines)


def _count_indent_levels(ws: str) -> int:
    """Count indentation levels: each tab = 1 level, each 2 spaces = 1 level."""
    if not ws:
        return 0
    if '\t' in ws:
        return ws.count('\t')
    space_count = len(ws)
    # Treat each 2 spaces as 1 level (Python standard), with remainder as partial
    levels = space_count // 2
    return levels


def _indent_from_levels(levels: int, reference_ws: str) -> str:
    """Generate indentation string from level count, matching reference style."""
    if levels <= 0:
        return ''
    if '\t' in (reference_ws or ''):
        return '\t' * levels
    return ' ' * (levels * 2)


def _fuzzy_find_closest(
    content_lf: str,
    search_lines: list[str],
    content_lines: list[str],
    original: str,
    confidence_threshold: float = 0.95,
) -> tuple[int, int] | None:
    """Pass 5: normalize all whitespace on every line, sliding-window match.

    Normalizes both search and content lines by collapsing whitespace
    (Unicode ws->space, tabs->spaces, strip, collapse multiple spaces).
    Requires a unique match -- if multiple windows match, returns None.
    Also enforces a confidence threshold: the best match must have >=95% of
    normalized lines matching exactly.  If below threshold, returns None
    so the caller can report the near-miss with a score.

    Returns None on ambiguous or low-confidence matches.
    """
    norm_search = [_normalize_line(s) for s in search_lines]
    n_search = len(search_lines)
    n_content = len(content_lines)
    if n_search == 0 or n_content < n_search:
        return None

    match_start = None
    best_score = -1
    best_idx = 0

    for i in range(n_content - n_search + 1):
        window = content_lines[i:i + n_search]
        norm_window = [_normalize_line(w) for w in window]
        score = sum(1 for a, b in zip(norm_search, norm_window) if a == b)
        if score > best_score:
            best_score = score
            best_idx = i
            match_start = None  # reset ambiguity
        if norm_window == norm_search:
            if match_start is not None:
                return None  # ambiguous -- multiple exact normalized matches
            match_start = i

    # If we have a unique exact normalized match, use it regardless of score
    if match_start is not None:
        start_byte = sum(len(line) + 1 for line in content_lines[:match_start])
        end_byte = start_byte + sum(
            len(line) + 1 for line in content_lines[match_start:match_start + n_search]
        )
        if end_byte > start_byte and content_lf[end_byte - 1:end_byte] == '\n':
            end_byte -= 1
        return _map_lf_region_to_original(original, start_byte, end_byte)

    # No exact normalized match -- check confidence threshold
    confidence = best_score / n_search if n_search > 0 else 0.0
    if confidence < confidence_threshold:
        return None  # below threshold, let caller report near-miss

    # Above threshold but not exact -- use best match
    # (this handles near-perfect matches with minor whitespace differences)
    start_byte = sum(len(line) + 1 for line in content_lines[:best_idx])
    end_byte = start_byte + sum(
        len(line) + 1 for line in content_lines[best_idx:best_idx + n_search]
    )
    if end_byte > start_byte and content_lf[end_byte - 1:end_byte] == '\n':
        end_byte -= 1
    return _map_lf_region_to_original(original, start_byte, end_byte)


def _line_match(content_lines, search_lines, trim, content=''):
    normalize = str.rstrip if trim == 'right' else str.strip
    n_search = len(search_lines)
    n_content = len(content_lines)
    norm_search = [normalize(s) for s in search_lines]
    match_start = None
    for i in range(n_content - n_search + 1):
        window = content_lines[i:i + n_search]
        if [normalize(w) for w in window] == norm_search:
            if match_start is not None:
                return None
            match_start = i
    if match_start is None:
        return None
    start_byte = sum(len(line) + 1 for line in content_lines[:match_start])
    end_byte = start_byte + sum(len(line) + 1 for line in content_lines[match_start:match_start + n_search])
    if end_byte > start_byte and content[end_byte - 1:end_byte] == '\n':
        end_byte -= 1
    return (start_byte, end_byte)



def _apply_single_edit(
    path: str,
    old: str,
    new: str,
    count: int,
    preview: bool,
    wg: WriteSafetyGate,
    args: dict,
) -> _EditResult:
    """Apply an edit to a single file. Returns (path, ToolResult)."""
    if not old:
        return (path, ToolResult(
            success=False,
            content="edit_file: 'old_string' must not be empty.",
        ))
    safety_result = wg.check(path)
    if not safety_result.allowed:
        return (path, ToolResult(
            success=False,
            content=f"Edit blocked by safety layer: {safety_result.reason}",
        ))
    resolved = safety_result.resolved_path

    # --- Read-before-edit enforcement ---
    if resolved not in _READ_FILES:
        return (path, ToolResult(
            success=False,
            content=(
                f"Edit blocked: '{resolved}' has not been read yet in this session.\n"
                f"Use read_file first to read the file before editing it.\n"
                f"This ensures the model sees the current file content and can construct\n"
                f"an accurate old_string for matching."
            ),
        ))

    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            original = f.read()
        diff = wg.generate_diff("edit_file", args)
        _backup_before_write(resolved)
        match = _fuzzy_find(original, old)
        if match is None:
            # Search for similar substrings to help the agent self-correct
            candidates: list[str] = []
            old_first_line = old.split("\n")[0].strip()
            for lineno, line in enumerate(original.split("\n"), 1):
                if old_first_line and old_first_line[:30] in line:
                    candidates.append(f"  line {lineno}: {line.rstrip()[:120]}")
                if len(candidates) >= 3:
                    break
            # Build diagnostic: find the closest matching lines and show diff
            _old_lines = old.split('\n')
            _content_lines = original.split('\n')
            best_match = _find_closest_lines(_content_lines, _old_lines)
            hint = (
                f"Edit failed: old_string not found in '{resolved}'.\n"
                f"Hint: The string must match exactly -- check whitespace, indentation, "
                f"and line endings. Try read_file first to verify the exact text."
            )
            if best_match:
                # Show confidence score for the closest match
                n_search = len(_old_lines)
                if n_search > 0 and best_match.get('match_ratio', 0) > 0:
                    pct = int(best_match['match_ratio'] * 100)
                    hint += (
                        f"\n\nClosest match found at line {best_match['line']} "
                        f"(confidence: {pct}%, {best_match.get('matched_lines', 0)}/{n_search} lines):"
                    )
                else:
                    hint += f"\n\nClosest match found around line {best_match['line']}:"
                hint += f"\n  Expected ({len(_old_lines)} lines):\n"
                for ol in _old_lines[:10]:
                    hint += f"    | {ol.rstrip()}\n"
                if len(_old_lines) > 10:
                    hint += f"    ... ({len(_old_lines) - 10} more lines omitted)\n"
                hint += f"  Actual (file at line {best_match['line']}):\n"
                for fl in best_match['lines'][:10]:
                    hint += f"    | {fl.rstrip()}\n"
                if len(best_match['lines']) > 10:
                    hint += f"    ... ({len(best_match['lines']) - 10} more lines omitted)\n"
                if best_match['diff_hint']:
                    hint += f"\nDifferences: {best_match['diff_hint']}"
            if candidates:
                hint += "\nSimilar lines found (did you mean one of these?):\n" + "\n".join(candidates)
            if old_first_line:
                try:
                    memory = getattr(_TOOL_CONTEXT, "_memory_store", None)
                    if memory is not None:
                        memory.add_knowledge(
                            category="pattern",
                            summary=f"edit_file mismatch: {old_first_line[:80]}",
                            detail=f"File: {resolved}. Could not find exact match for old_string.",
                        )
                except Exception as exc:
                    print(f"  WARNING: backup skipped: {exc}", file=sys.stderr, flush=True)
            return (path, ToolResult(success=False, content=hint))

        if count == -1:
            occurrences = original.count(old)
            # Apply indentation preservation per occurrence (reverse walk so
            # offsets stay stable for each replacement).
            updated = original
            for _ in range(occurrences):
                occ_match = _fuzzy_find(updated, old)
                if occ_match is None:
                    break  # shouldn't happen, but be safe
                start, end = occ_match
                matched_region = updated[start:end]
                preserved_new = _preserve_indentation(old, new, matched_region)
                updated = updated[:start] + preserved_new + updated[end:]
            replaced = occurrences
        elif count >= 1:
            start, end = match
            # --- Indentation preservation ---
            # Capture the matched region from the original file and apply
            # indentation preservation to the new_string to match the file's style.
            matched_region = original[start:end]
            preserved_new = _preserve_indentation(old, new, matched_region)
            updated = original[:start] + preserved_new + original[end:]
            replaced = 1
        else:
            return (path, ToolResult(success=False, content=f"Invalid count: {count}. Use a positive integer or -1 (all)."))

        if preview:
            raw_diff = wg._format_diff(resolved, original, updated)
            return (path, ToolResult(
                success=True,
                content=f"Preview: proposed edit to {resolved}\n{raw_diff}",
            ))

        ok, err = _finalize_edit(resolved, original, updated, wg.workspace_root)
        if not ok:
            return (path, ToolResult(success=False, content=err or "Edit failed"))

        added = updated.count("\n") - original.count("\n")
        label = f"{replaced} occurrence(s)" if replaced > 1 else "1 occurrence"
        return (path, ToolResult(
            success=True,
            content=(
                f"OK: replaced {label} in {resolved}"
                + (f" (+{added} lines)" if added > 0 else f" ({added} lines)" if added < 0 else "")
            ),
            diff_preview=diff.preview_text if diff.changed else None,
        ))
    except Exception as e:
        return (path, ToolResult(
            success=False,
            content=f"Error editing '{resolved}': {e}",
        ))


@_register("edit_file")
def _edit_file(args: dict, wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    # --- Anchor-based edit mode (new hot path) ---
    files = args.get("files", None)
    if files is not None:
        return _edit_file_anchored(files, wg, _rg)

    # --- Legacy old_string/new_string mode ---
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    count = args.get("count", 1)
    preview = args.get("preview", False)
    paths = args.get("paths", None)

    if paths is not None:
        # Batch edit: apply same old->new to all paths
        if not isinstance(paths, list) or not paths:
            return ToolResult(
                success=False,
                content="'paths' must be a non-empty list of file paths.",
            )
        results: list[_EditResult] = []
        for p in paths:
            result = _apply_single_edit(p, old, new, count, preview, wg, {**args, "path": p})
            results.append(result)
        all_ok = all(r.success for _, r in results)
        lines: list[str] = []
        failures: list[str] = []
        for p, r in results:
            first_line = r.content.split("\n")[0]
            if r.success:
                lines.append(f"  [OK] {p}: {first_line}")
            else:
                lines.append(f"  [FAIL] {p}: {first_line}")
                failures.append(p)
        summary = "Batch edit results:\n" + "\n".join(lines)
        if all_ok:
            return ToolResult(success=True, content=summary)
        else:
            return ToolResult(
                success=False,
                content=summary + f"\n\nFailed paths: {failures}",
            )
    else:
        path = args["path"]
        result = _apply_single_edit(path, old, new, count, preview, wg, args)
        return result[1]


def _edit_file_anchored(
    files: list[dict],
    wg: WriteSafetyGate,
    _rg: ReadSafetyGate,
) -> ToolResult:
    """Apply anchor-based edits to one or more files.

    Each file dict: {path, edits: [{anchor, end_anchor?, edit_type?, text}]}

    All anchors are validated before any edit is applied.
    """
    from core.anchor_manager import (
        AnchorStateManager,
        resolve_anchored_edits,
        apply_resolved_edits,
        strip_anchors,
    )
    import difflib as _difflib

    if not isinstance(files, list) or not files:
        return ToolResult(success=False, content="'files' must be a non-empty array.")

    # Phase 1: Read all files and prepare edits (syntax-validated) (with syntax validation)
    file_states: list[dict] = []  # {resolved, display, lines, anchors, edits}
    all_failures: list[str] = []

    for file_entry in files:
        path = file_entry.get("path", "")
        edits = file_entry.get("edits", [])

        if isinstance(edits, str):
            import json as _json
            try:
                edits = _json.loads(edits)
            except Exception:
                all_failures.append(f"{path}: 'edits' must be a valid JSON array")
                continue

        if not isinstance(edits, list) or not edits:
            all_failures.append(f"{path}: 'edits' must be a non-empty array")
            continue

        safety_result = wg.check(path)
        if not safety_result.allowed:
            all_failures.append(f"{path}: blocked by safety layer: {safety_result.reason}")
            continue

        resolved = safety_result.resolved_path

        # Read-before-edit enforcement
        if resolved.endswith(".py") and resolved not in _READ_FILES:
            all_failures.append(
                f"{path}: not read yet this session. Use read_file(include_anchors=True) first."
            )
            continue

        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            all_failures.append(f"{path}: error reading file: {e}")
            continue

        lines = content.split("\n")
        anchors = AnchorStateManager.reconcile(resolved, lines)

        file_states.append({
            "resolved": resolved,
            "display": path,
            "lines": lines,
            "anchors": anchors,
            "edits": edits,
        })

    if not file_states and all_failures:
        return ToolResult(success=False, content="All files failed validation:\n" + "\n".join(all_failures))

    # Phase 2: Resolve edits (validate anchors match actual content)
    all_resolved: list[dict] = []  # {file_idx, resolved_edits, failed_edits}

    for idx, fs in enumerate(file_states):
        resolved, failed = resolve_anchored_edits(fs["edits"], fs["lines"], fs["anchors"])
        all_resolved.append({
            "file_idx": idx,
            "resolved_edits": resolved,
            "failed_edits": failed,
        })

    # Check for failures
    total_failed = sum(len(ar["failed_edits"]) for ar in all_resolved)
    if total_failed > 0:
        failure_msgs: list[str] = []
        for ar in all_resolved:
            fs = file_states[ar["file_idx"]]
            for fe in ar["failed_edits"]:
                failure_msgs.append(f"{fs['display']}: {fe['error']}")
        return ToolResult(
            success=False,
            content="Anchor validation failed -- no edits were applied:\n" + "\n".join(failure_msgs),
        )

    # Phase 3: Apply edits (already validated, so this should succeed)
    results: list[str] = []
    all_diffs: list[str] = []

    for ar in all_resolved:
        fs = file_states[ar["file_idx"]]
        resolved = ar["resolved_edits"]

        if not resolved:
            continue

        new_lines, applied = apply_resolved_edits(fs["lines"], resolved)
        new_content = "\n".join(new_lines)
        orig_content = "\n".join(fs["lines"])

        ok, err = _finalize_edit(fs["resolved"], orig_content, new_content, wg.workspace_root)
        if not ok:
            results.append(f"[FAIL] {fs['display']}: {err}")
            all_failures.append(err or "Edit failed")
            continue

        # Reconcile anchors with new content
        AnchorStateManager.reconcile(fs["resolved"], new_lines)

        # Generate diff
        orig_lines = fs["lines"]
        diff_lines = list(_difflib.unified_diff(
            orig_lines, new_lines,
            fromfile=fs["display"], tofile=fs["display"],
            lineterm="",
        ))
        diff_text = "\n".join(diff_lines) if diff_lines else "(no visible change)"

        additions = sum(e["lines_added"] for e in applied)
        deletions = sum(e["lines_deleted"] for e in applied)
        stats = f" (+{additions}, -{deletions})" if additions or deletions else ""
        results.append(
            f"[OK] {fs['display']}{stats}: {len(resolved)} edit(s) applied"
        )
        all_diffs.append(f"--- {fs['display']} ---\n{diff_text}")

    final = "\n".join(results)
    if all_diffs:
        final += "\n\n--- Diffs ---\n" + "\n\n".join(all_diffs)
    return ToolResult(success=True, content=final)


@_summarize("edit_file")
def _edit_file_summary(args: dict) -> str:
    # Anchor-based multi-file edit mode
    files = args.get("files", [])
    if files:
        total_edits = sum(len(f.get("edits", [])) for f in files)
        # Show per-edit anchor details for the first few edits
        edit_previews = []
        for f in files[:2]:  # max 2 files in preview
            fpath = f["path"]
            for ed in f.get("edits", [])[:3]:  # max 3 edits per file in preview
                anchor = ed.get("anchor", "")
                # Extract just the anchor word (before §) for readability
                anchor_word = anchor.split("\u00a7")[0] if "\u00a7" in anchor else anchor[:20]
                new_text = ed.get("text", "")
                new_preview = new_text[:60].replace("\n", "\\n").strip()
                if len(new_text) > 60:
                    new_preview += "..."
                edit_previews.append(f"{anchor_word}\u2192\"{new_preview}\"")
        preview_str = ", ".join(edit_previews)
        if total_edits > len(edit_previews):
            preview_str += f", +{total_edits - len(edit_previews)} more"
        return f"edit_file({len(files)} file(s), {total_edits} edits: {preview_str})"

    # Legacy mode with paths[]
    paths = args.get("paths", [])
    if paths:
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        old_preview = old[:80].replace("\n", "\\n")
        if len(old) > 80:
            old_preview += "..."
        new_preview = new[:60].replace("\n", "\\n")
        if len(new) > 60:
            new_preview += "..."
        preview_flag = args.get("preview", False)
        suffix = " [preview]" if preview_flag else ""
        return f"edit_file({len(paths)} files: {', '.join(paths[:3])}) \"{old_preview}\" -> \"{new_preview}\"{suffix}"

    # Single-file legacy mode
    path = args.get("path", "?")
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    old_preview = old[:80].replace("\n", "\\n")
    if len(old) > 80:
        old_preview += "..."
    new_preview = new[:60].replace("\n", "\\n")
    if len(new) > 60:
        new_preview += "..."
    preview_flag = args.get("preview", False)
    suffix = " [preview]" if preview_flag else ""
    return f"edit_file({path}) \"{old_preview}\" -> \"{new_preview}\"{suffix}"


# ---------------------------------------------------------------------------
# edit_lines -- hash-anchored editing (Hashlines pattern from Akay/Howard Chen)
# ---------------------------------------------------------------------------

@_register("edit_lines")
def _edit_lines(args: dict, wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    '''Replace line ranges using hash anchors for reliable first-attempt edits.

    Each edit specifies {from, from_hash, to, to_hash, new_text}.
    The file is re-read fresh; hashes are recomputed and validated before
    any edit is applied.  Edits are applied bottom-up so line numbers
    in the edits array can refer to the pre-edit file.

    On any hash mismatch the ENTIRE batch is rejected with a precise error.
    '''
    path = args["path"]
    edits = args["edits"]

    safety_result = wg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Edit blocked by safety layer: {safety_result.reason}",
        )
    resolved = safety_result.resolved_path

    if not isinstance(edits, list) or not edits:
        return ToolResult(success=False, content="'edits' must be a non-empty list.")

    # --- Read-before-edit enforcement ---
    if resolved not in _READ_FILES:
        return ToolResult(
            success=False,
            content=(
                f"Edit blocked: '{resolved}' has not been read yet in this session.\n"
                f"Use read_file(hash_lines=True) first to see the current content "
                f"with hash anchors before constructing edit_lines calls."
            ),
        )

    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            original = f.read()
    except Exception as e:
        return ToolResult(success=False, content=f"Error reading '{resolved}': {e}")

    lines = original.split("\n")
    hashes = _compute_line_hashes(original)

    # --- Validate all hash anchors first ---
    for i, edit in enumerate(edits):
        for endpoint, label in [("from", "from"), ("to", "to")]:
            line_num = edit.get(endpoint)
            claimed_hash = edit.get(f"{label}_hash")
            if line_num is None or claimed_hash is None:
                return ToolResult(
                    success=False,
                    content=f"edit_lines: edit[{i}] missing '{endpoint}' or '{label}_hash'.",
                )
            # 1-indexed -> 0-indexed
            idx = line_num - 1
            if idx < 0 or idx >= len(lines):
                return ToolResult(
                    success=False,
                    content=(
                        f"edit_lines: edit[{i}] {label}={line_num} is out of range "
                        f"(file has {len(lines)} lines)."
                    ),
                )
            actual_hash = hashes[idx]
            if claimed_hash != actual_hash:
                return ToolResult(
                    success=False,
                    content=(
                        f"edit_lines: edit[{i}] {label} line {line_num} hash mismatch "
                        f"-- claimed '{claimed_hash}', actual '{actual_hash}'.\n"
                        f"Current line {line_num}: {lines[idx][:120]}"
                    ),
                )

    # --- Apply edits bottom-up (reverse order by line number) ---
    sorted_edits = sorted(enumerate(edits), key=lambda x: x[1]["from"], reverse=True)
    updated_lines = list(lines)

    for orig_idx, edit in sorted_edits:
        from_line = edit["from"] - 1  # 0-indexed
        to_line = edit["to"] - 1      # 0-indexed
        new_text = edit["new_text"]
        new_lines = new_text.split("\n")

        # Validate: from must be <= to
        if from_line > to_line:
            return ToolResult(
                success=False,
                content=(
                    f"edit_lines: edit[{orig_idx}] from={from_line + 1} > to={to_line + 1}. "
                    f"'from' must be <= 'to'."
                ),
            )

        updated_lines[from_line:to_line + 1] = new_lines

    updated = "\n".join(updated_lines)

    # Build edit_text from new_text fields for auto-advance matching
    _edit_text = " ".join(e.get("new_text", "") for e in edits)

    ok, err = _finalize_edit(resolved, original, updated, wg.workspace_root, edit_text=_edit_text)
    if not ok:
        return ToolResult(success=False, content=err or "Edit failed")

    diff = wg.generate_diff("edit_lines", args)

    added = len(updated_lines) - len(lines)
    label = "s" if len(edits) != 1 else ""
    return ToolResult(
        success=True,
        content=(
            f"OK: applied {len(edits)} edit{label} to {resolved}"
            + (f" (+{added} lines)" if added > 0 else f" ({added} lines)" if added < 0 else "")
        ),
        diff_preview=diff.preview_text if diff.changed else None,
    )


@_summarize("edit_lines")
def _edit_lines_summary(args: dict) -> str:
    path = args.get("path", "?")
    edits = args.get("edits", [])
    # Show per-edit hash anchors and line ranges
    edit_previews = []
    for ed in edits[:5]:
        from_line = ed.get("from", "?")
        to_line = ed.get("to", "?")
        from_hash = ed.get("from_hash", "")[:6] + "..." if len(ed.get("from_hash", "")) > 6 else ed.get("from_hash", "")
        to_hash = ed.get("to_hash", "")[:6] + "..." if len(ed.get("to_hash", "")) > 6 else ed.get("to_hash", "")
        new_len = len(ed.get("new_text", ""))
        if to_line == from_line:
            range_str = f"L{from_line}"
        else:
            range_str = f"L{from_line}-{to_line}"
        hash_str = f"h={from_hash}" if from_hash else ""
        edit_previews.append(f"{range_str} {hash_str} +{new_len}B")
    preview = ", ".join(edit_previews)
    if len(edits) > 5:
        preview += f", +{len(edits) - 5} more"
    return f"edit_lines({path}, {len(edits)} edits: {preview})"


