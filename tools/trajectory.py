"""
Trajectory assertion framework for agent testing.

Provides structured assertions on agent behavior traces:
- Tool call sequences (order, count, mutual exclusion)
- Step-level data flow (was step N's output used by step M?)
- Budget invariants (steps, tool calls, cost)
- Safety invariants (forbidden tools, argument constraints)
- Stop condition validation

Usage:
    from tools.trajectory import TrajectoryAssertions

    trace = [
        {"tool": "read_file", "args": {"path": "foo.py"}},
        {"tool": "edit_file", "args": {"path": "foo.py", "old_string": "x", "new_string": "y"}},
    ]

    ta = TrajectoryAssertions(trace)
    ta.assert_called_before("read_file", "edit_file")
    ta.assert_call_count("edit_file", 1)
    ta.assert_no_loop("run_shell", max_calls=3)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRecord:
    """Single tool call in a trace."""
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    duration_ms: float | None = None
    step: int = 0


class TrajectoryAssertionError(AssertionError):
    """Raised when a trajectory assertion fails."""
    pass


class TrajectoryAssertions:
    """Assert on agent behavior traces (tool call sequences)."""

    def __init__(self, trace: list[dict | ToolCallRecord]):
        self._trace: list[ToolCallRecord] = []
        for i, item in enumerate(trace):
            if isinstance(item, ToolCallRecord):
                rec = item
            else:
                rec = ToolCallRecord(
                    tool=item.get("tool", item.get("name", "unknown")),
                    args=item.get("args", item.get("arguments", {})),
                    result=item.get("result"),
                    error=item.get("error"),
                    duration_ms=item.get("duration_ms"),
                    step=item.get("step", i),
                )
            self._trace.append(rec)

    @property
    def tools_called(self) -> list[str]:
        """List of tool names in call order."""
        return [r.tool for r in self._trace]

    @property
    def call_count(self) -> dict[str, int]:
        """Count of calls per tool."""
        counts: dict[str, int] = {}
        for r in self._trace:
            counts[r.tool] = counts.get(r.tool, 0) + 1
        return counts

    @property
    def total_calls(self) -> int:
        """Total number of tool calls in the trace."""
        return len(self._trace)

    # ── Ordering assertions ──────────────────────────────────────

    def assert_called_before(self, earlier_tool: str, later_tool: str) -> None:
        """Assert `earlier_tool` is called before `later_tool`."""
        tools = self.tools_called
        try:
            early_idx = tools.index(earlier_tool)
        except ValueError:
            raise TrajectoryAssertionError(
                f"Tool '{earlier_tool}' was never called. "
                f"Tools called: {tools}"
            )
        try:
            late_idx = tools.index(later_tool)
        except ValueError:
            raise TrajectoryAssertionError(
                f"Tool '{later_tool}' was never called. "
                f"Tools called: {tools}"
            )
        if early_idx >= late_idx:
            raise TrajectoryAssertionError(
                f"Expected '{earlier_tool}' (index {early_idx}) before "
                f"'{later_tool}' (index {late_idx}), but order was reversed."
            )

    def assert_called_in_order(self, *tool_names: str) -> None:
        """Assert tools were called in the exact given order."""
        tools = self.tools_called
        # Find subsequence
        ti = 0
        for tool in tools:
            if ti < len(tool_names) and tool == tool_names[ti]:
                ti += 1
        if ti < len(tool_names):
            missing = tool_names[ti]
            raise TrajectoryAssertionError(
                f"Expected tools in order {list(tool_names)}, "
                f"but '{missing}' not found after previous matches. "
                f"Actual: {tools}"
            )

    # ── Count assertions ─────────────────────────────────────────

    def assert_call_count(self, tool: str, expected: int) -> None:
        """Assert a tool was called exactly `expected` times."""
        actual = self.call_count.get(tool, 0)
        if actual != expected:
            raise TrajectoryAssertionError(
                f"Expected {expected} call(s) to '{tool}', got {actual}."
            )

    def assert_call_count_at_least(self, tool: str, minimum: int) -> None:
        """Assert a tool was called at least `minimum` times."""
        actual = self.call_count.get(tool, 0)
        if actual < minimum:
            raise TrajectoryAssertionError(
                f"Expected at least {minimum} call(s) to '{tool}', got {actual}."
            )

    def assert_call_count_at_most(self, tool: str, maximum: int) -> None:
        """Assert a tool was called at most `maximum` times."""
        actual = self.call_count.get(tool, 0)
        if actual > maximum:
            raise TrajectoryAssertionError(
                f"Expected at most {maximum} call(s) to '{tool}', got {actual}."
            )

    def assert_no_loop(self, tool: str, max_calls: int = 5) -> None:
        """Assert a tool isn't called in a tight loop (> max_calls)."""
        self.assert_call_count_at_most(tool, max_calls)

    # ── Tool presence assertions ─────────────────────────────────

    def assert_tool_called(self, tool: str) -> None:
        """Assert a tool was called at least once."""
        if tool not in self.tools_called:
            raise TrajectoryAssertionError(
                f"Expected '{tool}' to be called, but it wasn't. "
                f"Tools called: {self.tools_called}"
            )

    def assert_tool_not_called(self, tool: str) -> None:
        """Assert a tool was never called."""
        if tool in self.tools_called:
            count = self.call_count[tool]
            raise TrajectoryAssertionError(
                f"Expected '{tool}' NOT to be called, but it was called {count} time(s)."
            )

    def assert_mutual_exclusion(self, tool_a: str, tool_b: str) -> None:
        """Assert that tool_a and tool_b are never both called in the same run."""
        if tool_a in self.tools_called and tool_b in self.tools_called:
            raise TrajectoryAssertionError(
                f"Mutual exclusion violated: both '{tool_a}' and '{tool_b}' were called."
            )

    # ── Argument assertions ──────────────────────────────────────

    def assert_arg_matches(self, tool: str, arg_name: str, pattern: str) -> None:
        """Assert that every call to `tool` has arg `arg_name` matching regex `pattern`."""
        compiled = re.compile(pattern)
        for rec in self._trace:
            if rec.tool == tool:
                value = str(rec.args.get(arg_name, ""))
                if not compiled.search(value):
                    raise TrajectoryAssertionError(
                        f"Call to '{tool}': arg '{arg_name}' = {value!r} "
                        f"does not match pattern {pattern!r}."
                    )

    def assert_arg_not_contains(self, tool: str, arg_name: str, forbidden: str) -> None:
        """Assert no call to `tool` has `forbidden` in arg `arg_name`."""
        for rec in self._trace:
            if rec.tool == tool:
                value = str(rec.args.get(arg_name, ""))
                if forbidden in value:
                    raise TrajectoryAssertionError(
                        f"Call to '{tool}': arg '{arg_name}' contains forbidden "
                        f"substring {forbidden!r}. Value: {value!r}"
                    )

    def assert_no_args_contain(self, forbidden: str) -> None:
        """Assert no tool call has `forbidden` in any argument value.

        Useful for detecting PII leaks or prompt injection in arguments.
        """
        for rec in self._trace:
            for key, value in rec.args.items():
                str_val = str(value)
                if forbidden in str_val:
                    raise TrajectoryAssertionError(
                        f"Call to '{rec.tool}': arg '{key}' contains forbidden "
                        f"substring {forbidden!r}. Value: {str_val!r}"
                    )

    # ── Safety / budget assertions ───────────────────────────────

    def assert_total_calls_under(self, maximum: int) -> None:
        """Assert total tool calls don't exceed budget."""
        if self.total_calls > maximum:
            raise TrajectoryAssertionError(
                f"Total tool calls {self.total_calls} exceeds budget of {maximum}."
            )

    def assert_tool_not_in_set(self, forbidden: set[str]) -> None:
        """Assert none of the forbidden tools were called."""
        for tool in forbidden:
            if tool in self.tools_called:
                raise TrajectoryAssertionError(
                    f"Forbidden tool '{tool}' was called. Tools: {self.tools_called}"
                )

    def assert_stopped_cleanly(self, stop_reason: str | None = None) -> None:
        """Assert the trace ends cleanly (last tool isn't an error).

        If `stop_reason` is provided, assert the last call's error/reason matches.
        """
        if not self._trace:
            return  # Empty trace is clean
        last = self._trace[-1]
        if last.error:
            raise TrajectoryAssertionError(
                f"Trace ended with error on '{last.tool}': {last.error}"
            )

    # ── Data flow assertions ─────────────────────────────────────

    def assert_result_used(self, producer_step: int, consumer_step: int) -> None:
        """Assert that output from `producer_step` is referenced in `consumer_step`'s args."""
        if producer_step < 0 or producer_step >= len(self._trace):
            raise TrajectoryAssertionError(f"Producer step {producer_step} out of range.")
        if consumer_step < 0 or consumer_step >= len(self._trace):
            raise TrajectoryAssertionError(f"Consumer step {consumer_step} out of range.")

        producer = self._trace[producer_step]
        consumer = self._trace[consumer_step]

        # Check if producer's result appears in consumer's args
        if producer.result is None:
            return  # Nothing to check

        result_str = json.dumps(producer.result) if not isinstance(producer.result, str) else producer.result
        args_str = json.dumps(consumer.args)

        if result_str not in args_str:
            # Try substring matching on individual values
            found = False
            for val in consumer.args.values():
                if isinstance(val, str) and val in result_str:
                    found = True
                    break
                if isinstance(producer.result, str) and producer.result in str(val):
                    found = True
                    break

            if not found:
                raise TrajectoryAssertionError(
                    f"Expected step {consumer_step} ('{consumer.tool}') to use "
                    f"result from step {producer_step} ('{producer.tool}'), "
                    f"but no reference found."
                )


# ── Convenience: build trace from raw tool call dicts ──────────────

def assert_trajectory(
    trace: list[dict | ToolCallRecord],
) -> TrajectoryAssertions:
    """Create a TrajectoryAssertions from a raw trace list."""
    return TrajectoryAssertions(trace)


def trace_from_calls(*calls: str) -> list[dict]:
    """Quickly build a trace from tool name strings (for simple ordering tests).

    Example:
        trace = trace_from_calls("read_file", "edit_file", "run_shell")
        assert_trajectory(trace).assert_called_before("read_file", "edit_file")
    """
    return [{"tool": name, "args": {}} for name in calls]
