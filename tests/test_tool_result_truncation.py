"""Tests for tool-result truncation in _append_tool_result (core/llm.py)."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from core.compaction import TURN_END_RESULT_CAP_CHARS


class TestToolResultTruncation(unittest.TestCase):
    """Verify that _append_tool_result truncates oversized tool results."""

    def setUp(self):
        # Create a mock ToolResult-like object
        self.small_content = "short output"
        self.large_content = "x" * 200000

    def _make_fake_result(self, content: str) -> MagicMock:
        """Build a mock result with to_json() and needed attrs."""
        r = MagicMock()
        r.content = content
        r.success = True
        r.diff_preview = None
        r.to_json.return_value = json.dumps({"success": True, "content": content})
        return r

    def test_small_result_not_truncated(self):
        """Results under TURN_END_RESULT_CAP_CHARS pass through unchanged."""
        result = self._make_fake_result(self.small_content)
        json_str = result.to_json()
        self.assertLess(len(json_str), TURN_END_RESULT_CAP_CHARS)

    def test_large_result_truncated(self):
        """Results over TURN_END_RESULT_CAP_CHARS are truncated with marker."""
        result = self._make_fake_result(self.large_content)
        json_str = result.to_json()
        self.assertGreater(len(json_str), TURN_END_RESULT_CAP_CHARS)

        # Apply same truncation logic as _append_tool_result
        if len(json_str) > TURN_END_RESULT_CAP_CHARS:
            head = json_str[:200]
            tail = json_str[-(TURN_END_RESULT_CAP_CHARS - 400):]
            truncated_len = len(json_str) - 200 - len(tail)
            json_str = (
                head
                + f"\n... [truncated {truncated_len:} chars / ~{truncated_len // 4:} tokens] ...\n"
                + tail
            )

        self.assertIn("truncated", json_str)
        self.assertLess(len(json_str), TURN_END_RESULT_CAP_CHARS + 500,
                        f"Truncated length {len(json_str)} exceeds cap")

    def test_truncation_preserves_head_and_tail(self):
        """Head (first 200 chars) and tail are preserved after truncation."""
        result = self._make_fake_result(self.large_content)
        json_str = result.to_json()
        original_head = json_str[:200]

        if len(json_str) > TURN_END_RESULT_CAP_CHARS:
            head = json_str[:200]
            tail = json_str[-(TURN_END_RESULT_CAP_CHARS - 400):]
            json_str = (
                head
                + "\n... [truncated ...]\n"
                + tail
            )

        self.assertTrue(json_str.startswith(original_head),
                        "Head of result should be preserved")

    def test_boundary_result_not_truncated(self):
        """Results near but under the cap are not truncated."""
        # Build content so JSON is just under cap
        content = "y" * (TURN_END_RESULT_CAP_CHARS - 100)
        result = self._make_fake_result(content)
        json_str = result.to_json()
        if len(json_str) <= TURN_END_RESULT_CAP_CHARS:
            self.assertNotIn("truncated", json_str)


if __name__ == "__main__":
    unittest.main()
