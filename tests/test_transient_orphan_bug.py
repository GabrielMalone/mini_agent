"""Test that _transient tool results don't cause orphaned assistant(tool_calls).

Reproduces the API 400 error: "An assistant message with 'tool_calls' must be
followed by tool messages responding to each 'tool_call_id'."
"""

from __future__ import annotations

import unittest

from memory.memory_prune import _strip_orphaned_tool_messages


class TestTransientOrphanBug(unittest.TestCase):
    """Verify _transient stripping doesn't leave orphaned tool_calls."""

    def test_transient_tool_result_stripped_leaves_no_orphan(self):
        """When a tool result is _transient, the matching assistant(tool_calls)
        must also be removed (truncated) to avoid API 400 errors."""
        messages = [
            {"role": "user", "content": "do thing"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "result",
                "_transient": True,
            },
            {"role": "user", "content": "next thing"},
        ]

        # Simulate what _clean_messages does: strip system + _transient, then strip orphans
        cleaned = [
            m for m in messages if m.get("role") != "system" and not m.get("_transient")
        ]
        result = _strip_orphaned_tool_messages(cleaned, truncate=True)

        # Check: no assistant messages with tool_calls remain (they were truncated)
        for msg in result:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                self.fail(
                    f"Orphaned assistant(tool_calls) found: {msg}. "
                    f"All messages: {result}"
                )

        # The truncated list should end before the assistant with tool_calls
        # so we should only have the user message
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "user")

    def test_transient_tool_result_in_middle_of_sequence(self):
        """Multiple tool calls where one result is _transient — the entire
        sequence from that assistant onward should be truncated."""
        messages = [
            {"role": "user", "content": "do things"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "function": {"name": "read_file", "arguments": "{}"},
                    },
                    {
                        "id": "call_b",
                        "function": {"name": "search_files", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": "result a"},
            {
                "role": "tool",
                "tool_call_id": "call_b",
                "content": "result b",
                "_transient": True,
            },
            {"role": "user", "content": "next"},
        ]

        cleaned = [
            m for m in messages if m.get("role") != "system" and not m.get("_transient")
        ]
        result = _strip_orphaned_tool_messages(cleaned, truncate=True)

        for msg in result:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tc_ids = [tc.get("id") for tc in msg.get("tool_calls", [])]
                # Check that every tool_call_id has a matching tool result AFTER it
                tool_ids_after = set()
                found_assistant = False
                for m in result:
                    if m is msg:
                        found_assistant = True
                        continue
                    if found_assistant and m.get("role") == "tool":
                        tool_ids_after.add(m.get("tool_call_id"))
                for tcid in tc_ids:
                    if tcid not in tool_ids_after:
                        self.fail(
                            f"Orphaned tool_call_id {tcid} in assistant message. "
                            f"Messages: {result}"
                        )

    def test_no_transient_all_kept(self):
        """Without _transient markers, all messages are kept."""
        messages = [
            {"role": "user", "content": "do thing"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            {"role": "user", "content": "next"},
        ]

        cleaned = [
            m for m in messages if m.get("role") != "system" and not m.get("_transient")
        ]
        result = _strip_orphaned_tool_messages(cleaned, truncate=True)

        self.assertEqual(len(result), 4)
        roles = [m["role"] for m in result]
        self.assertEqual(roles, ["user", "assistant", "tool", "user"])

    def test_truncate_false_strips_only_orphan(self):
        """truncate=False should only remove the orphaned assistant, not truncate."""
        messages = [
            {"role": "user", "content": "first"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_orphan",
                        "function": {"name": "bad_tool", "arguments": "{}"},
                    }
                ],
            },
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "reply"},
        ]

        result = _strip_orphaned_tool_messages(messages, truncate=False)

        # The orphaned assistant should be removed, but subsequent messages kept
        roles = [m["role"] for m in result]
        self.assertEqual(roles, ["user", "user", "assistant"])

    def test_system_message_stripped(self):
        """System messages are stripped by _clean_messages behavior."""
        messages = [
            {"role": "system", "content": "you are a helpful assistant"},
            {"role": "user", "content": "hello"},
        ]

        cleaned = [
            m for m in messages if m.get("role") != "system" and not m.get("_transient")
        ]
        result = _strip_orphaned_tool_messages(cleaned, truncate=True)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "user")

    def test_user_messages_between_assistant_and_tool_results_truncate_true(self):
        """Even with truncate=True, _strip_orphaned_tool_messages does NOT
        catch user messages injected between assistant(tool_calls) and
        tool results. The backward scan finds the tool result before the
        assistant, so truncation doesn't trigger.

        This confirms that the PRIMARY fix in llm.py (moving
        _inject_pre_execution_context before messages.append(msg)) is the
        essential defense against this API 400 error."""
        messages = [
            {"role": "user", "content": "do thing"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "function": {"name": "run_shell", "arguments": "{}"},
                    },
                ],
            },
            {"role": "user", "content": "FAILURE PATTERN WARNING: ..."},
            {"role": "user", "content": "TOOL SEQUENCING WARNING: ..."},
            {"role": "tool", "tool_call_id": "call_a", "content": "result"},
        ]

        result = _strip_orphaned_tool_messages(messages, truncate=True)

        # All 5 messages survive — truncation doesn't trigger because
        # backward scan finds tool result before reaching assistant.
        self.assertEqual(len(result), 5)
        roles = [m["role"] for m in result]
        self.assertEqual(roles, ["user", "assistant", "user", "user", "tool"])

    def test_user_messages_between_assistant_and_tool_results_no_truncate(self):
        """With truncate=False, _strip_orphaned_tool_messages does NOT catch
        user messages between assistant(tool_calls) and tool results.
        This is acceptable because the PRIMARY fix is in llm.py where
        _inject_pre_execution_context now runs before messages.append(msg),
        preventing this scenario at the source."""
        messages = [
            {"role": "user", "content": "do thing"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "function": {"name": "run_shell", "arguments": "{}"},
                    },
                ],
            },
            {"role": "user", "content": "FAILURE PATTERN WARNING: ..."},
            {"role": "tool", "tool_call_id": "call_a", "content": "result"},
        ]

        result = _strip_orphaned_tool_messages(messages, truncate=False)

        # truncate=False keeps the messages as-is — the safety net only
        # removes truly orphaned tool messages and assistant(tool_calls),
        # not interleaved user messages. The primary fix in llm.py prevents
        # this scenario from occurring.
        self.assertEqual(len(result), 4)
        roles = [m["role"] for m in result]
        self.assertEqual(roles, ["user", "assistant", "user", "tool"])

    def test_contiguous_assistant_tool_sequence(self):
        """Normal case: assistant(tool_calls) immediately followed by tool results."""
        messages = [
            {"role": "user", "content": "do thing"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "function": {"name": "run_shell", "arguments": "{}"},
                    },
                    {
                        "id": "call_b",
                        "function": {"name": "read_file", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": "result a"},
            {"role": "tool", "tool_call_id": "call_b", "content": "result b"},
            {"role": "user", "content": "context message after results"},
        ]

        result = _strip_orphaned_tool_messages(messages, truncate=False)
        self.assertEqual(len(result), 5)
        roles = [m["role"] for m in result]
        self.assertEqual(roles, ["user", "assistant", "tool", "tool", "user"])


if __name__ == "__main__":
    unittest.main()
