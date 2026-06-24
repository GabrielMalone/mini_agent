"""Tests for tools/agent_ops.py — restore_file, session_stats, recall_turn, remember, read_image."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tools.agent_ops import (
    _restore_file,
    _session_stats,
    _recall_turn,
    _remember,
    _guess_mime_type,
)

from tools.result import ToolResult


class TestGuessMimeType(unittest.TestCase):
    """Test MIME type guessing from file extensions."""

    def test_png(self):
        self.assertEqual(_guess_mime_type("image.png"), "image/png")

    def test_jpg(self):
        self.assertEqual(_guess_mime_type("photo.jpg"), "image/jpeg")

    def test_jpeg(self):
        self.assertEqual(_guess_mime_type("photo.jpeg"), "image/jpeg")

    def test_gif(self):
        self.assertEqual(_guess_mime_type("anim.gif"), "image/gif")

    def test_webp(self):
        self.assertEqual(_guess_mime_type("img.webp"), "image/webp")

    def test_unknown_fallback_to_png(self):
        self.assertEqual(_guess_mime_type("file.xyz"), "image/png")

    def test_no_extension(self):
        self.assertEqual(_guess_mime_type("noext"), "image/png")

    def test_case_insensitive(self):
        self.assertEqual(_guess_mime_type("IMG.PNG"), "image/png")


class TestRecallTurn(unittest.TestCase):
    """Test recall_turn tool."""

    def test_turn_must_be_positive_integer(self):
        result = _recall_turn({"turn": 0}, None, None)
        self.assertFalse(result.success)
        self.assertIn("positive integer", result.content)

    def test_turn_must_be_int(self):
        result = _recall_turn({"turn": "abc"}, None, None)
        self.assertFalse(result.success)

    def test_turn_not_in_history(self):
        from tools import context as ctx_mod

        original = getattr(ctx_mod._TOOL_CONTEXT, "_turn_history", None)
        try:
            ctx_mod._TOOL_CONTEXT._turn_history = {1: "did something"}
            result = _recall_turn({"turn": 5}, None, None)
            self.assertTrue(result.success)
            self.assertIn("No record of turn 5", result.content)
        finally:
            if original is not None:
                ctx_mod._TOOL_CONTEXT._turn_history = original


class TestRemember(unittest.TestCase):
    """Test remember tool validation."""

    def test_missing_topic(self):
        result = _remember({"topic": "", "detail": "something"}, None, None)
        self.assertFalse(result.success)
        self.assertIn("Missing required parameter", result.content)

    def test_no_memory_store(self):
        result = _remember({"topic": "test", "detail": "learned something"}, None, None)
        self.assertFalse(result.success)
        self.assertIn("No memory store", result.content)


if __name__ == "__main__":
    unittest.main()
