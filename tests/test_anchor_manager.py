#!/usr/bin/env python3
"""Tests for core/anchor_manager.py -- stable word anchors, Myers diff reconciliation,
and anchor-based edit resolution."""

from __future__ import annotations

import os
import tempfile

import pytest

from core.anchor_manager import (
    AnchorStateManager,
    _fnv1a_32,
    apply_resolved_edits,
    content_hash,
    format_line_for_model,
    format_lines_for_model,
    resolve_anchored_edits,
    split_anchor,
    strip_anchors,
)


# ---------------------------------------------------------------------------
# FNV-1a 32-bit hash
# ---------------------------------------------------------------------------

class TestFNV1A:
    def test_empty_string(self):
        assert _fnv1a_32("") == 0x811C9DC5  # FNV offset basis

    def test_known_vector(self):
        # "hello" -> known FNV-1a 32-bit result
        assert _fnv1a_32("hello") == 0x4F9F2CAB

    def test_different_strings_produce_different_hashes(self):
        assert _fnv1a_32("abc") != _fnv1a_32("abd")

    def test_trailing_whitespace_distinction(self):
        # Raw hash -- caller is responsible for rstrip if desired
        assert _fnv1a_32("line") != _fnv1a_32("line ")

    def test_unicode(self):
        h = _fnv1a_32("café")
        assert isinstance(h, int)
        assert h > 0


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_returns_hex_string(self):
        h = content_hash("hello world")
        assert isinstance(h, str)
        assert len(h) == 6
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        assert content_hash("abc") == content_hash("abc")

    def test_different_inputs(self):
        assert content_hash("a") != content_hash("b")

    def test_empty_string(self):
        assert content_hash("") == content_hash("")


# ---------------------------------------------------------------------------
# format_line_with_anchor
# ---------------------------------------------------------------------------

class TestFormatLineForModel:
    def test_basic(self):
        result = format_line_for_model("def foo():", "Apple")
        assert result == "Apple§def foo():"

    def test_empty_content(self):
        result = format_line_for_model("", "X")
        assert result == "X§"


# ---------------------------------------------------------------------------
# format_lines_for_model
# ---------------------------------------------------------------------------

class TestFormatLinesForModel:
    def test_basic(self):
        lines = ["def foo():", "    return 42"]
        anchors = ["Apple", "Banana"]
        result = format_lines_for_model(lines, anchors)
        # Each line should have anchor and content
        assert "Apple" in result
        assert "Banana" in result
        assert "def foo():" in result
        assert "return 42" in result

    def test_reveal_mode(self):
        """In reveal mode, the anchor§ prefix is shown to the LLM."""
        lines = ["x = 1"]
        anchors = ["Zebra"]
        result = format_lines_for_model(lines, anchors, reveal=True)
        assert "Zebra§x = 1" in result

    def test_no_reveal(self):
        """Without reveal, anchors are not shown."""
        lines = ["x = 1"]
        anchors = ["Zebra"]
        result = format_lines_for_model(lines, anchors, reveal=False)
        assert "Zebra§" not in result

    def test_mismatched_lengths(self):
        """Should not crash if anchors and lines differ in length."""
        result = format_lines_for_model(["a", "b"], ["X"])
        assert result  # Should produce something without crashing


# ---------------------------------------------------------------------------
# strip_anchors
# ---------------------------------------------------------------------------

class TestStripAnchors:
    def test_removes_anchor_prefix(self):
        assert strip_anchors("Apple§def foo():") == "def foo():"

    def test_no_anchor(self):
        assert strip_anchors("just a line") == "just a line"

    def test_empty_string(self):
        assert strip_anchors("") == ""

    def test_multiple_delimiters(self):
        # Only the first § is treated as anchor delimiter
        result = strip_anchors("X§a§b")
        assert result == "a§b"


# ---------------------------------------------------------------------------
# split_anchor
# ---------------------------------------------------------------------------

class TestSplitAnchor:
    def test_basic(self):
        word, content = split_anchor("Apple§def foo():")
        assert word == "Apple"
        assert content == "def foo():"

    def test_no_delimiter(self):
        word, content = split_anchor("justaline")
        assert word == ""
        assert content == "justaline"

    def test_empty_string(self):
        word, content = split_anchor("")
        assert word == ""
        assert content == ""

    def test_trailing_delimiter(self):
        word, content = split_anchor("Word§")
        assert word == "Word"
        assert content == ""


# ---------------------------------------------------------------------------
# AnchorStateManager.reconcile
# ---------------------------------------------------------------------------

class TestReconcile:
    def setup_method(self):
        # Use a unique task_id per test to avoid cross-test pollution
        self.task_id = f"test_{id(self)}"
        self.tmpdir = tempfile.mkdtemp()

    def _tmpfile(self, name="test.py"):
        return os.path.join(self.tmpdir, name)

    def test_new_file_gets_anchors(self):
        path = self._tmpfile("new.py")
        lines = ["def foo():", "    pass", ""]
        anchors = AnchorStateManager.reconcile(path, lines, self.task_id)
        assert len(anchors) == len(lines)
        # All should be unique words
        assert len(set(anchors)) == len(anchors)
        # None should be numeric-only fallbacks for small files
        for a in anchors:
            assert not a.startswith("W0")

    def test_unchanged_file_keeps_same_anchors(self):
        path = self._tmpfile("stable.py")
        lines = ["a = 1", "b = 2"]

        anchors1 = AnchorStateManager.reconcile(path, lines, self.task_id)
        anchors2 = AnchorStateManager.reconcile(path, lines, self.task_id)

        assert anchors1 == anchors2

    def test_insert_line_gets_new_anchor(self):
        path = self._tmpfile("insert.py")
        original = ["line one", "line three"]
        modified = ["line one", "line two", "line three"]

        anchors1 = AnchorStateManager.reconcile(path, original, self.task_id)
        anchors2 = AnchorStateManager.reconcile(path, modified, self.task_id)

        # First and last lines should keep anchors
        assert anchors2[0] == anchors1[0]
        assert anchors2[2] == anchors1[1]
        # Middle line is new
        assert anchors2[1] not in anchors1

    def test_delete_line_removes_anchor(self):
        path = self._tmpfile("delete.py")
        original = ["keep me", "delete me", "also keep"]
        modified = ["keep me", "also keep"]

        anchors1 = AnchorStateManager.reconcile(path, original, self.task_id)
        anchors2 = AnchorStateManager.reconcile(path, modified, self.task_id)

        assert anchors2[0] == anchors1[0]
        assert anchors2[1] == anchors1[2]

    def test_replace_line_gets_new_anchor(self):
        path = self._tmpfile("replace.py")
        original = ["old line"]
        modified = ["new line"]

        anchors1 = AnchorStateManager.reconcile(path, original, self.task_id)
        anchors2 = AnchorStateManager.reconcile(path, modified, self.task_id)

        # Entire line changed → new anchor
        assert anchors2[0] != anchors1[0]

    def test_massive_file_uses_fallback(self):
        """Files with more than MAX_TRACKED_LINES use numeric fallbacks."""
        path = self._tmpfile("huge.py")
        huge = [f"line {i}" for i in range(AnchorStateManager.MAX_TRACKED_LINES + 10)]

        anchors = AnchorStateManager.reconcile(path, huge, self.task_id)

        assert len(anchors) == len(huge)
        # Fallback format: L1, L2, L3...
        assert anchors[0] == "L1"
        assert anchors[1] == "L2"

    def test_empty_file(self):
        path = self._tmpfile("empty.py")
        anchors = AnchorStateManager.reconcile(path, [], self.task_id)
        assert anchors == []

    def test_different_tasks_independent(self):
        """Same file, different tasks → independent anchor state."""
        path = self._tmpfile("shared.py")
        lines = ["x = 1"]

        anchors_a = AnchorStateManager.reconcile(path, lines, "task_a")
        anchors_b = AnchorStateManager.reconcile(path, lines, "task_b")

        # Both should get anchors (not sharing state)
        assert len(anchors_a) == 1
        assert len(anchors_b) == 1


# ---------------------------------------------------------------------------
# resolve_anchored_edits
# ---------------------------------------------------------------------------

class TestResolveAnchoredEdits:
    # Helper: split anchored lines into (plain_lines, anchor_words)
    @staticmethod
    def _split(lines):
        plain = []
        anchors = []
        for l in lines:
            w, c = split_anchor(l)
            plain.append(c)
            anchors.append(w)
        return plain, anchors

    def test_single_line_replace(self):
        anchored = ["Apple§def foo():", "Banana§    return 42"]
        plain, anchors = self._split(anchored)
        edits = [
            {"anchor": "Apple§def foo():", "text": "def bar():"}
        ]
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        assert len(failed) == 0
        assert len(resolved) == 1
        assert resolved[0]["line_idx"] == 0
        assert resolved[0]["end_idx"] == 0

    def test_multi_line_replace(self):
        anchored = [
            "Apple§def foo():",
            "Banana§    x = 1",
            "Cherry§    return x",
        ]
        plain, anchors = self._split(anchored)
        edits = [
            {
                "anchor": "Apple§def foo():",
                "end_anchor": "Cherry§    return x",
                "text": "def bar():\n    return 99",
            }
        ]
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        assert len(failed) == 0
        assert len(resolved) == 1
        assert resolved[0]["line_idx"] == 0
        assert resolved[0]["end_idx"] == 2

    def test_missing_anchor(self):
        anchored = ["Apple§x = 1"]
        plain, anchors = self._split(anchored)
        edits = [{"anchor": "Zebra§not here", "text": "y = 2"}]
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        assert len(resolved) == 0
        assert len(failed) == 1
        assert "not found" in failed[0]["error"]

    def test_content_mismatch(self):
        """Anchor word exists but content doesn't match."""
        anchored = ["Apple§actual content"]
        plain, anchors = self._split(anchored)
        edits = [{"anchor": "Apple§wrong content", "text": "new"}]
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        assert len(resolved) == 0
        assert len(failed) == 1
        assert "doesn't match" in failed[0]["error"]

    def test_insert_before(self):
        anchored = ["Apple§line two"]
        plain, anchors = self._split(anchored)
        edits = [
            {"anchor": "Apple§line two", "edit_type": "insert_before", "text": "line one"}
        ]
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        assert len(failed) == 0
        assert len(resolved) == 1
        assert resolved[0]["line_idx"] == 0

    def test_insert_after(self):
        anchored = ["Apple§line one"]
        plain, anchors = self._split(anchored)
        edits = [
            {"anchor": "Apple§line one", "edit_type": "insert_after", "text": "line two"}
        ]
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        assert len(failed) == 0
        assert len(resolved) == 1
        assert resolved[0]["line_idx"] == 0

    def test_missing_anchor_field(self):
        anchored = ["Apple§x"]
        plain, anchors = self._split(anchored)
        edits = [{"text": "y"}]  # No anchor
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        assert len(resolved) == 0
        assert len(failed) == 1

    def test_end_anchor_before_start(self):
        anchored = [
            "Apple§first",
            "Banana§second",
            "Cherry§third",
        ]
        plain, anchors = self._split(anchored)
        edits = [
            {
                "anchor": "Cherry§third",
                "end_anchor": "Apple§first",
                "text": "reversed",
            }
        ]
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        assert len(resolved) == 0
        assert len(failed) == 1
        assert "before" in failed[0]["error"]

    def test_multiple_edits(self):
        anchored = [
            "Apple§import os",
            "Banana§import sys",
            "Cherry§",
            "Date§def main():",
        ]
        plain, anchors = self._split(anchored)
        edits = [
            {"anchor": "Apple§import os", "text": "import os, sys"},
            {"anchor": "Banana§import sys", "text": ""},
        ]
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        assert len(failed) == 0
        assert len(resolved) == 2

    def test_raw_anchor_no_delimiter(self):
        """Anchor without § delimiter — treated as content-only (word='').
        This means the anchor word is empty and won't match any word in the file."""
        anchored = ["Apple§x = 1"]
        plain, anchors = self._split(anchored)
        edits = [{"anchor": "Apple", "text": "y = 2"}]  # No § → word="", content="Apple"
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        # Empty anchor word won't match → fails
        assert len(resolved) == 0
        assert len(failed) == 1


# ---------------------------------------------------------------------------
# apply_resolved_edits
# ---------------------------------------------------------------------------

class TestApplyResolvedEdits:
    def test_replace_single_line(self):
        lines = ["def foo():", "    pass"]
        resolved = [
            {"line_idx": 0, "end_idx": 0, "edit": {"text": "def bar():"}}
        ]
        new_lines, applied = apply_resolved_edits(lines, resolved)
        assert new_lines == ["def bar():", "    pass"]
        assert len(applied) == 1
        assert applied[0]["lines_added"] == 1
        assert applied[0]["lines_deleted"] == 1

    def test_replace_multi_line_with_single(self):
        lines = ["a", "b", "c"]
        resolved = [
            {"line_idx": 0, "end_idx": 1, "edit": {"text": "replacement"}}
        ]
        new_lines, applied = apply_resolved_edits(lines, resolved)
        assert new_lines == ["replacement", "c"]
        assert applied[0]["lines_added"] == 1
        assert applied[0]["lines_deleted"] == 2

    def test_replace_with_multiple_lines(self):
        lines = ["old"]
        resolved = [
            {"line_idx": 0, "end_idx": 0, "edit": {"text": "new1\nnew2\nnew3"}}
        ]
        new_lines, applied = apply_resolved_edits(lines, resolved)
        assert new_lines == ["new1", "new2", "new3"]
        assert applied[0]["lines_added"] == 3
        assert applied[0]["lines_deleted"] == 1

    def test_insert_before(self):
        lines = ["second"]
        resolved = [
            {"line_idx": 0, "end_idx": 0,
             "edit": {"text": "first", "edit_type": "insert_before"}}
        ]
        new_lines, applied = apply_resolved_edits(lines, resolved)
        assert new_lines == ["first", "second"]
        assert applied[0]["lines_added"] == 1
        assert applied[0]["lines_deleted"] == 0

    def test_insert_after(self):
        lines = ["first"]
        resolved = [
            {"line_idx": 0, "end_idx": 0,
             "edit": {"text": "second", "edit_type": "insert_after"}}
        ]
        new_lines, applied = apply_resolved_edits(lines, resolved)
        assert new_lines == ["first", "second"]
        assert applied[0]["lines_added"] == 1
        assert applied[0]["lines_deleted"] == 0

    def test_insert_after_multiline(self):
        lines = ["header"]
        resolved = [
            {"line_idx": 0, "end_idx": 0,
             "edit": {"text": "line1\nline2", "edit_type": "insert_after"}}
        ]
        new_lines, applied = apply_resolved_edits(lines, resolved)
        assert new_lines == ["header", "line1", "line2"]
        assert applied[0]["lines_added"] == 2

    def test_bottom_up_ordering(self):
        """Edits are applied bottom-up so indices stay stable."""
        lines = ["a", "b", "c", "d", "e"]
        # Edit line 3 first, then line 1 -- bottom-up ensures correctness
        resolved = [
            {"line_idx": 3, "end_idx": 3, "edit": {"text": "DD"}},
            {"line_idx": 1, "end_idx": 1, "edit": {"text": "BB"}},
        ]
        new_lines, applied = apply_resolved_edits(lines, resolved)
        assert new_lines == ["a", "BB", "c", "DD", "e"]

    def test_delete_range(self):
        """Replace with empty string = delete."""
        lines = ["a", "b", "c"]
        resolved = [
            {"line_idx": 1, "end_idx": 1, "edit": {"text": ""}}
        ]
        new_lines, applied = apply_resolved_edits(lines, resolved)
        assert new_lines == ["a", "c"]
        assert applied[0]["lines_added"] == 0
        assert applied[0]["lines_deleted"] == 1

    def test_empty_edits(self):
        lines = ["a", "b"]
        new_lines, applied = apply_resolved_edits(lines, [])
        assert new_lines == lines
        assert applied == []

    def test_insert_before_first_line(self):
        lines = ["first"]
        resolved = [
            {"line_idx": 0, "end_idx": 0,
             "edit": {"text": "zeroth", "edit_type": "insert_before"}}
        ]
        new_lines, applied = apply_resolved_edits(lines, resolved)
        assert new_lines == ["zeroth", "first"]

    def test_end_to_end_resolve_and_apply(self):
        """Full pipeline: resolve → apply with anchors."""
        anchored = [
            "Apple§import os",
            "Banana§",
            "Cherry§x = 1",
        ]
        plain, anchors = [strip_anchors(l) for l in anchored], [split_anchor(l)[0] for l in anchored]
        edits = [
            {"anchor": "Banana§", "text": "import sys"},
            {"anchor": "Cherry§x = 1", "text": "x = 42"},
        ]
        resolved, failed = resolve_anchored_edits(edits, plain, anchors)
        assert len(failed) == 0

        new_lines, applied = apply_resolved_edits(plain, resolved)
        assert new_lines == ["import os", "import sys", "x = 42"]
        assert len(applied) == 2
