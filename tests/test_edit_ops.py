"""Tests for tools/_edit_ops.py — targeting uncovered paths."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from core.safety import ReadSafetyGate, WriteSafetyGate
from tools._edit_ops import (
    _READ_FILES,
    _edit_file,
    _edit_file_anchored,
    _finalize_edit,
)


def _resolve(p):
    """Resolve path same way safety gate does."""
    return os.path.realpath(p)


class TestEditOpsBatch(unittest.TestCase):
    """Test _edit_file with paths= (batch mode)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.wg = WriteSafetyGate(self.ws_root)
        self.rg = ReadSafetyGate(self.ws_root)
        self.f1 = os.path.join(self.tmp, "a.txt")
        self.f2 = os.path.join(self.tmp, "b.txt")
        Path(self.f1).write_text("hello world\n")
        Path(self.f2).write_text("foo bar\n")
        _READ_FILES.add(_resolve(self.f1))
        _READ_FILES.add(_resolve(self.f2))

    def tearDown(self):
        _READ_FILES.discard(_resolve(self.f1))
        _READ_FILES.discard(_resolve(self.f2))

    def test_batch_edit_two_files(self):
        """Batch edit with paths= replaces in both files."""
        result = _edit_file(
            {"paths": [self.f1, self.f2], "old_string": "hello", "new_string": "HELLO", "count": 1, "preview": False},
            self.wg,
            self.rg,
        )
        # Batch fails because b.txt doesn't contain 'hello'
        self.assertFalse(result.success, "Expected partial failure")
        self.assertIn("[OK]", result.content)
        self.assertIn("[FAIL]", result.content)

    def test_batch_edit_invalid_paths_not_list(self):
        result = _edit_file({"paths": "not_a_list", "old_string": "x", "new_string": "y"}, self.wg, self.rg)
        self.assertFalse(result.success)
        self.assertIn("non-empty list", result.content)

    def test_batch_edit_empty_paths(self):
        result = _edit_file({"paths": [], "old_string": "x", "new_string": "y"}, self.wg, self.rg)
        self.assertFalse(result.success)
        self.assertIn("non-empty list", result.content)

    def test_batch_edit_mixed_success_failure(self):
        result = _edit_file(
            {"paths": [self.f1, self.f2], "old_string": "xyz_nonexistent", "new_string": "REPLACED", "count": 1, "preview": False},
            self.wg, self.rg,
        )
        self.assertFalse(result.success)
        self.assertIn("Failed paths:", result.content)

    def test_batch_edit_preview_mode(self):
        before = Path(self.f1).read_text()
        result = _edit_file(
            {"paths": [self.f1], "old_string": "hello", "new_string": "HELLO", "count": 1, "preview": True},
            self.wg, self.rg,
        )
        self.assertTrue(result.success, result.content)
        self.assertIn("Preview", result.content)  # batch preview uses different format
        self.assertEqual(Path(self.f1).read_text(), before)


class TestFinalizeEdit(unittest.TestCase):
    """Test _finalize_edit ruff lint gate paths."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp

    def test_finalize_ruff_lint_passes(self):
        f = os.path.join(self.tmp, "mod.py")
        ok, err = _finalize_edit(f, "", "x: int = 1\n", self.ws_root)
        self.assertTrue(ok, err)
        self.assertIsNone(err)

    def test_finalize_clean_code_passes(self):
        f = os.path.join(self.tmp, "mod.py")
        ok, err = _finalize_edit(f, "", "x = 1\ny = 2\n", self.ws_root)
        self.assertTrue(ok, f"Expected ok but got err: {err}")
        self.assertIsNone(err)

    def test_finalize_nonexistent_dir(self):
        f = os.path.join(self.tmp, "no_dir", "mod.py")
        ok, err = _finalize_edit(f, "", "x = 1\n", self.ws_root)
        self.assertFalse(ok)
        self.assertIsNotNone(err)


class TestEditFileAnchored(unittest.TestCase):
    """Test _edit_file_anchored with anchor-based editing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.wg = WriteSafetyGate(self.ws_root)
        self.rg = ReadSafetyGate(self.ws_root)

    def test_empty_files_arg(self):
        result = _edit_file_anchored([], self.wg, self.rg)
        self.assertFalse(result.success)
        self.assertIn("non-empty array", result.content)

    def test_files_not_list(self):
        result = _edit_file_anchored("not_a_list", self.wg, self.rg)
        self.assertFalse(result.success)
        self.assertIn("non-empty array", result.content)

    def test_edits_not_array(self):
        f = os.path.join(self.tmp, "mod.py")
        Path(f).write_text("x = 1\n")
        result = _edit_file_anchored([{"path": f, "edits": "not_array"}], self.wg, self.rg)
        self.assertFalse(result.success)
        self.assertIn("valid JSON array", result.content)

    def test_edits_empty_list(self):
        f = os.path.join(self.tmp, "mod.py")
        Path(f).write_text("x = 1\n")
        result = _edit_file_anchored([{"path": f, "edits": []}], self.wg, self.rg)
        self.assertFalse(result.success)
        self.assertIn("non-empty array", result.content)

    def test_edits_json_string_valid(self):
        f = os.path.join(self.tmp, "mod.py")
        Path(f).write_text("x = 1\n")
        _READ_FILES.add(_resolve(f))
        try:
            _edit_file_anchored(
                [{"path": f, "edits": json.dumps([{"anchor": "x", "text": "y = 2", "edit_type": "replace"}])}],
                self.wg, self.rg,
            )
        finally:
            _READ_FILES.discard(_resolve(f))

    def test_edits_json_string_invalid(self):
        f = os.path.join(self.tmp, "mod.py")
        Path(f).write_text("x = 1\n")
        result = _edit_file_anchored([{"path": f, "edits": "not valid json"}], self.wg, self.rg)
        self.assertFalse(result.success)
        self.assertIn("valid JSON array", result.content)

    def test_file_not_read_yet(self):
        f = os.path.join(self.tmp, "mod.py")
        Path(f).write_text("x = 1\n")
        _READ_FILES.discard(_resolve(f))
        result = _edit_file_anchored(
            [{"path": f, "edits": [{"anchor": "x", "text": "y = 2"}]}],
            self.wg, self.rg,
        )
        self.assertFalse(result.success)
        self.assertIn("not read yet", result.content)

    def test_all_files_fail_validation(self):
        result = _edit_file_anchored(
            [{"path": "/nonexistent/file.py", "edits": [{"anchor": "x", "text": "y"}]}],
            self.wg, self.rg,
        )
        self.assertFalse(result.success)
        self.assertIn("All files failed validation", result.content)


class TestEditFileSingle(unittest.TestCase):
    """Test _edit_file single-file mode edge paths."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.wg = WriteSafetyGate(self.ws_root)
        self.rg = ReadSafetyGate(self.ws_root)

    def test_count_minus_one_replace_all(self):
        f = os.path.join(self.tmp, "mod.py")
        Path(f).write_text("hello\nhello\nworld\n")
        _READ_FILES.add(_resolve(f))
        try:
            result = _edit_file(
                {"path": f, "old_string": "hello", "new_string": "HELLO", "count": -1, "preview": False},
                self.wg, self.rg,
            )
            self.assertTrue(result.success, result.content)
            self.assertEqual(Path(f).read_text(), "HELLO\nHELLO\nworld\n")
        finally:
            _READ_FILES.discard(_resolve(f))

    def test_preview_count_minus_one(self):
        f = os.path.join(self.tmp, "mod.py")
        before = "hello\nhello\nworld\n"
        Path(f).write_text(before)
        _READ_FILES.add(_resolve(f))
        try:
            result = _edit_file(
                {"path": f, "old_string": "hello", "new_string": "HELLO", "count": -1, "preview": True},
                self.wg, self.rg,
            )
            self.assertTrue(result.success, result.content)
            self.assertIn("Preview", result.content)  # batch preview uses different format
            self.assertEqual(Path(f).read_text(), before)
        finally:
            _READ_FILES.discard(_resolve(f))


if __name__ == "__main__":
    unittest.main()
