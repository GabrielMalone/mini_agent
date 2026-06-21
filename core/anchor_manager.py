#!/usr/bin/env python3
"""
anchor_manager.py -- Stable word anchors for file lines, inspired by Dirac.

Each line in a tracked file gets a unique single-word anchor (e.g., "Apple",
"Banana") from a dictionary. Anchors persist across edits via Myers Diff
reconciliation: unchanged lines keep their anchors; new lines get fresh ones.

LLMs reference these anchors with edit_file instead of brittle line numbers or
fragile old_string matching. The backend validates both the anchor name AND the
line content after the § delimiter before applying edits.

Architecture:
    AnchorStateManager  -- task-scoped LRU cache of (filepath -> TrackedDocument)
    reconcile()         -- diff current content against last-known state
    format_line()       -- prepend "Anchor§" to a line for LLM visibility
    split_anchor()      -- parse "Anchor§content" back into (anchor, content)
"""

from __future__ import annotations

import difflib
import hashlib
import os
import random
import threading
from typing import Optional

# ---------------------------------------------------------------------------
# FNV-1a 32-bit hash (matching Dirac's integer hash approach)
# ---------------------------------------------------------------------------


def _fnv1a_32(s: str) -> int:
    """FNV-1a 32-bit hash of a string."""
    h = 0x811C9DC5
    for ch in s:
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    return h


def content_hash(text: str) -> str:
    """Short hex content hash for change detection (SHA-256 first 6 chars)."""
    return hashlib.sha256(text.encode()).hexdigest()[:6]


# ---------------------------------------------------------------------------
# Anchor delimiter
# ---------------------------------------------------------------------------

ANCHOR_DELIMITER = "\u00a7"  # § -- section sign, unlikely in source code


def format_line_for_model(line: str, anchor: str, reveal: bool = True) -> str:
    """Format a line with its anchor for LLM consumption.

    When reveal=True:  "Apple§def process_data(items):"
    When reveal=False: "def process_data(items):"
    """
    if reveal:
        return f"{anchor}{ANCHOR_DELIMITER}{line}"
    return line


def format_lines_for_model(
    lines: list[str],
    anchors: list[str],
    reveal: bool = True,
) -> str:
    """Format all lines with anchors."""
    return "\n".join(
        format_line_for_model(
            line, anchors[i] if i < len(anchors) else f"L{i + 1}", reveal
        )
        for i, line in enumerate(lines)
    )


def split_anchor(line: str) -> tuple[str, str]:
    """Split "Anchor§content" into (anchor, content).

    If the line doesn't have the delimiter, returns ("", line).
    """
    idx = line.find(ANCHOR_DELIMITER)
    if idx == -1:
        return ("", line)
    return (line[:idx], line[idx + 1 :])


def strip_anchors(text: str) -> str:
    """Remove anchor prefixes from all lines in text (for display)."""
    result: list[str] = []
    for line in text.split("\n"):
        _, content = split_anchor(line)
        result.append(content)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Word dictionary
# ---------------------------------------------------------------------------


def _load_dictionary() -> list[str]:
    """Load the anchor word dictionary from disk."""
    dict_path = os.path.join(os.path.dirname(__file__), "anchor_words.txt")
    try:
        with open(dict_path, encoding="utf-8") as f:
            return [w.strip() for w in f if w.strip()]
    except FileNotFoundError:
        # Fallback: generate from common short words
        return [
            "ace",
            "act",
            "add",
            "age",
            "ago",
            "aid",
            "aim",
            "air",
            "ale",
            "ape",
            "arc",
            "arm",
            "art",
            "ash",
            "ate",
            "awe",
            "axe",
            "bad",
            "bag",
            "ban",
            "bar",
            "bat",
            "bay",
            "bed",
            "bet",
            "bid",
            "big",
            "bin",
            "bit",
            "bow",
        ]


# ---------------------------------------------------------------------------
# TrackedDocument -- per-file state
# ---------------------------------------------------------------------------


class TrackedDocument:
    """State for a single file being tracked with anchors."""

    __slots__ = ("hashes", "anchors", "used_words", "available_pool")

    def __init__(
        self,
        hashes: list[int],
        anchors: list[str],
        used_words: set[str],
        available_pool: list[str],
    ):
        self.hashes = hashes
        self.anchors = anchors
        self.used_words = used_words
        self.available_pool = available_pool


# ---------------------------------------------------------------------------
# AnchorStateManager
# ---------------------------------------------------------------------------


class AnchorStateManager:
    """Task-scoped manager for stable line anchors."""

    # Class-level state: task_id -> (filepath -> TrackedDocument)
    _storage: dict[str, dict[str, TrackedDocument]] = {}
    _lock = threading.Lock()
    _dictionary: Optional[list[str]] = None

    MAX_TRACKED_LINES = 50_000
    MAX_TRACKED_FILES = 512
    MAX_TRACKED_TASKS = 50

    # ------------------------------------------------------------------
    # Dictionary
    # ------------------------------------------------------------------

    @classmethod
    def get_dictionary(cls) -> list[str]:
        if cls._dictionary is None:
            cls._dictionary = _load_dictionary()
        return cls._dictionary

    @classmethod
    def _get_unique_word(cls, used: set[str], pool: list[str]) -> str:
        """Get a word not currently in *used*, refilling pool as needed."""
        while pool:
            w = pool.pop()
            if w not in used:
                return w
        # Pool exhausted -- generate synthetic words
        dict_words = cls.get_dictionary()
        for w in dict_words:
            if w not in used:
                return w
        # Absolute fallback
        for i in range(100000):
            w = f"W{i:05d}"
            if w not in used:
                return w
        return "FALLBACK"

    # ------------------------------------------------------------------
    # Task state management
    # ------------------------------------------------------------------

    @classmethod
    def _get_task_state(cls, task_id: Optional[str]) -> dict[str, TrackedDocument]:
        tid = task_id or "__default__"
        with cls._lock:
            if tid not in cls._storage:
                cls._storage[tid] = {}
            state = cls._storage[tid]
            # LRU eviction: too many tasks
            if len(cls._storage) > cls.MAX_TRACKED_TASKS:
                oldest = next(iter(cls._storage))
                if oldest != tid:
                    del cls._storage[oldest]
            return state

    @classmethod
    def _update_state(
        cls, absolute_path: str, doc: TrackedDocument, task_id: Optional[str] = None
    ) -> None:
        state = cls._get_task_state(task_id)
        with cls._lock:
            # Delete and re-insert for LRU ordering
            state.pop(absolute_path, None)
            state[absolute_path] = doc
            # Evict oldest if limit exceeded
            if len(state) > cls.MAX_TRACKED_FILES:
                oldest_key = next(iter(state))
                if oldest_key != absolute_path:
                    state.pop(oldest_key, None)

    # ------------------------------------------------------------------
    # Hash computation
    # ------------------------------------------------------------------

    @classmethod
    def _compute_hashes(cls, lines: list[str]) -> list[int]:
        """Compute FNV-1a hashes for all lines (with trailing ws stripped)."""
        return [_fnv1a_32(line.rstrip()) for line in lines]

    # ------------------------------------------------------------------
    # Myers Diff reconciliation
    # ------------------------------------------------------------------

    @classmethod
    def reconcile(
        cls,
        absolute_path: str,
        current_lines: list[str],
        task_id: Optional[str] = None,
    ) -> list[str]:
        """Reconcile current file content with saved state.

        Uses difflib.SequenceMatcher (Myers Diff) on hash arrays to detect
        unchanged vs. changed regions.  Unchanged lines keep their anchors;
        new lines get fresh words from the pool.

        Returns anchor list parallel to *current_lines*.
        """
        # Safeguard for massive files
        if len(current_lines) > cls.MAX_TRACKED_LINES:
            return [f"L{i + 1}" for i in range(len(current_lines))]

        state = cls._get_task_state(task_id)
        current_hashes = cls._compute_hashes(current_lines)

        with cls._lock:
            tracked = state.get(absolute_path)

        # Fast path: hashes identical -> nothing changed
        if tracked and len(tracked.hashes) == len(current_hashes):
            if tracked.hashes == current_hashes:
                cls._update_state(absolute_path, tracked, task_id)
                return list(tracked.anchors)

        # First time seeing this file
        if not tracked:
            used_words: set[str] = set()
            pool = list(cls.get_dictionary())
            random.shuffle(pool)
            anchors = [cls._get_unique_word(used_words, pool) for _ in current_lines]
            for w in anchors:
                used_words.add(w)
            doc = TrackedDocument(
                hashes=list(current_hashes),
                anchors=list(anchors),
                used_words=used_words,
                available_pool=pool,
            )
            cls._update_state(absolute_path, doc, task_id)
            return anchors

        # Myers Diff on hash sequences
        sm = difflib.SequenceMatcher(
            a=[str(h) for h in tracked.hashes],
            b=[str(h) for h in current_hashes],
            autojunk=False,
        )
        opcodes = sm.get_opcodes()

        new_anchors: list[str] = []
        new_used_words = set(tracked.used_words)
        pool = list(tracked.available_pool)

        # Refill pool if needed
        if len(pool) < 100:
            all_words = set(cls.get_dictionary())
            available = [w for w in all_words if w not in new_used_words]
            random.shuffle(available)
            pool = available + pool

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                # Unchanged lines CARRY OVER the exact same anchors
                for k in range(i1, i2):
                    new_anchors.append(tracked.anchors[k])
                    new_used_words.add(tracked.anchors[k])
            elif tag == "replace":
                # Both sides changed -- new anchors for the new lines
                for _ in range(j1, j2):
                    w = cls._get_unique_word(new_used_words, pool)
                    new_anchors.append(w)
                    new_used_words.add(w)
                # Old words are NOT added to used_words (they're retired)
            elif tag == "delete":
                # Lines removed -- nothing to add to new_anchors
                pass
            elif tag == "insert":
                # New lines inserted -- fresh anchors
                for _ in range(j1, j2):
                    w = cls._get_unique_word(new_used_words, pool)
                    new_anchors.append(w)
                    new_used_words.add(w)

        doc = TrackedDocument(
            hashes=list(current_hashes),
            anchors=new_anchors,
            used_words=new_used_words,
            available_pool=pool,
        )
        cls._update_state(absolute_path, doc, task_id)
        return new_anchors

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @classmethod
    def is_tracking(cls, absolute_path: str, task_id: Optional[str] = None) -> bool:
        state = cls._get_task_state(task_id)
        with cls._lock:
            return absolute_path in state

    @classmethod
    def get_anchors(
        cls, absolute_path: str, task_id: Optional[str] = None
    ) -> Optional[list[str]]:
        state = cls._get_task_state(task_id)
        with cls._lock:
            doc = state.get(absolute_path)
            return list(doc.anchors) if doc else None

    @classmethod
    def clear_state(cls, absolute_path: str, task_id: Optional[str] = None) -> None:
        state = cls._get_task_state(task_id)
        with cls._lock:
            state.pop(absolute_path, None)

    @classmethod
    def reset(cls, task_id: Optional[str] = None) -> None:
        with cls._lock:
            if task_id:
                cls._storage.pop(task_id, None)
            else:
                cls._storage.clear()


# ---------------------------------------------------------------------------
# Anchor resolution for edits
# ---------------------------------------------------------------------------


def resolve_anchored_edits(
    edits: list[dict],
    lines: list[str],
    anchors: list[str],
) -> tuple[list[dict], list[dict]]:
    """Resolve anchor-based edits against current file state.

    Each edit dict:
        {
            "anchor": str,           # anchor word + § + expected line content
            "end_anchor": str,       # (optional) for multi-line edits
            "edit_type": "replace" | "insert_after" | "insert_before",
            "text": str,             # replacement or insertion text
        }

    Returns (resolved_edits, failed_edits).
    resolved_edits each have: line_idx, end_idx, edit
    """
    resolved: list[dict] = []
    failed: list[dict] = []

    # Build lookup: anchor_word -> line_index
    anchor_map: dict[str, int] = {}
    for i, anchor in enumerate(anchors):
        anchor_map[anchor] = i

    for edit in edits:
        anchor_raw = edit.get("anchor", "")
        anchor_word, anchor_content = split_anchor(anchor_raw)

        if not anchor_word:
            failed.append({"edit": edit, "error": "Missing or empty 'anchor' field"})
            continue

        if anchor_word not in anchor_map:
            # Find closest matching anchor for a helpful error
            similar = difflib.get_close_matches(
                anchor_word, list(anchor_map), n=3, cutoff=0.0
            )
            hint = f" (did you mean {', '.join(similar)}?)" if similar else ""
            failed.append(
                {
                    "edit": edit,
                    "error": f"Anchor '{anchor_word}' not found in file{hint}. The file may have changed. Re-read it first.",
                }
            )
            continue

        line_idx = anchor_map[anchor_word]

        # Validate anchor content matches the actual line
        actual_line = lines[line_idx]
        if anchor_content and anchor_content != actual_line:
            failed.append(
                {
                    "edit": edit,
                    "error": (
                        f"Anchor '{anchor_word}' found at line {line_idx + 1}, "
                        f"but the content after '{ANCHOR_DELIMITER}' doesn't match. "
                        f"Expected: '{anchor_content[:60]}...' "
                        f"Got: '{actual_line[:60]}...'"
                    ),
                }
            )
            continue

        edit_type = edit.get("edit_type", "replace")

        if edit_type in ("insert_after", "insert_before"):
            resolved.append(
                {
                    "line_idx": line_idx,
                    "end_idx": line_idx,
                    "edit": edit,
                }
            )
            continue

        # replace: resolve end_anchor
        end_raw = edit.get("end_anchor", "")
        if end_raw:
            end_word, end_content = split_anchor(end_raw)
            if end_word not in anchor_map:
                failed.append(
                    {
                        "edit": edit,
                        "error": f"End anchor '{end_word}' not found in file",
                    }
                )
                continue
            end_idx = anchor_map[end_word]
        else:
            # Single-line replacement
            end_idx = line_idx

        if end_idx < line_idx:
            failed.append(
                {
                    "edit": edit,
                    "error": f"end_anchor line ({end_idx + 1}) is before anchor line ({line_idx + 1})",
                }
            )
            continue

        resolved.append(
            {
                "line_idx": line_idx,
                "end_idx": end_idx,
                "edit": edit,
            }
        )

    return resolved, failed


def apply_resolved_edits(
    lines: list[str],
    resolved_edits: list[dict],
) -> tuple[list[str], list[dict]]:
    """Apply resolved edits to lines, bottom-up so indices stay stable.

    Returns (new_lines, applied_edits).
    applied_edits each have: start_idx, end_idx, original_start_idx,
    original_end_idx, edit, lines_added, lines_deleted
    """
    # Sort bottom-up (descending by line_idx) so we don't shift indices
    sorted_edits = sorted(resolved_edits, key=lambda e: e["line_idx"], reverse=True)

    new_lines = list(lines)
    applied: list[dict] = []

    for re in sorted_edits:
        edit = re["edit"]
        edit_type = edit.get("edit_type", "replace")
        text = edit.get("text", "")
        new_text_lines = text.split("\n") if text else []

        if edit_type == "insert_before":
            line_idx = re["line_idx"]
            original_start = line_idx
            original_end = line_idx
            for i, nl in enumerate(new_text_lines):
                new_lines.insert(line_idx + i, nl)
            lines_added = len(new_text_lines)
            lines_deleted = 0
            applied.append(
                {
                    "start_idx": line_idx,
                    "end_idx": line_idx + lines_added - 1,
                    "original_start_idx": original_start,
                    "original_end_idx": original_end,
                    "edit": edit,
                    "lines_added": lines_added,
                    "lines_deleted": lines_deleted,
                }
            )

        elif edit_type == "insert_after":
            line_idx = re["line_idx"] + 1  # after the anchor line
            original_start = re["line_idx"]
            original_end = re["line_idx"]
            for i, nl in enumerate(new_text_lines):
                new_lines.insert(line_idx + i, nl)
            lines_added = len(new_text_lines)
            lines_deleted = 0
            applied.append(
                {
                    "start_idx": line_idx,
                    "end_idx": line_idx + lines_added - 1,
                    "original_start_idx": original_start,
                    "original_end_idx": original_end,
                    "edit": edit,
                    "lines_added": lines_added,
                    "lines_deleted": lines_deleted,
                }
            )

        else:  # replace
            start_idx = re["line_idx"]
            end_idx = re["end_idx"]
            original_start = start_idx
            original_end = end_idx
            old_count = end_idx - start_idx + 1
            new_count = len(new_text_lines)

            # Replace the range
            new_lines[start_idx : end_idx + 1] = new_text_lines

            lines_added = new_count
            lines_deleted = old_count
            applied.append(
                {
                    "start_idx": start_idx,
                    "end_idx": start_idx + new_count - 1
                    if new_count > 0
                    else start_idx,
                    "original_start_idx": original_start,
                    "original_end_idx": original_end,
                    "edit": edit,
                    "lines_added": lines_added,
                    "lines_deleted": lines_deleted,
                }
            )

    return new_lines, applied
