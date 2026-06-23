"""
Tests for the trajectory assertion framework (tools/trajectory.py).

Covers: ordering, count, mutual exclusion, arguments, safety/budget,
data flow, and convenience functions.
"""

import pytest
from tools.trajectory import (
    TrajectoryAssertions,
    TrajectoryAssertionError,
    ToolCallRecord,
    assert_trajectory,
    trace_from_calls,
)


# ── Helpers ──────────────────────────────────────────────────────

def _make_trace(*tools: str) -> list[dict]:
    return [{"tool": t, "args": {}} for t in tools]


# ── Ordering assertions ──────────────────────────────────────────

class TestOrdering:
    def test_called_before_pass(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "edit_file"))
        ta.assert_called_before("read_file", "edit_file")

    def test_called_before_reversed_fails(self):
        ta = TrajectoryAssertions(_make_trace("edit_file", "read_file"))
        with pytest.raises(TrajectoryAssertionError, match="order was reversed"):
            ta.assert_called_before("read_file", "edit_file")

    def test_called_before_never_called_fails(self):
        ta = TrajectoryAssertions(_make_trace("read_file"))
        with pytest.raises(TrajectoryAssertionError, match="never called"):
            ta.assert_called_before("read_file", "edit_file")

    def test_called_in_order_pass(self):
        ta = TrajectoryAssertions(
            _make_trace("read_file", "search_files", "edit_file", "run_shell")
        )
        ta.assert_called_in_order("read_file", "edit_file", "run_shell")

    def test_called_in_order_fails(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "edit_file"))
        with pytest.raises(TrajectoryAssertionError, match="not found after previous"):
            ta.assert_called_in_order("edit_file", "read_file")

    def test_called_in_order_with_interleaving(self):
        """Subsequence matching: interleaved calls still pass."""
        ta = TrajectoryAssertions(
            _make_trace("read_file", "search_files", "edit_file", "search_files", "run_shell")
        )
        ta.assert_called_in_order("read_file", "edit_file", "run_shell")


# ── Count assertions ─────────────────────────────────────────────

class TestCountAssertions:
    def test_call_count_pass(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "read_file", "edit_file"))
        ta.assert_call_count("read_file", 2)

    def test_call_count_fails(self):
        ta = TrajectoryAssertions(_make_trace("read_file"))
        with pytest.raises(TrajectoryAssertionError, match="Expected 2.*got 1"):
            ta.assert_call_count("read_file", 2)

    def test_call_count_zero_for_missing(self):
        ta = TrajectoryAssertions(_make_trace("read_file"))
        ta.assert_call_count("edit_file", 0)

    def test_call_count_at_least_pass(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "read_file", "read_file"))
        ta.assert_call_count_at_least("read_file", 2)

    def test_call_count_at_least_fails(self):
        ta = TrajectoryAssertions(_make_trace("read_file"))
        with pytest.raises(TrajectoryAssertionError, match="at least 2"):
            ta.assert_call_count_at_least("read_file", 2)

    def test_call_count_at_most_pass(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "read_file"))
        ta.assert_call_count_at_most("read_file", 3)

    def test_call_count_at_most_fails(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "read_file", "read_file"))
        with pytest.raises(TrajectoryAssertionError, match="at most 2"):
            ta.assert_call_count_at_most("read_file", 2)

    def test_no_loop_pass(self):
        ta = TrajectoryAssertions(_make_trace(*(["run_shell"] * 4)))
        ta.assert_no_loop("run_shell", max_calls=5)

    def test_no_loop_fails(self):
        ta = TrajectoryAssertions(_make_trace(*(["run_shell"] * 6)))
        with pytest.raises(TrajectoryAssertionError, match="at most 5"):
            ta.assert_no_loop("run_shell", max_calls=5)


# ── Tool presence assertions ─────────────────────────────────────

class TestPresence:
    def test_tool_called_pass(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "edit_file"))
        ta.assert_tool_called("edit_file")

    def test_tool_called_fails(self):
        ta = TrajectoryAssertions(_make_trace("read_file"))
        with pytest.raises(TrajectoryAssertionError, match="Expected.*to be called"):
            ta.assert_tool_called("edit_file")

    def test_tool_not_called_pass(self):
        ta = TrajectoryAssertions(_make_trace("read_file"))
        ta.assert_tool_not_called("run_shell")

    def test_tool_not_called_fails(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "read_file"))
        with pytest.raises(TrajectoryAssertionError, match="NOT to be called"):
            ta.assert_tool_not_called("read_file")

    def test_mutual_exclusion_pass(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "edit_file"))
        ta.assert_mutual_exclusion("run_shell", "web_search")

    def test_mutual_exclusion_fails(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "edit_file"))
        with pytest.raises(TrajectoryAssertionError, match="Mutual exclusion violated"):
            ta.assert_mutual_exclusion("read_file", "edit_file")


# ── Argument assertions ──────────────────────────────────────────

class TestArgumentAssertions:
    def test_arg_matches_pass(self):
        ta = TrajectoryAssertions([
            {"tool": "edit_file", "args": {"path": "foo.py", "old_string": "hello"}},
        ])
        ta.assert_arg_matches("edit_file", "path", r"\.py$")

    def test_arg_matches_fails(self):
        ta = TrajectoryAssertions([
            {"tool": "edit_file", "args": {"path": "foo.txt", "old_string": "hello"}},
        ])
        with pytest.raises(TrajectoryAssertionError, match="does not match pattern"):
            ta.assert_arg_matches("edit_file", "path", r"\.py$")

    def test_arg_matches_missing_arg(self):
        """Missing arg => empty string, won't match."""
        ta = TrajectoryAssertions([
            {"tool": "edit_file", "args": {"old_string": "hello"}},
        ])
        with pytest.raises(TrajectoryAssertionError, match="does not match pattern"):
            ta.assert_arg_matches("edit_file", "path", r"\.py$")

    def test_arg_not_contains_pass(self):
        ta = TrajectoryAssertions([
            {"tool": "run_shell", "args": {"command": "ls -la"}},
        ])
        ta.assert_arg_not_contains("run_shell", "command", "rm -rf")

    def test_arg_not_contains_fails(self):
        ta = TrajectoryAssertions([
            {"tool": "run_shell", "args": {"command": "rm -rf /"}},
        ])
        with pytest.raises(TrajectoryAssertionError, match="contains forbidden"):
            ta.assert_arg_not_contains("run_shell", "command", "rm -rf")

    def test_no_args_contain_pass(self):
        ta = TrajectoryAssertions([
            {"tool": "edit_file", "args": {"path": "foo.py"}},
            {"tool": "run_shell", "args": {"command": "ls"}},
        ])
        ta.assert_no_args_contain("sk-abc123")

    def test_no_args_contain_fails(self):
        ta = TrajectoryAssertions([
            {"tool": "run_shell", "args": {"command": "echo sk-abc123-secret"}},
        ])
        with pytest.raises(TrajectoryAssertionError, match="contains forbidden"):
            ta.assert_no_args_contain("sk-abc123")


# ── Safety / budget assertions ───────────────────────────────────

class TestSafetyBudget:
    def test_total_calls_under_pass(self):
        ta = TrajectoryAssertions(_make_trace("a", "b", "c"))
        ta.assert_total_calls_under(5)

    def test_total_calls_under_fails(self):
        ta = TrajectoryAssertions(_make_trace("a", "b", "c", "d", "e"))
        with pytest.raises(TrajectoryAssertionError, match="exceeds budget"):
            ta.assert_total_calls_under(3)

    def test_tool_not_in_set_pass(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "edit_file"))
        ta.assert_tool_not_in_set({"run_shell", "web_search"})

    def test_tool_not_in_set_fails(self):
        ta = TrajectoryAssertions(_make_trace("read_file", "run_shell"))
        with pytest.raises(TrajectoryAssertionError, match="Forbidden tool"):
            ta.assert_tool_not_in_set({"run_shell"})

    def test_stopped_cleanly_empty_trace(self):
        ta = TrajectoryAssertions([])
        ta.assert_stopped_cleanly()  # Empty is clean

    def test_stopped_cleanly_pass(self):
        ta = TrajectoryAssertions([
            {"tool": "edit_file", "args": {}},
        ])
        ta.assert_stopped_cleanly()

    def test_stopped_cleanly_with_error_fails(self):
        ta = TrajectoryAssertions([
            {"tool": "run_shell", "args": {}, "error": "timed out"},
        ])
        with pytest.raises(TrajectoryAssertionError, match="ended with error"):
            ta.assert_stopped_cleanly()


# ── Data flow assertions ─────────────────────────────────────────

class TestDataFlow:
    def test_result_used_direct_match(self):
        ta = TrajectoryAssertions([
            {"tool": "read_file", "args": {"path": "foo.py"},
             "result": "def hello(): pass"},
            {"tool": "edit_file", "args": {"path": "foo.py",
             "old_string": "def hello(): pass", "new_string": "def hello(): return 42"}},
        ])
        ta.assert_result_used(0, 1)

    def test_result_used_no_match(self):
        ta = TrajectoryAssertions([
            {"tool": "read_file", "args": {"path": "foo.py"},
             "result": "def hello(): pass"},
            {"tool": "edit_file", "args": {"path": "bar.py",
             "old_string": "unrelated", "new_string": "still_unrelated"}},
        ])
        with pytest.raises(TrajectoryAssertionError, match="no reference found"):
            ta.assert_result_used(0, 1)

    def test_result_used_none_result_skips(self):
        """Producer with None result is not checked."""
        ta = TrajectoryAssertions([
            {"tool": "read_file", "args": {"path": "foo.py"}, "result": None},
            {"tool": "edit_file", "args": {"path": "foo.py", "old_string": "x"}},
        ])
        ta.assert_result_used(0, 1)  # Should not raise

    def test_result_used_out_of_range(self):
        ta = TrajectoryAssertions(_make_trace("a", "b"))
        with pytest.raises(TrajectoryAssertionError, match="out of range"):
            ta.assert_result_used(5, 0)
        with pytest.raises(TrajectoryAssertionError, match="out of range"):
            ta.assert_result_used(0, 5)


# ── ToolCallRecord ───────────────────────────────────────────────

class TestToolCallRecord:
    def test_from_dict_with_alt_key_names(self):
        ta = TrajectoryAssertions([
            {"name": "read_file", "arguments": {"path": "x.py"}, "result": "hello"},
        ])
        assert ta.tools_called == ["read_file"]
        ta.assert_tool_called("read_file")

    def test_step_auto_assigned(self):
        ta = TrajectoryAssertions([
            {"tool": "a"},
            {"tool": "b"},
            {"tool": "c", "step": 99},
        ])
        assert ta._trace[0].step == 0
        assert ta._trace[1].step == 1
        assert ta._trace[2].step == 99  # Explicit overrides auto


# ── Convenience functions ────────────────────────────────────────

class TestConvenience:
    def test_trace_from_calls(self):
        trace = trace_from_calls("read_file", "edit_file", "run_shell")
        assert len(trace) == 3
        assert trace[0]["tool"] == "read_file"
        assert trace[1]["tool"] == "edit_file"
        assert trace[2]["tool"] == "run_shell"

    def test_assert_trajectory_returns_assertions(self):
        ta = assert_trajectory(_make_trace("read_file"))
        assert isinstance(ta, TrajectoryAssertions)
        ta.assert_tool_called("read_file")

    def test_tools_called_and_call_count_properties(self):
        trace = _make_trace("a", "b", "a", "c")
        ta = TrajectoryAssertions(trace)
        assert ta.tools_called == ["a", "b", "a", "c"]
        assert ta.call_count == {"a": 2, "b": 1, "c": 1}
        assert ta.total_calls == 4
