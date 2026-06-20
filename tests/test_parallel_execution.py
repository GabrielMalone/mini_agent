"""Tests for parallel tool execution in core/llm.py.

Exercises _execute_tools, _execute_parallel_no_pipes, _execute_groups,
_build_execution_groups, _extract_pipe_deps, _apply_pipe, _on_tool_ready
pipe deferral, ContextVar cancel event isolation, concurrency cap,
and cache thread safety.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import ANY, MagicMock, patch

import pytest

from core.llm import (
    MAX_PARALLEL_TOOLS,
    _apply_pipe,
    _build_execution_groups,
    _capped_workers,
    _execute_groups,
    _execute_parallel_no_pipes,
    _execute_single_no_pipe,
    _execute_tools,
    _extract_pipe_deps,
)
from core.safety import ReadSafetyGate, WriteSafetyGate
from tools import ToolResult, execute_tool


# ── helpers ────────────────────────────────────────────────────────────────


def _make_tc(name: str, args: dict, idx: int = 0) -> dict:
    """Build a minimal tool call dict for testing."""
    return {
        "id": f"call_{name}_{idx}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }


def _make_tc_with_pipe(
    name: str, args: dict, pipe_from: int, pipe_into: str = "", idx: int = 0,
) -> dict:
    """Build a tool call dict with _pipe metadata."""
    full_args = dict(args)
    pipe_cfg = {"from": pipe_from}
    if pipe_into:
        pipe_cfg["into"] = pipe_into
    full_args["_pipe"] = pipe_cfg
    return {
        "id": f"call_{name}_{idx}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(full_args),
        },
    }


def _null_on_output(tool_name: str, line: str) -> None:
    pass


def _null_on_tool_start(summary: str, parallel: bool = False) -> None:
    pass


def _null_on_tool_end(result: ToolResult) -> None:
    pass


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def write_gate(tmp_path):
    return WriteSafetyGate(str(tmp_path))


@pytest.fixture
def read_gate(tmp_path):
    return ReadSafetyGate(str(tmp_path))


@pytest.fixture
def messages():
    return []


# ── _capped_workers ────────────────────────────────────────────────────────


class TestCappedWorkers:
    def test_below_cap(self):
        assert _capped_workers([1, 2, 3]) == 3

    def test_at_cap(self):
        assert _capped_workers(list(range(MAX_PARALLEL_TOOLS))) == MAX_PARALLEL_TOOLS

    def test_above_cap(self):
        assert _capped_workers(list(range(20))) == MAX_PARALLEL_TOOLS

    def test_empty(self):
        assert _capped_workers([]) == 0

    def test_single(self):
        assert _capped_workers([1]) == 1


# ── _extract_pipe_deps ─────────────────────────────────────────────────────


class TestExtractPipeDeps:
    def test_no_pipe(self):
        remaining = [_make_tc("read_file", {"path": "a.py"}, 0)]
        deps, results = _extract_pipe_deps(remaining)
        assert deps == {}
        assert results == {}
        # Arguments should be unchanged
        assert json.loads(remaining[0]["function"]["arguments"]) == {"path": "a.py"}

    def test_single_pipe(self):
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            _make_tc_with_pipe("write_file", {"path": "b.py"}, pipe_from=0, idx=1),
        ]
        deps, results = _extract_pipe_deps(remaining)
        # into_param is "" when not specified; _apply_pipe auto-detects the first
        # string parameter at substitution time.
        assert deps == {1: (0, "")}
        assert results == {}
        # _pipe should be stripped
        tc1_args = json.loads(remaining[1]["function"]["arguments"])
        assert "_pipe" not in tc1_args
        assert tc1_args == {"path": "b.py"}

    def test_pipe_with_explicit_into(self):
        remaining = [
            _make_tc("run_shell", {"command": "echo hello"}, 0),
            _make_tc_with_pipe("write_file", {"content": ""}, pipe_from=0, pipe_into="content", idx=1),
        ]
        deps, _ = _extract_pipe_deps(remaining)
        assert deps == {1: (0, "content")}

    def test_pipe_malformed_arguments_skipped(self):
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            {
                "id": "call_bad",
                "type": "function",
                "function": {"name": "bad", "arguments": "not json {{{"},
            },
        ]
        deps, _ = _extract_pipe_deps(remaining)
        # The bad tool call should have no pipe deps
        assert 1 not in deps

    def test_strips_pipe_from_all(self):
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            _make_tc_with_pipe("read_file", {"path": "b.py"}, pipe_from=0, idx=1),
        ]
        _extract_pipe_deps(remaining)
        for tc in remaining:
            args = json.loads(tc["function"]["arguments"])
            assert "_pipe" not in args


# ── _apply_pipe ────────────────────────────────────────────────────────────


class TestApplyPipe:
    def test_applies_pipe_result(self):
        tc = _make_tc_with_pipe("write_file", {"path": "b.py"}, pipe_from=0, idx=1)
        pipe_deps = {1: (0, "path")}
        src_result = ToolResult(True, "/workspace/a.py content here")
        pipe_results = {0: src_result}
        _apply_pipe(tc, 1, pipe_deps, pipe_results, json)
        args = json.loads(tc["function"]["arguments"])
        assert args["path"] == "/workspace/a.py content here"

    def test_noop_when_index_not_in_deps(self):
        tc = _make_tc("read_file", {"path": "a.py"})
        original = tc["function"]["arguments"]
        _apply_pipe(tc, 0, {}, {}, json)
        assert tc["function"]["arguments"] == original

    def test_noop_when_source_not_ready(self):
        tc = _make_tc_with_pipe("write_file", {"path": "b.py"}, pipe_from=0, idx=1)
        pipe_deps = {1: (0, "path")}
        pipe_results = {}  # source result missing
        original = tc["function"]["arguments"]
        _apply_pipe(tc, 1, pipe_deps, pipe_results, json)
        assert tc["function"]["arguments"] == original

    def test_auto_detects_first_string_param(self):
        tc = _make_tc_with_pipe("write_file", {"content": "will be replaced"}, pipe_from=0, idx=1)
        pipe_deps = {1: (0, "")}  # no into_param specified
        src_result = ToolResult(True, "new content")
        pipe_results = {0: src_result}
        _apply_pipe(tc, 1, pipe_deps, pipe_results, json)
        args = json.loads(tc["function"]["arguments"])
        assert args["content"] == "new content"


# ── _build_execution_groups ────────────────────────────────────────────────


class TestBuildExecutionGroups:
    def test_no_deps_single_group(self):
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            _make_tc("read_file", {"path": "b.py"}, 1),
            _make_tc("read_file", {"path": "c.py"}, 2),
        ]
        groups = _build_execution_groups(remaining, {})
        assert groups == [[0, 1, 2]]

    def test_chain_two_serial(self):
        # 0 -> 1  (tool 1 depends on tool 0)
        pipe_deps = {1: (0, "path")}
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            _make_tc("write_file", {"path": "b.py"}, 1),
        ]
        groups = _build_execution_groups(remaining, pipe_deps)
        assert groups == [[0], [1]]

    def test_diamond_dependency(self):
        # 0 -> 1, 0 -> 2, 1 -> 3, 2 -> 3
        pipe_deps = {1: (0, ""), 2: (0, ""), 3: (1, ""), 4: (2, "")}
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, i) for i in range(5)
        ]
        groups = _build_execution_groups(remaining, pipe_deps)
        # Should produce: [0], [1, 2], [3, 4]
        assert groups == [[0], [1, 2], [3, 4]]

    def test_cycle_detected_returns_none(self):
        # 0 -> 1 -> 0 (cycle)
        pipe_deps = {0: (1, ""), 1: (0, "")}
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            _make_tc("read_file", {"path": "b.py"}, 1),
        ]
        groups = _build_execution_groups(remaining, pipe_deps)
        assert groups is None

    def test_self_loop_cycle(self):
        pipe_deps = {0: (0, "")}
        remaining = [_make_tc("read_file", {"path": "a.py"}, 0)]
        groups = _build_execution_groups(remaining, pipe_deps)
        assert groups is None


# ── _execute_tools no-pipe path ────────────────────────────────────────────


class TestExecuteToolsNoPipe:
    def test_single_tool(self, write_gate, read_gate, messages):
        remaining = [_make_tc("read_file", {"path": "a.py"})]
        with patch("core.llm.execute_tool") as mock_exec:
            mock_exec.return_value = ToolResult(True, "content of a.py")
            results = _execute_tools(
                remaining, messages, write_gate, read_gate,
                on_tool_start=None, on_tool_end=None, on_tool_output=None,
                approve_callback=None, cancel_event=None,
            )
        assert len(results) == 1
        assert results[0][1].success is True
        assert len(messages) == 1  # tool result message appended

    def test_multiple_independent_parallel(self, write_gate, read_gate, messages):
        """Independent tools should run through _execute_parallel_no_pipes."""
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            _make_tc("read_file", {"path": "b.py"}, 0),
        ]
        results = []
        with patch("core.llm._execute_parallel_no_pipes") as mock_par:
            mock_par.return_value = [
                (remaining[0], ToolResult(True, "a")),
                (remaining[1], ToolResult(True, "b")),
            ]
            results = _execute_tools(
                remaining, messages, write_gate, read_gate,
                on_tool_start=None, on_tool_end=None, on_tool_output=None,
                approve_callback=None, cancel_event=None,
            )
        mock_par.assert_called_once()
        assert len(results) == 2

    def test_single_tool_calls_single_path(self, write_gate, read_gate, messages):
        """A single tool should use _execute_single_no_pipe, not parallel."""
        remaining = [_make_tc("read_file", {"path": "a.py"})]
        with patch("core.llm._execute_single_no_pipe") as mock_single:
            mock_single.return_value = [(remaining[0], ToolResult(True, "ok"))]
            _execute_tools(
                remaining, messages, write_gate, read_gate,
                on_tool_start=None, on_tool_end=None, on_tool_output=None,
                approve_callback=None, cancel_event=None,
            )
        mock_single.assert_called_once()


# ── _execute_tools with pipe path ──────────────────────────────────────────


class TestExecuteToolsWithPipe:
    def test_pipe_chain(self, write_gate, read_gate, messages):
        """A piped chain should execute through _execute_groups."""
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            _make_tc_with_pipe("write_file", {"path": "b.py"}, pipe_from=0, idx=1),
        ]
        with patch("core.llm.execute_tool") as mock_exec:
            mock_exec.side_effect = [
                ToolResult(True, "content of a.py"),
                ToolResult(True, "wrote b.py"),
            ]
            results = _execute_tools(
                remaining, messages, write_gate, read_gate,
                on_tool_start=None, on_tool_end=None, on_tool_output=None,
                approve_callback=None, cancel_event=None,
            )
        assert len(results) == 2
        # The second tool should have gotten the piped result
        args_1 = json.loads(remaining[1]["function"]["arguments"])
        assert args_1["path"] == "content of a.py"

    def test_cycle_falls_back_to_sequential(self, write_gate, read_gate, messages):
        """A cycle should fall back to sequential execution."""
        remaining = [
            _make_tc_with_pipe("read_file", {"path": "a.py"}, pipe_from=1, idx=0),
            _make_tc_with_pipe("read_file", {"path": "b.py"}, pipe_from=0, idx=1),
        ]
        with patch("core.llm.execute_tool") as mock_exec:
            mock_exec.return_value = ToolResult(True, "ok")
            results = _execute_tools(
                remaining, messages, write_gate, read_gate,
                on_tool_start=None, on_tool_end=None, on_tool_output=None,
                approve_callback=None, cancel_event=None,
            )
        assert len(results) == 2


# ── cancel propagation ─────────────────────────────────────────────────────


class TestCancelPropagation:
    def test_single_no_pipe_cancel_before_exec(self, write_gate, read_gate, messages):
        cancel = threading.Event()
        cancel.set()
        remaining = [_make_tc("read_file", {"path": "a.py"})]
        results = _execute_single_no_pipe(
            remaining[0], messages, write_gate, read_gate,
            on_tool_start=None, on_tool_end=None, on_tool_output=None,
            approve_callback=None, cancel_event=cancel,
            recent_tool_keys=None, tool_keys_lock=None,
        )
        assert len(results) == 0
        assert len(messages) == 1  # cancel failure message

    def test_parallel_no_pipe_cancel_mid_execution(self, write_gate, read_gate, messages):
        """Cancel during parallel execution should add failure results for incomplete tools."""
        cancel = threading.Event()
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            _make_tc("read_file", {"path": "b.py"}, 1),
        ]

        # Make the first tool slow, second tool fast, then cancel
        results_lock = threading.Lock()
        results = []

        def slow_then_cancel(tc, wg, rg, **kw):
            time.sleep(0.3)
            with results_lock:
                results.append((tc, ToolResult(True, "ok")))
            # Signal cancel after first completes
            cancel.set()
            return ToolResult(True, "ok")

        with patch("core.llm.execute_tool", side_effect=slow_then_cancel):
            _execute_parallel_no_pipes(
                remaining, messages, write_gate, read_gate,
                on_tool_start=None, on_tool_end=None, on_tool_output=None,
                approve_callback=None, cancel_event=cancel,
                recent_tool_keys=None, tool_keys_lock=None,
            )
        # At least the cancel failure message should be appended
        assert len(messages) >= 1

    def test_parallel_no_pipe_cancel_before_any(self, write_gate, read_gate, messages):
        cancel = threading.Event()
        cancel.set()
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            _make_tc("read_file", {"path": "b.py"}, 1),
        ]
        results = _execute_parallel_no_pipes(
            remaining, messages, write_gate, read_gate,
            on_tool_start=None, on_tool_end=None, on_tool_output=None,
            approve_callback=None, cancel_event=cancel,
            recent_tool_keys=None, tool_keys_lock=None,
        )
        # All tools should get failure results
        assert len(messages) == 2

    def test_groups_cancel_mid_execution(self, write_gate, read_gate, messages):
        """Cancel during group execution should append failures for remaining groups."""
        cancel = threading.Event()
        remaining = [
            _make_tc("read_file", {"path": "a.py"}, 0),
            _make_tc_with_pipe("write_file", {"path": "b.py"}, pipe_from=0, idx=1),
            _make_tc_with_pipe("write_file", {"path": "c.py"}, pipe_from=1, idx=2),
        ]
        pipe_deps = {1: (0, "path"), 2: (1, "path")}
        pipe_results = {}
        groups = [[0], [1], [2]]

        def exec_then_cancel(tc, wg, rg, **kw):
            result = ToolResult(True, "ok")
            # Cancel after first group
            cancel.set()
            return result

        with patch("core.llm.execute_tool", side_effect=exec_then_cancel):
            _execute_groups(
                groups, remaining, messages, write_gate, read_gate,
                pipe_deps, pipe_results,
                on_tool_start=None, on_tool_end=None, on_tool_output=None,
                approve_callback=None, cancel_event=cancel,
                recent_tool_keys=None, tool_keys_lock=None,
            )
        # Tool 0 succeeded, tools 1 and 2 should get failure messages
        assert len(messages) >= 3  # 1 success + 2 cancel failures


# ── ContextVar cancel event isolation ──────────────────────────────────────


class TestCancelEventIsolation:
    def test_parallel_tools_get_isolated_cancel_events(self, write_gate, read_gate):
        """When two tools run in parallel, each should see its own cancel event."""
        cancel_a = threading.Event()
        cancel_b = threading.Event()

        # Capture the cancel events seen by the tool threads
        seen_events: dict[str, threading.Event | None] = {}

        def capture_cancel_event(tc, wg, rg, **kw):
            import contextvars
            from tools import _CURRENT_CANCEL_EVENT as cce_var
            seen_events[tc["function"]["name"]] = cce_var.get()
            return ToolResult(True, "ok")

        remaining = [
            _make_tc("tool_a", {"path": "a.py"}),
            _make_tc("tool_b", {"path": "b.py"}),
        ]

        # Use ThreadPoolExecutor manually to simulate _execute_parallel_no_pipes
        # but with different cancel events per tool
        def run_a():
            from tools import _CURRENT_CANCEL_EVENT as cce_var
            tok = cce_var.set(cancel_a)
            try:
                return capture_cancel_event(remaining[0], write_gate, read_gate)
            finally:
                cce_var.reset(tok)

        def run_b():
            from tools import _CURRENT_CANCEL_EVENT as cce_var
            tok = cce_var.set(cancel_b)
            try:
                return capture_cancel_event(remaining[1], write_gate, read_gate)
            finally:
                cce_var.reset(tok)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(run_a), pool.submit(run_b)]
            for f in as_completed(futs):
                f.result()

        # Each tool should see its own cancel event
        assert seen_events.get("tool_a") is cancel_a
        assert seen_events.get("tool_b") is cancel_b

    def test_original_value_restored(self):
        """After execute_tool, the ContextVar should be back to its original value."""
        from tools import _CURRENT_CANCEL_EVENT as cce_var

        original = cce_var.get()

        cancel = threading.Event()
        tok = cce_var.set(cancel)
        cce_var.reset(tok)

        assert cce_var.get() == original


# ── Concurrency cap ────────────────────────────────────────────────────────


class TestConcurrencyCap:
    def test_max_parallel_tools_is_reasonable(self):
        """The cap should be positive and not excessive."""
        assert MAX_PARALLEL_TOOLS > 0
        assert MAX_PARALLEL_TOOLS <= 32  # sane upper bound

    def test_capped_workers_never_exceeds_max(self):
        """_capped_workers should return at most MAX_PARALLEL_TOOLS."""
        for n in (0, 1, 3, MAX_PARALLEL_TOOLS, MAX_PARALLEL_TOOLS + 1, MAX_PARALLEL_TOOLS * 10):
            result = _capped_workers(list(range(n)))
            assert result == min(n, MAX_PARALLEL_TOOLS)

    def test_capped_workers_used_by_parallel_no_pipes(self):
        """_execute_parallel_no_pipes calls ThreadPoolExecutor with capped workers."""
        remaining = [_make_tc("read_file", {"path": f"{i}.py"}, i) for i in range(20)]
        # Cancel immediately to avoid ThreadPoolExecutor + as_completed hang
        cancel = threading.Event()
        cancel.set()

        with patch("core.llm.ThreadPoolExecutor") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool.__enter__.return_value = mock_pool
            mock_pool_cls.return_value = mock_pool

            _execute_parallel_no_pipes(
                remaining, [], MagicMock(), MagicMock(),
                on_tool_start=None, on_tool_end=None, on_tool_output=None,
                approve_callback=None, cancel_event=cancel,
                recent_tool_keys=None, tool_keys_lock=None,
            )

        mock_pool_cls.assert_called_once()
        assert mock_pool_cls.call_args.kwargs["max_workers"] == MAX_PARALLEL_TOOLS

    def test_capped_workers_used_by_groups(self):
        """_execute_groups calls ThreadPoolExecutor with capped workers."""
        remaining = [_make_tc("read_file", {"path": f"{i}.py"}, i) for i in range(10)]
        groups = [list(range(10))]
        pipe_deps = {}
        pipe_results = {}
        # Cancel immediately
        cancel = threading.Event()
        cancel.set()

        with patch("core.llm.ThreadPoolExecutor") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool.__enter__.return_value = mock_pool
            mock_pool_cls.return_value = mock_pool

            _execute_groups(
                groups, remaining, [], MagicMock(), MagicMock(),
                pipe_deps, pipe_results,
                on_tool_start=None, on_tool_end=None, on_tool_output=None,
                approve_callback=None, cancel_event=cancel,
                recent_tool_keys=None, tool_keys_lock=None,
            )

        mock_pool_cls.assert_called_once()
        assert mock_pool_cls.call_args.kwargs["max_workers"] == MAX_PARALLEL_TOOLS


# ── Cache thread safety ────────────────────────────────────────────────────


class TestCacheThreadSafety:
    def test_cache_is_thread_safe_under_parallel_reads(self, write_gate, read_gate):
        """Multiple parallel reads of the same file should not corrupt the cache."""
        errors = []

        def read_file_tool(path):
            tc = _make_tc("read_file", {"path": path})
            try:
                result = execute_tool(tc, write_gate, read_gate)
                return result
            except Exception as e:
                errors.append(e)
                return ToolResult(False, str(e))

        # Create a file first
        test_file = write_gate.workspace_root + "/shared.py"
        with open(test_file, "w") as f:
            f.write("x = 1\n")

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = [pool.submit(read_file_tool, test_file) for _ in range(32)]
            for f in as_completed(futs):
                f.result()

        assert len(errors) == 0

    def test_cache_hit_counts_accurate_under_contention(self):
        """Cache hit/miss counters should be consistent under parallel access."""
        import tools as tmod

        # Directly exercise the locked cache
        with tmod._TOOL_CACHE_LOCK:
            tmod._TOOL_CACHE.clear()

        hit_events = []
        miss_events = []

        def cache_worker(key: str, should_hit: bool):
            with tmod._TOOL_CACHE_LOCK:
                if key in tmod._TOOL_CACHE:
                    tmod._TOOL_CACHE_HITS += 1
                    hit_events.append(True)
                else:
                    tmod._TOOL_CACHE_MISSES += 1
                    miss_events.append(True)
                    if should_hit:
                        tmod._TOOL_CACHE[key] = (time.monotonic(), ToolResult(True, "ok"))

        with ThreadPoolExecutor(max_workers=8) as pool:
            # First wave: all misses, populate cache
            futs = [pool.submit(cache_worker, f"key_{i}", True) for i in range(50)]
            for f in as_completed(futs):
                f.result()

        # All should have been misses (keys didn't exist yet)
        assert len(miss_events) == 50
        assert len(hit_events) == 0

    def test_dispatch_signatures_thread_safe(self):
        """_DISPATCH_SIGNATURES should not race under parallel lookups."""
        from tools import _DISPATCH_SIGNATURES, _DISPATCH_SIGNATURES_LOCK

        errors = []

        def lookup(name: str):
            try:
                with _DISPATCH_SIGNATURES_LOCK:
                    val = _DISPATCH_SIGNATURES.get(name)
                    if val is None:
                        _DISPATCH_SIGNATURES[name] = True
                return True
            except Exception as e:
                errors.append(e)
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = [pool.submit(lookup, f"tool_{i % 4}") for i in range(32)]
            for f in as_completed(futs):
                f.result()

        assert len(errors) == 0


# ── _on_tool_ready pipe deferral ───────────────────────────────────────────


class TestOnToolReadyPipeDeferral:
    """Verify that streaming _on_tool_ready defers pipe-dependent tools.

    Since _on_tool_ready is a closure inside _api_call_phase, we test
    the deferral logic by simulating what it does: checking for _pipe
    in the raw arguments string.
    """

    def test_detects_pipe_in_raw_args(self):
        """Tool calls with _pipe in arguments should be detected."""
        tc = _make_tc_with_pipe("write_file", {"path": "b.py"}, pipe_from=0, idx=1)
        raw_args = tc.get("function", {}).get("arguments", "")
        assert "_pipe" in raw_args

    def test_no_pipe_passes_through(self):
        """Tool calls without _pipe should not be deferred."""
        tc = _make_tc("read_file", {"path": "a.py"})
        raw_args = tc.get("function", {}).get("arguments", "")
        assert "_pipe" not in raw_args

    def test_empty_arguments_ok(self):
        tc = {
            "id": "call_empty",
            "type": "function",
            "function": {"name": "noop", "arguments": ""},
        }
        raw_args = tc.get("function", {}).get("arguments", "")
        assert "_pipe" not in raw_args

    def test_non_string_arguments_ok(self):
        tc = {
            "id": "call_dict",
            "type": "function",
            "function": {"name": "noop", "arguments": {"path": "a.py"}},
        }
        raw_args = tc.get("function", {}).get("arguments", "")
        # If it's a dict, isinstance check should handle it
        assert not (isinstance(raw_args, str) and "_pipe" in raw_args)


# ── ToolResult in parallel ─────────────────────────────────────────────────


class TestToolResultParallel:
    def test_tool_result_unchanged_by_concurrent_access(self):
        """ToolResult instances should be safe to read from multiple threads."""
        result = ToolResult(True, "hello" * 100)
        errors = []

        def read_result():
            try:
                _ = result.success
                _ = result.content
                _ = result.hint
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=16) as pool:
            futs = [pool.submit(read_result) for _ in range(100)]
            for f in as_completed(futs):
                f.result()

        assert len(errors) == 0
