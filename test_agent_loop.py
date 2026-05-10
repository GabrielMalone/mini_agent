#!/usr/bin/env python3
"""
test_agent_loop.py — integration tests for the full agent turn pipeline.

Mocks the DeepSeek API to verify that tool calls are executed, results are
appended, text responses are handled correctly, and API retry works.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import requests as req_mod

from config import AgentConfig
from llm import call_deepseek
from safety import ReadSafetyGate, WriteSafetyGate
from tools import execute_tool, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_api_response(content: str = "", tool_calls: list[dict] | None = None) -> dict:
    """Build a minimal DeepSeek-style API response."""
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


def _tool_call(name: str, call_id: str, args: dict) -> dict:
    """Build a single tool_call object as returned by the API."""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }


def _gates(workspace: str) -> tuple[WriteSafetyGate, ReadSafetyGate]:
    return WriteSafetyGate(workspace, allow_overwrites=True), ReadSafetyGate(workspace)


# ---------------------------------------------------------------------------
# Tests: turn pipeline
# ---------------------------------------------------------------------------

class TestAgentTurnPipeline(unittest.TestCase):
    """Simulate full turns: API → tool execution → response."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.write_gate, self.read_gate = _gates(self.workspace)
        self.config = AgentConfig.load(self.workspace)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    @patch("llm.requests.post")
    def test_turn_with_one_tool_call(self, mock_post):
        """API returns one tool_call, tool executes, then API returns text."""
        call1_response = MagicMock()
        call1_response.ok = True
        call1_response.json.return_value = _make_api_response(
            tool_calls=[_tool_call("write_file", "call_1", {
                "path": os.path.join(self.workspace, "out.txt"),
                "content": "hello integration",
            })]
        )

        call2_response = MagicMock()
        call2_response.ok = True
        call2_response.json.return_value = _make_api_response(
            content="Done. Wrote the file."
        )

        mock_post.side_effect = [call1_response, call2_response]

        messages: list[dict] = [
            {"role": "user", "content": "write out.txt with hello integration"}
        ]

        msg1 = call_deepseek(messages, self.config)
        self.assertIn("tool_calls", msg1)
        self.assertEqual(len(msg1["tool_calls"]), 1)

        messages.append(msg1)
        for tc in msg1["tool_calls"]:
            result = execute_tool(tc, self.write_gate, self.read_gate)
            self.assertTrue(result.success)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result.to_json(),
            })

        msg2 = call_deepseek(messages, self.config)
        self.assertNotIn("tool_calls", msg2)
        self.assertEqual(msg2["content"], "Done. Wrote the file.")
        messages.append(msg2)

        out_path = os.path.join(self.workspace, "out.txt")
        self.assertTrue(os.path.isfile(out_path))
        with open(out_path) as f:
            self.assertEqual(f.read(), "hello integration")

        roles = [m["role"] for m in messages]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])

    @patch("llm.requests.post")
    def test_turn_with_multiple_tool_calls(self, mock_post):
        """API returns multiple tool_calls in one response, all execute."""
        call1_response = MagicMock()
        call1_response.ok = True
        call1_response.json.return_value = _make_api_response(
            tool_calls=[
                _tool_call("write_file", "call_a", {
                    "path": os.path.join(self.workspace, "a.txt"),
                    "content": "AAA",
                }),
                _tool_call("write_file", "call_b", {
                    "path": os.path.join(self.workspace, "b.txt"),
                    "content": "BBB",
                }),
            ]
        )

        call2_response = MagicMock()
        call2_response.ok = True
        call2_response.json.return_value = _make_api_response(
            content="Both files written."
        )

        mock_post.side_effect = [call1_response, call2_response]

        messages: list[dict] = [
            {"role": "user", "content": "write a.txt and b.txt"}
        ]

        msg1 = call_deepseek(messages, self.config)
        self.assertEqual(len(msg1["tool_calls"]), 2)

        messages.append(msg1)
        for tc in msg1["tool_calls"]:
            result = execute_tool(tc, self.write_gate, self.read_gate)
            self.assertTrue(result.success)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result.to_json(),
            })

        msg2 = call_deepseek(messages, self.config)
        self.assertEqual(msg2["content"], "Both files written.")
        messages.append(msg2)

        self.assertTrue(os.path.isfile(os.path.join(self.workspace, "a.txt")))
        self.assertTrue(os.path.isfile(os.path.join(self.workspace, "b.txt")))
        self.assertEqual(len(messages), 5)

    @patch("llm.requests.post")
    def test_turn_without_tools(self, mock_post):
        """API returns plain text — no tool execution needed."""
        call_response = MagicMock()
        call_response.ok = True
        call_response.json.return_value = _make_api_response(
            content="Hello, how can I help?"
        )

        mock_post.return_value = call_response

        messages: list[dict] = [
            {"role": "user", "content": "hi"}
        ]

        msg = call_deepseek(messages, self.config)
        self.assertNotIn("tool_calls", msg)
        self.assertEqual(msg["content"], "Hello, how can I help?")
        messages.append(msg)

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["role"], "assistant")

    @patch("llm.requests.post")
    def test_tool_failure_does_not_crash(self, mock_post):
        """A failing tool returns a ToolResult with success=False."""
        call1_response = MagicMock()
        call1_response.ok = True
        call1_response.json.return_value = _make_api_response(
            tool_calls=[_tool_call("read_file", "call_fail", {
                "path": os.path.join(self.workspace, "nonexistent.xyz"),
            })]
        )

        call2_response = MagicMock()
        call2_response.ok = True
        call2_response.json.return_value = _make_api_response(
            content="That file doesn't exist."
        )

        mock_post.side_effect = [call1_response, call2_response]

        messages: list[dict] = [
            {"role": "user", "content": "read nonexistent.xyz"}
        ]

        msg1 = call_deepseek(messages, self.config)
        messages.append(msg1)

        for tc in msg1["tool_calls"]:
            result = execute_tool(tc, self.write_gate, self.read_gate)
            self.assertFalse(result.success)
            self.assertIsInstance(result, ToolResult)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result.to_json(),
            })

        msg2 = call_deepseek(messages, self.config)
        self.assertEqual(msg2["content"], "That file doesn't exist.")

        tool_msg = json.loads(messages[2]["content"])
        self.assertFalse(tool_msg["success"])

    @patch("llm.requests.post")
    def test_safety_gate_blocks_write(self, mock_post):
        """Write outside workspace is blocked by WriteSafetyGate."""
        outside = tempfile.mkdtemp()
        try:
            call1_response = MagicMock()
            call1_response.ok = True
            call1_response.json.return_value = _make_api_response(
                tool_calls=[_tool_call("write_file", "call_block", {
                    "path": os.path.join(outside, "escape.txt"),
                    "content": "should not be written",
                })]
            )

            call2_response = MagicMock()
            call2_response.ok = True
            call2_response.json.return_value = _make_api_response(
                content="That path is outside the workspace."
            )

            mock_post.side_effect = [call1_response, call2_response]

            messages: list[dict] = [
                {"role": "user", "content": "write outside the workspace"}
            ]

            msg1 = call_deepseek(messages, self.config)
            messages.append(msg1)

            for tc in msg1["tool_calls"]:
                result = execute_tool(tc, self.write_gate, self.read_gate)
                self.assertFalse(result.success)
                self.assertIn("blocked by safety", result.content)

            self.assertFalse(os.path.isfile(os.path.join(outside, "escape.txt")))
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests: API retry
# ---------------------------------------------------------------------------

class TestAPIRetry(unittest.TestCase):
    """Verify that transient API failures are retried."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.config = AgentConfig.load(self.workspace)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    @patch("llm.requests.post")
    def test_retries_on_503_then_succeeds(self, mock_post):
        """Two 503 failures then a successful response."""
        fail1 = MagicMock()
        fail1.ok = False
        fail1.status_code = 503

        fail2 = MagicMock()
        fail2.ok = False
        fail2.status_code = 503

        success = MagicMock()
        success.ok = True
        success.json.return_value = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        mock_post.side_effect = [fail1, fail2, success]

        messages: list[dict] = [{"role": "user", "content": "hi"}]
        msg = call_deepseek(messages, self.config)
        self.assertEqual(msg["content"], "ok")
        self.assertEqual(mock_post.call_count, 3)

    @patch("llm.requests.post")
    def test_retries_on_network_error_then_succeeds(self, mock_post):
        """Two ConnectionErrors then a successful response."""
        success = MagicMock()
        success.ok = True
        success.json.return_value = {"choices": [{"message": {"role": "assistant", "content": "recovered"}}]}

        mock_post.side_effect = [
            req_mod.ConnectionError("refused"),
            req_mod.ConnectionError("refused"),
            success,
        ]

        messages: list[dict] = [{"role": "user", "content": "hi"}]
        msg = call_deepseek(messages, self.config)
        self.assertEqual(msg["content"], "recovered")
        self.assertEqual(mock_post.call_count, 3)

    @patch("llm.requests.post")
    def test_non_retryable_error_raises_immediately(self, mock_post):
        """400 Bad Request should NOT be retried."""
        fail = MagicMock()
        fail.ok = False
        fail.status_code = 400
        fail.raise_for_status.side_effect = req_mod.HTTPError("400 Bad Request")

        mock_post.return_value = fail

        messages: list[dict] = [{"role": "user", "content": "hi"}]
        with self.assertRaises(req_mod.HTTPError):
            call_deepseek(messages, self.config)
        # Only one attempt, no retry
        self.assertEqual(mock_post.call_count, 1)

    @patch("llm.requests.post")
    def test_exhausted_retries_raises(self, mock_post):
        """All retries exhausted should raise."""
        mock_post.side_effect = req_mod.ConnectionError("always down")

        messages: list[dict] = [{"role": "user", "content": "hi"}]
        with self.assertRaises(req_mod.ConnectionError):
            call_deepseek(messages, self.config)
        # 1 initial + 3 retries = 4 attempts
        self.assertEqual(mock_post.call_count, 4)


if __name__ == "__main__":
    unittest.main()
