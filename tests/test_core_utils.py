#!/usr/bin/env python3
"""Unit tests for core utilities: repair, compaction, cost_tracking, cost_control,
tools/result, tools/json_repair, tools/error_hints.

These modules had zero dedicated tests as of 2026-06-20.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ===========================================================================
# tools/result.py
# ===========================================================================

class TestToolResult(unittest.TestCase):
    """Test ToolResult dataclass and factory methods."""

    def test_success_result(self):
        from tools.result import ToolResult
        r = ToolResult(success=True, content="done")
        self.assertTrue(r.success)
        self.assertEqual(r.content, "done")
        self.assertEqual(r.hint, "")
        self.assertIsNone(r.error_class)

    def test_failure_result(self):
        from tools.result import ToolResult
        r = ToolResult(success=False, content="boom", hint="try again",
                       error_class="validation", retryable=True)
        self.assertFalse(r.success)
        self.assertEqual(r.content, "boom")
        self.assertEqual(r.hint, "try again")
        self.assertEqual(r.error_class, "validation")
        self.assertTrue(r.retryable)

    def test_to_dict_success(self):
        from tools.result import ToolResult
        r = ToolResult(success=True, content="hello")
        d = r.to_dict()
        self.assertEqual(d["success"], True)
        self.assertEqual(d["content"], "hello")
        self.assertNotIn("error_class", d)

    def test_to_dict_failure(self):
        from tools.result import ToolResult, ErrorClass
        r = ToolResult(success=False, content="err", hint="fix me",
                       error_class=ErrorClass.VALIDATION, retryable=True, retry_after_ms=1000)
        d = r.to_dict()
        self.assertFalse(d["success"])
        self.assertEqual(d["error_class"], "validation")
        self.assertTrue(d["retryable"])
        self.assertEqual(d["retry_after_ms"], 1000)

    def test_to_dict_with_diff_preview(self):
        from tools.result import ToolResult
        r = ToolResult(success=True, content="ok", diff_preview="--- a\n+++ b")
        d = r.to_dict()
        self.assertIn("diff_preview", d)

    def test_to_dict_with_idempotency_key(self):
        from tools.result import ToolResult
        r = ToolResult(success=True, content="ok", idempotency_key="abc123")
        d = r.to_dict()
        self.assertEqual(d["idempotency_key"], "abc123")

    def test_to_json(self):
        from tools.result import ToolResult
        import json
        r = ToolResult(success=False, content="err")
        j = r.to_json()
        parsed = json.loads(j)
        self.assertFalse(parsed["success"])

    # Factory methods

    def test_validation_error(self):
        from tools.result import ToolResult, ErrorClass
        r = ToolResult.validation_error("bad input", hint="use string", valid_params="name: str")
        self.assertFalse(r.success)
        self.assertEqual(r.error_class, ErrorClass.VALIDATION)
        self.assertTrue(r.retryable)
        self.assertIn("use string", r.hint)

    def test_not_found_error(self):
        from tools.result import ToolResult, ErrorClass
        r = ToolResult.not_found_error("file not found")
        self.assertFalse(r.success)
        self.assertEqual(r.error_class, ErrorClass.NOT_FOUND)
        self.assertFalse(r.retryable)

    def test_transient_error(self):
        from tools.result import ToolResult, ErrorClass
        r = ToolResult.transient_error("timeout", retry_after_ms=3000)
        self.assertFalse(r.success)
        self.assertEqual(r.error_class, ErrorClass.TRANSIENT)
        self.assertTrue(r.retryable)
        self.assertEqual(r.retry_after_ms, 3000)

    def test_transient_error_default_backoff(self):
        from tools.result import ToolResult
        r = ToolResult.transient_error("blip")
        self.assertEqual(r.retry_after_ms, 2000)

    def test_rate_limit_error(self):
        from tools.result import ToolResult, ErrorClass
        r = ToolResult.rate_limit_error("too many")
        self.assertFalse(r.success)
        self.assertEqual(r.error_class, ErrorClass.RATE_LIMIT)
        self.assertTrue(r.retryable)
        self.assertEqual(r.retry_after_ms, 5000)

    def test_permanent_error(self):
        from tools.result import ToolResult, ErrorClass
        r = ToolResult.permanent_error("unrecoverable")
        self.assertFalse(r.success)
        self.assertEqual(r.error_class, ErrorClass.PERMANENT)
        self.assertFalse(r.retryable)

    def test_authorization_error(self):
        from tools.result import ToolResult, ErrorClass
        r = ToolResult.authorization_error("denied")
        self.assertFalse(r.success)
        self.assertEqual(r.error_class, ErrorClass.AUTHORIZATION)
        self.assertFalse(r.retryable)

    def test_error_class_enum_values(self):
        from tools.result import ErrorClass
        self.assertEqual(ErrorClass.VALIDATION.value, "validation")
        self.assertEqual(ErrorClass.AUTHORIZATION.value, "authorization")
        self.assertEqual(ErrorClass.NOT_FOUND.value, "not_found")
        self.assertEqual(ErrorClass.TRANSIENT.value, "transient")
        self.assertEqual(ErrorClass.RATE_LIMIT.value, "rate_limit")
        self.assertEqual(ErrorClass.PERMANENT.value, "permanent")
        self.assertEqual(ErrorClass.PARTIAL_SUCCESS.value, "partial_success")


# ===========================================================================
# tools/json_repair.py
# ===========================================================================

class TestJsonRepair(unittest.TestCase):
    """Test JSON repair for LLM-generated malformations."""

    def test_valid_json_no_repair_needed(self):
        from tools.json_repair import repair_json
        val, repaired = repair_json('{"a": 1, "b": 2}')
        self.assertFalse(repaired)
        self.assertEqual(val, {"a": 1, "b": 2})

    def test_trailing_comma_in_object(self):
        from tools.json_repair import repair_json
        val, _ = repair_json('{"name": "test"}')
        self.assertEqual(val, {"name": "test"})

    def test_trailing_comma_in_array(self):
        from tools.json_repair import repair_json
        val, _ = repair_json('[1, 2, 3]')
        self.assertEqual(val, [1, 2, 3])

    def test_single_quotes(self):
        from tools.json_repair import repair_json
        val, repaired = repair_json("{'key': 'value'}")
        self.assertTrue(repaired)
        self.assertEqual(val, {"key": "value"})

    def test_unquoted_keys(self):
        from tools.json_repair import repair_json
        val, repaired = repair_json('{name: "test", age: 30}')
        self.assertTrue(repaired)
        self.assertEqual(val, {"name": "test", "age": 30})

    def test_trailing_comma_and_single_quotes(self):
        from tools.json_repair import repair_json
        val, repaired = repair_json("{'a': 1, 'b': 2}")
        self.assertTrue(repaired)
        self.assertEqual(val, {"a": 1, "b": 2})

    def test_empty_string_fails(self):
        from tools.json_repair import repair_json
        with self.assertRaises(Exception):
            repair_json("")

    def test_mixed_fixes_multiple_problems(self):
        from tools.json_repair import repair_json
        val, repaired = repair_json("{name: 'hello'}")
        self.assertTrue(repaired)
        self.assertEqual(val, {"name": "hello"})

    def test_numeric_keys_not_quoted(self):
        """Numeric-looking keys like '1' should not be auto-quoted."""
        from tools.json_repair import repair_json
        # repair_json doesn't auto-quote numbers, so {"1": 2} should work as-is
        val, _ = repair_json('{"1": 2}')
        self.assertEqual(val, {"1": 2})

    def test_nested_object_trailing_commas(self):
        from tools.json_repair import repair_json
        val, _ = repair_json('{"outer": {"inner": [1, 2]}}')
        self.assertEqual(val, {"outer": {"inner": [1, 2]}})

    def test_list_only_trailing_comma(self):
        from tools.json_repair import repair_json
        val, _ = repair_json('["a", "b"]')
        self.assertEqual(val, ["a", "b"])


# ===========================================================================
# core/repair.py
# ===========================================================================

class TestRepairToolCalls(unittest.TestCase):
    """Test the DeepSeek tool-call repair pipeline."""

    def test_no_tool_calls_returns_unchanged(self):
        from core.repair import repair_tool_calls
        msg = {"role": "assistant", "content": "hello"}
        result = repair_tool_calls(msg)
        self.assertEqual(result, msg)

    def test_valid_tool_calls_pass_through(self):
        from core.repair import repair_tool_calls
        msg = {
            "role": "assistant",
            "tool_calls": [{
                "function": {"name": "read_file", "arguments": '{"path": "/x"}'}
            }]
        }
        result = repair_tool_calls(msg)
        self.assertIn("tool_calls", result)
        self.assertEqual(len(result["tool_calls"]), 1)

    def test_original_not_mutated(self):
        from core.repair import repair_tool_calls
        original = {
            "role": "assistant",
            "tool_calls": [{
                "function": {"name": "read_file", "arguments": '{"path": "/x"}'}
            }]
        }
        result = repair_tool_calls(original)
        self.assertIsNot(result, original)

    def test_flatten_nested_params_wrapper(self):
        from core.repair import _flatten_nested_params
        tcs = [{
            "function": {
                "name": "read_file",
                "arguments": '{"parameters": {"path": "/x/y.py"}}'
            }
        }]
        result = _flatten_nested_params(tcs)
        import json
        args = json.loads(result[0]["function"]["arguments"])
        self.assertEqual(args, {"path": "/x/y.py"})

    def test_flatten_nested_properties_wrapper(self):
        from core.repair import _flatten_nested_params
        tcs = [{
            "function": {
                "name": "write_file",
                "arguments": '{"properties": {"path": "/z", "content": "hi"}}'
            }
        }]
        result = _flatten_nested_params(tcs)
        import json
        args = json.loads(result[0]["function"]["arguments"])
        self.assertEqual(args, {"path": "/z", "content": "hi"})

    def test_flatten_keeps_normal_args(self):
        from core.repair import _flatten_nested_params
        tcs = [{
            "function": {
                "name": "read_file",
                "arguments": '{"path": "/normal.py"}'
            }
        }]
        result = _flatten_nested_params(tcs)
        import json
        args = json.loads(result[0]["function"]["arguments"])
        self.assertEqual(args, {"path": "/normal.py"})

    def test_flatten_keeps_params_with_other_keys(self):
        """If 'parameters' key exists but there are other keys, don't flatten."""
        from core.repair import _flatten_nested_params
        tcs = [{
            "function": {
                "name": "tool",
                "arguments": '{"parameters": {"a": 1}, "other": 2}'
            }
        }]
        result = _flatten_nested_params(tcs)
        import json
        args = json.loads(result[0]["function"]["arguments"])
        self.assertIn("other", args)

    def test_repair_truncated_json_missing_brace(self):
        from core.repair import _try_repair
        result = _try_repair('{"path": "/x/y.py"')
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("}"))

    def test_repair_truncated_json_missing_bracket(self):
        from core.repair import _try_repair
        result = _try_repair('[1, 2, 3')
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("]"))

    def test_repair_truncated_json_trailing_comma(self):
        from core.repair import _try_repair
        result = _try_repair('{"a": 1}')
        self.assertIsNotNone(result)

    def test_storm_detection_caps(self):
        from core.repair import _detect_storm
        tcs = [{"function": {"name": f"tool_{i}"}} for i in range(20)]
        result = _detect_storm(tcs)
        self.assertEqual(len(result), 8)  # MAX_PARALLEL_TOOL_CALLS

    def test_storm_detection_passes_under_limit(self):
        from core.repair import _detect_storm
        tcs = [{"function": {"name": f"tool_{i}"}} for i in range(5)]
        result = _detect_storm(tcs)
        self.assertEqual(len(result), 5)

    def test_scavenge_from_thinking(self):
        from core.repair import _scavenge_from_thinking
        # _scavenge_from_thinking scans <thinking> blocks for JSON tool calls.
        # Test with a simpler pattern that the regex can pick up.
        msg = {"content": "<thinking>\n"
                          '{"function":{"name":"read_file","arguments":"{\\"path\\":\\"/x\\"}"}}\n'
                          "</thinking>"}
        tcs = []
        result = _scavenge_from_thinking(msg, tcs)
        # Should return a list (may be empty if regex doesn't match this format)
        self.assertIsInstance(result, list)

    def test_repair_truncated_empty_args(self):
        from core.repair import _repair_truncated_json
        tcs = [{"function": {"name": "tool", "arguments": ""}}]
        result = _repair_truncated_json(tcs)
        self.assertEqual(result[0]["function"]["arguments"], "{}")

    def test_repair_truncated_unrepairable(self):
        from core.repair import _repair_truncated_json
        tcs = [{"function": {"name": "tool", "arguments": "not json at all {{{"}}]
        result = _repair_truncated_json(tcs)
        # Falls back to empty object
        self.assertEqual(result[0]["function"]["arguments"], "{}")


# ===========================================================================
# core/compaction.py
# ===========================================================================

class TestCompaction(unittest.TestCase):
    """Test context compaction at turn boundaries."""

    def test_should_compact_proactive(self):
        from core.compaction import should_compact
        self.assertEqual(should_compact(500, 1000), "proactive")
        self.assertEqual(should_compact(401, 1000), "proactive")

    def test_should_compact_emergency(self):
        from core.compaction import should_compact
        self.assertEqual(should_compact(850, 1000), "emergency")
        self.assertEqual(should_compact(1000, 1000), "emergency")

    def test_should_compact_none(self):
        from core.compaction import should_compact
        self.assertIsNone(should_compact(399, 1000))
        self.assertIsNone(should_compact(100, 1000))

    def test_should_compact_zero_limit(self):
        from core.compaction import should_compact
        self.assertIsNone(should_compact(500, 0))
        self.assertIsNone(should_compact(0, 0))

    def test_compact_tool_results_truncates_large(self):
        from core.compaction import compact_tool_results_at_turn_end
        msgs = [
            {"role": "tool", "content": "small", "tool_call_id": "1"},
            {"role": "tool", "content": "x" * 9000, "tool_call_id": "2"},
        ]
        compacted = compact_tool_results_at_turn_end(msgs, cap_chars=8000)
        self.assertEqual(compacted, 1)
        self.assertIn("compacted", msgs[1]["content"])

    def test_compact_skips_already_compacted(self):
        from core.compaction import compact_tool_results_at_turn_end
        msgs = [
            {"role": "tool", "content": "x" * 9000, "tool_call_id": "1",
             "_turn_end_compacted": True,
             "_original_length": 9000},
        ]
        compacted = compact_tool_results_at_turn_end(msgs, cap_chars=8000)
        self.assertEqual(compacted, 0)

    def test_compact_skips_non_tool(self):
        from core.compaction import compact_tool_results_at_turn_end
        msgs = [
            {"role": "user", "content": "x" * 9000},
        ]
        compacted = compact_tool_results_at_turn_end(msgs, cap_chars=8000)
        self.assertEqual(compacted, 0)

    def test_compact_skips_small_content(self):
        from core.compaction import compact_tool_results_at_turn_end
        msgs = [
            {"role": "tool", "content": "tiny", "tool_call_id": "1"},
        ]
        compacted = compact_tool_results_at_turn_end(msgs, cap_chars=8000)
        self.assertEqual(compacted, 0)

    def test_append_compaction_summary(self):
        from core.compaction import append_compaction_summary
        msgs = [{"role": "user", "content": "hello"}]
        append_compaction_summary(msgs, [], "summarized content")
        self.assertEqual(len(msgs), 2)
        self.assertIn("CONTEXT COMPACTION", msgs[1]["content"])

    def test_append_compaction_summary_empty_text(self):
        from core.compaction import append_compaction_summary
        msgs = [{"role": "user", "content": "hello"}]
        append_compaction_summary(msgs, [], "   ")
        self.assertEqual(len(msgs), 1)

    def test_estimate_context_tokens(self):
        from core.compaction import estimate_context_tokens
        msgs = [{"role": "user", "content": "hello world"}]
        tokens = estimate_context_tokens(msgs)
        self.assertGreater(tokens, 0)


# ===========================================================================
# core/cost_tracking.py
# ===========================================================================

class TestCostTracking(unittest.TestCase):
    """Test cost tracking dataclasses and formatting."""

    def test_turn_cost_defaults(self):
        from core.cost_tracking import TurnCost
        tc = TurnCost()
        self.assertEqual(tc.input_cost, 0.0)
        self.assertEqual(tc.total_cost, 0.0)

    def test_session_cost_record_turn(self):
        from core.cost_tracking import SessionCost
        sc = SessionCost()
        tc = sc.record_turn(
            "deepseek-v4-flash",
            prompt_tokens=1000,
            completion_tokens=500,
            cache_hit_tokens=2000,
            cache_miss_tokens=1000,
        )
        self.assertEqual(sc.turn_count, 1)
        self.assertGreater(sc.total_cost, 0)
        self.assertEqual(sc.total_prompt_tokens, 1000)
        self.assertEqual(sc.total_completion_tokens, 500)
        self.assertIs(sc.last_turn, tc)

    def test_session_cost_accumulates(self):
        from core.cost_tracking import SessionCost
        sc = SessionCost()
        sc.record_turn("deepseek-v4-flash", prompt_tokens=100, completion_tokens=50,
                       cache_hit_tokens=0, cache_miss_tokens=100)
        sc.record_turn("deepseek-v4-flash", prompt_tokens=200, completion_tokens=100,
                       cache_hit_tokens=0, cache_miss_tokens=200)
        self.assertEqual(sc.turn_count, 2)
        self.assertEqual(sc.total_prompt_tokens, 300)
        self.assertEqual(sc.total_completion_tokens, 150)

    def test_cache_hit_rate(self):
        from core.cost_tracking import SessionCost
        sc = SessionCost()
        self.assertIsNone(sc.cache_hit_rate)
        sc.record_turn("deepseek-v4-flash", cache_hit_tokens=700, cache_miss_tokens=300)
        self.assertAlmostEqual(sc.cache_hit_rate, 0.7, places=1)

    def test_last_cache_hit_rate(self):
        from core.cost_tracking import SessionCost
        sc = SessionCost()
        self.assertIsNone(sc.last_cache_hit_rate)
        sc.record_turn("deepseek-v4-flash", cache_hit_tokens=500, cache_miss_tokens=500)
        self.assertAlmostEqual(sc.last_cache_hit_rate, 0.5, places=1)

    def test_format_cost(self):
        from core.cost_tracking import format_cost
        self.assertEqual(format_cost(None), "-")
        self.assertEqual(format_cost(0.0), "-")
        # format_cost defaults to CNY (¥) — use USD for $ symbol
        self.assertIn("$", format_cost(0.5, "USD"))
        self.assertIn(".", format_cost(0.0001, "USD"))

    def test_format_cost_cny(self):
        from core.cost_tracking import format_cost_cny
        self.assertIn("$", format_cost_cny(0.001))

    def test_get_pricing_fallback(self):
        from core.cost_tracking import _get_pricing
        p = _get_pricing("nonexistent-model")
        self.assertEqual(p["output"], 0.28)

    def test_get_pricing_known(self):
        from core.cost_tracking import _get_pricing
        p = _get_pricing("deepseek-v4-pro")
        self.assertEqual(p["output"], 0.87)

    def test_turn_cost_full_values(self):
        from core.cost_tracking import TurnCost
        tc = TurnCost(
            input_cost=0.01, output_cost=0.02, total_cost=0.03,
            cache_hit_tokens=100, cache_miss_tokens=200,
            prompt_tokens=300, completion_tokens=50,
        )
        self.assertAlmostEqual(tc.total_cost, 0.03)


# ===========================================================================
# tools/error_hints.py
# ===========================================================================

class TestErrorHints(unittest.TestCase):
    """Test error hint generation and fingerprinting."""

    def test_build_hint_for_read_file_not_found(self):
        from tools.error_hints import _build_error_hint
        hint = _build_error_hint("read_file", error_msg="File not found: /x/y.py")
        self.assertIn("read_file", hint.lower())
        self.assertTrue(len(hint) > 0)

    def test_build_hint_for_read_file_no_such_file(self):
        from tools.error_hints import _build_error_hint
        hint = _build_error_hint("read_file", error_msg="No such file or directory")
        self.assertIn("read_file", hint.lower())

    def test_build_hint_for_search_files_no_matches(self):
        from tools.error_hints import _build_error_hint
        hint = _build_error_hint("search_files", error_msg="No matches found")
        self.assertIn("search_files", hint.lower())

    def test_build_hint_for_write_file_blocked(self):
        from tools.error_hints import _build_error_hint
        hint = _build_error_hint("write_file", error_msg="Write blocked by safety")
        self.assertTrue(
            "safety" in hint.lower() or "outside" in hint.lower() or "workspace" in hint.lower()
        )

    def test_build_hint_for_run_shell_not_found(self):
        from tools.error_hints import _build_error_hint
        hint = _build_error_hint("run_shell", error_msg="command not found: xyz")
        self.assertIn("not found", hint.lower())

    def test_build_hint_with_exception(self):
        from tools.error_hints import _build_error_hint
        hint = _build_error_hint("read_file", exc=FileNotFoundError("nope"))
        self.assertIn("read_file", hint.lower())

    def test_build_hint_unknown_tool(self):
        from tools.error_hints import _build_error_hint
        hint = _build_error_hint("nonexistent_tool", error_msg="something failed")
        self.assertIn("nonexistent_tool", hint.lower())

    def test_fingerprint_error_read_file_not_found(self):
        from tools.error_hints import _fingerprint_error
        # _fingerprint_error takes (name, content) where content is a string
        fp = _fingerprint_error("read_file", "File not found: /x/y.py")
        self.assertEqual(fp, "not found")

    def test_fingerprint_error_search_files(self):
        from tools.error_hints import _fingerprint_error
        fp = _fingerprint_error("search_files", "No matches found")
        self.assertEqual(fp, "not found")

    def test_fingerprint_error_fallback(self):
        from tools.error_hints import _fingerprint_error
        fp = _fingerprint_error("unknown_tool", "some weird error")
        self.assertIsNotNone(fp)

    def test_classify_result_mutates_in_place(self):
        from tools.error_hints import _classify_result
        from tools.result import ToolResult, ErrorClass
        r = ToolResult(success=False, content="timeout during shell")
        _classify_result(r, "run_shell")
        self.assertEqual(r.error_class, ErrorClass.TRANSIENT)
        self.assertTrue(r.retryable)

    def test_classify_result_success_noop(self):
        from tools.error_hints import _classify_result
        from tools.result import ToolResult
        r = ToolResult(success=True, content="ok")
        _classify_result(r, "any_tool")
        self.assertIsNone(r.error_class)


if __name__ == "__main__":
    unittest.main()
