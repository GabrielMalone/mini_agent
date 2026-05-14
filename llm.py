#!/usr/bin/env python3
"""
llm.py — DeepSeek API communication for mini_agent.

Provides ``call_deepseek()`` for non-streaming and streaming API
requests, ``run_agent_turn()`` orchestrator, circuit breaker,
tool piping (Kahn's algorithm), and turn-summary persistence.
Retry logic lives in ``retry.py``; SSE parsing in ``stream.py``.
"""

from __future__ import annotations

import json
import os
import subprocess as _sp
import sys
import threading
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import requests

from retry import _request_with_retry
from stream import _parse_stream, THINKING_START, THINKING_END

from config import AgentConfig
from terminal import c, _DIM
from tools import TOOLS, execute_tool, tool_summary, clear_tool_cache, _TOOL_CONTEXT, _MODIFIED_FILES
from memory import _total_tokens
from safety import ReadSafetyGate, WriteSafetyGate
from interject import poll_interjections


# ---------------------------------------------------------------------------
# Shared truncation / utility functions (reusable by sub_agent.py)
# ---------------------------------------------------------------------------

def truncate_content(content: str, max_len: int = 300) -> str:
    """Truncate a string to *max_len* chars, appending '…' if truncated."""
    if len(content) <= max_len:
        return content
    return content[:max_len] + "…"


def format_tool_detail(result: "ToolResult", max_len: int = 300) -> str:
    """Format a ToolResult's content for display, truncated to *max_len*."""
    from tools import ToolResult as TR
    detail = result.content[:max_len]
    if len(result.content) > max_len:
        detail += "…"
    return detail


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

# Incremental message cleaning cache: keyed by id(messages), stores a tuple
# of (last_cleaned_len, clean_messages) so repeated calls within a turn
# only clean newly appended messages rather than the entire list.
_clean_messages_cache: dict[int, tuple[int, list[dict]]] = {}


def _clean_message(msg: dict, index: int) -> dict:
    """Clean a single message dict for sending to the API.

    Strips internal tracking fields (keys starting with '_'), removes the
    ``index`` field from tool_calls, and marks the first system message
    with ``cache_control`` for prompt caching.
    """
    m2 = {k: v for k, v in msg.items()
          if not k.startswith("_")}
    if "tool_calls" in m2:
        m2["tool_calls"] = [
            {k: v for k, v in tc.items() if k != "index"}
            for tc in m2["tool_calls"]
        ]
    if index == 0 and m2.get("role") == "system":
        m2["cache_control"] = {"type": "ephemeral"}
    return m2


def call_deepseek(
    messages: list[dict],
    config: AgentConfig,
    on_token: Callable[[str], Any] | None = None,
    session: requests.Session | None = None,
    on_tool_ready: Callable[[dict], Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict | None:
    """Send messages to DeepSeek, return the assistant message dict.

    DeepSeek thinking mode requires ``reasoning_content`` to be passed back
    on subsequent requests. The ``index`` field inside tool_calls must be
    stripped (it is an output-only artefact).

    Returns a message dict with ``content`` and optionally ``tool_calls``.
    When *stream* is True, text content is printed chunk-by-chunk as it
    arrives and tool_calls are accumulated from the stream (single-pass).

    Automatically retries on transient failures (429, 5xx) up to 3 times
    with exponential backoff.  If *session* is provided it is used for
    connection reuse across calls within a turn.
    """
    if session is None:
        session = requests  # use module-level .post (testable via mock)

    # Incremental cleaning: only clean messages appended since last call.
    # This avoids O(n) deep-copy of the entire message list on every API call.
    list_id = id(messages)
    cached_len, clean_messages = _clean_messages_cache.get(list_id, (0, []))
    current_len = len(messages)
    if list_id in _clean_messages_cache and cached_len >= current_len:
        # Same list, no new messages — reuse cache as-is
        pass
    else:
        # Clean any new messages beyond the cached length
        for i in range(cached_len, current_len):
            clean_messages.append(_clean_message(messages[i], i))
        _clean_messages_cache[list_id] = (current_len, clean_messages)

    r = _request_with_retry(
        session,
        config.api_url,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "messages": clean_messages,
            "tools": TOOLS,
            "stream": config.stream,
        },
        stream=config.stream,
        cancel_event=cancel_event,
    )

    if r is None:
        return None  # cancelled during retry

    if not r.ok:
        try:
            err = r.json()
        except (ValueError, AttributeError):
            err = r.text
        print(f"\n[API {r.status_code}] {err}", file=sys.stderr, flush=True)
    r.raise_for_status()

    if config.stream:
        return _parse_stream(r, on_token, on_tool_ready)
    else:
        return r.json()["choices"][0]["message"]


# ---------------------------------------------------------------------------
# Circuit breaker — guards against repeated identical tool calls
# ---------------------------------------------------------------------------

_CIRCUIT_WINDOW: int = 6       # lookback window size
_CIRCUIT_THRESHOLD: int = 3    # trip after this many identical calls in the window


def _tool_call_key(tc: dict) -> str:
    """Stable hash key for a tool call: name + normalized args."""
    fn = tc["function"]
    name = fn["name"]
    try:
        args_normalized = json.dumps(
            json.loads(fn["arguments"]), sort_keys=True)
    except (json.JSONDecodeError, TypeError):
        args_normalized = fn["arguments"]
    return f"{name}:{args_normalized}"


def _check_circuit(recent_keys: list[str]) -> str | None:
    """Return a warning message if the circuit is tripped, otherwise None.

    Trips when the same tool call appears *CIRCUIT_THRESHOLD* or more times
    within the last *CIRCUIT_WINDOW* calls.
    """
    if len(recent_keys) < _CIRCUIT_THRESHOLD:
        return None
    counts = Counter(recent_keys)
    for key, count in counts.items():
        if count >= _CIRCUIT_THRESHOLD:
            return (
                f"⚠️ Circuit breaker: you have called '{key}' {count} times "
                f"in the last {len(recent_keys)} tool calls. "
                "The same call keeps being made with identical arguments. "
                "Stop, diagnose why it isn't working, and try a different "
                "approach rather than repeating it."
            )
    return None


# ---------------------------------------------------------------------------
# Shared agent loop — used by both terminal REPL and TUI
# ---------------------------------------------------------------------------

def _save_turn_summary(
    turn: int,
    msg: dict,
    deferred_results: list[tuple[dict, "ToolResult"]],
    messages: list[dict],
) -> None:
    """Save a concise summary of this turn for later recall via recall_turn()."""
    from tools import ToolResult as TR

    parts: list[str] = []
    content = msg.get("content", "")
    if content:
        parts.append(f"Assistant: {content[:200]}")
    tool_calls = msg.get("tool_calls", [])
    if tool_calls:
        for tc in tool_calls:
            fn = tc.get("function", {})
            parts.append(f"  Tool: {fn.get('name', '?')}({str(fn.get('arguments', ''))[:100]})")
    for tc, result in deferred_results:
        ok = "✓" if result.success else "✗"
        summary = result.content[:150].replace("\n", " ")
        if len(result.content) > 150:
            summary += "…"
        parts.append(f"  Result: {ok} {summary}")
    _TOOL_CONTEXT._turn_history[turn] = "\n".join(parts)
    # Cap to last 200 entries to prevent unbounded memory growth
    if not hasattr(_TOOL_CONTEXT, '_min_turn'):
        _TOOL_CONTEXT._min_turn = 0
    if len(_TOOL_CONTEXT._turn_history) > 200:
        oldest = _TOOL_CONTEXT._min_turn
        while oldest not in _TOOL_CONTEXT._turn_history:
            oldest += 1
        del _TOOL_CONTEXT._turn_history[oldest]
        _TOOL_CONTEXT._min_turn = oldest + 1


# Module-level flags for one-time context injections
_scratchpad_injected: bool = False
_git_diff_injected: bool = False


def _inject_context(
    messages: list[dict],
    *,
    turn_count: int,
    memory_store: Any = None,
    read_gate: ReadSafetyGate | None = None,
    recent_tool_keys: list[str] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Inject all context messages for the current turn.

    Handles:
      - One-time: scratchpad, git diff (first turn), orchestration context
      - Every turn: user interjections, token budget, progress reminders,
        modified-files checkpoint, circuit breaker, scratchpad nudge,
        active plan status.
    """
    global _scratchpad_injected, _git_diff_injected

    # --- one-time: scratchpad context ---
    if not _scratchpad_injected and memory_store is not None:
        _scratchpad_injected = True
        scratchpad = memory_store.get_scratchpad()
        if scratchpad.strip():
            messages.append({
                "role": "user",
                "content": (
                    "Your scratchpad (current working notes — use write_scratchpad "
                    "to update):\n\n" + scratchpad
                ),
                "_transient": True,
            })

    # --- one-time: git diff ---
    if not _git_diff_injected and memory_store is not None and read_gate is not None:
        _git_diff_injected = True
        try:
            result = _sp.run(
                ["git", "diff", "--stat", "HEAD~1"],
                capture_output=True, text=True, timeout=5,
                cwd=read_gate.workspace_root,
            )
            if result.stdout.strip():
                messages.append({
                    "role": "user",
                    "content": (
                        "Recent git changes since last commit:\n\n"
                        + result.stdout.strip()
                        + "\n\nFocus on these files first when making changes."
                    ),
                    "_transient": True,
                })
        except Exception as exc:
            print(f"  ⚠ git diff failed: {exc}", file=sys.stderr, flush=True)

    # --- one-time: sub-agent orchestration context ---
    try:
        runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        if runtime is not None:
            running_ids = runtime.get_running_ids()
            pending = runtime.get_pending_results()
            if running_ids or pending:
                parts: list[str] = []
                if pending:
                    parts.append("Sub-agent(s) COMPLETED since your last turn:")
                    for tid, result in pending:
                        status = "OK" if result.success else "FAILED"
                        parts.append(
                            f"  - {tid}: [{status}] {result.content[:120]}"
                            f"{'...' if len(result.content) > 120 else ''}"
                        )
                    parts.append("")
                if running_ids:
                    parts.append(
                        f"{len(running_ids)} sub-agent(s) still RUNNING: "
                        f"{', '.join(running_ids)}"
                    )
                    parts.append(
                        "Use agent_status() to check each or collect_any() to grab "
                        "the first result. Do NOT redo their work."
                    )
                if parts:
                    messages.append({
                        "role": "user",
                        "content": "\n".join(parts),
                        "_transient": True,
                    })
    except Exception as exc:
        print(f"  ⚠ orchestration context failed: {exc}", file=sys.stderr, flush=True)

    # --- per-turn: poll user interjections ---
    interjections = poll_interjections()
    for msg_text in interjections:
        messages.append({
            "role": "user",
            "content": "[User interjection while you were working] " + msg_text,
        })

    # --- per-turn: token budget awareness ---
    from memory import _inject_token_budget
    _inject_token_budget(messages, turn_count)

    # --- per-turn: periodic progress check ---
    PROGRESS_INTERVAL = 5
    if turn_count > 1 and turn_count % PROGRESS_INTERVAL == 0:
        reminder = (
            f"You have been working for {turn_count} turns. "
            "Briefly assess your progress: are you making headway, "
            "stuck in a loop, or done? If you can wrap up now, "
            "give the final answer. If you truly need more turns, "
            "continue — but be specific about what remains."
        )
        messages.append({"role": "user", "content": reminder, "_transient": True})

    # --- per-turn: modified-files checkpoint (turn 2 only) ---
    if turn_count == 2 and hasattr(read_gate, "workspace_root") if read_gate else False:
        if _MODIFIED_FILES:
            mod_list = "\n".join(f"  - {f}" for f in sorted(_MODIFIED_FILES))
            test_hint = ""
            for mf in _MODIFIED_FILES:
                base = os.path.basename(mf)
                if base.startswith("test_") and base.endswith(".py"):
                    test_hint += f"\n  Relevant test: {base}"
                elif base.endswith(".py") and not base.startswith("test_"):
                    candidate = f"test_{base}"
                    dp = os.path.dirname(mf)
                    test_path = os.path.join(dp, candidate) if dp else candidate
                    if os.path.isfile(os.path.join(read_gate.workspace_root, test_path)):
                        test_hint += f"\n  Relevant test: {test_path}"
            ckpt = (
                f"Files modified this session:\n{mod_list}\n"
                f"Running `verify` or `run_tests`{test_hint if test_hint else ''} "
                f"after changes is recommended."
            )
            messages.append({"role": "user", "content": ckpt, "_transient": True})

    # --- per-turn: circuit breaker check ---
    if recent_tool_keys is not None:
        warning = _check_circuit(recent_tool_keys)
        if warning:
            messages.append({"role": "user", "content": warning, "_transient": True})

    # --- per-turn: scratchpad staleness nudge ---
    if turn_count > 4 and (turn_count - 1) % 3 == 0:
        if not _TOOL_CONTEXT._scratchpad_updated:
            messages.append({
                "role": "user",
                "content": (
                    "⚠️ Your scratchpad hasn't been updated in several turns. "
                    "Consider using write_scratchpad to capture your current "
                    "plan, progress, and decisions before continuing."
                ),
                "_transient": True,
            })
        _TOOL_CONTEXT._scratchpad_updated = False

    # --- per-turn: active plan status ---
    plan_steps = _TOOL_CONTEXT._plan_steps
    if plan_steps:
        plan_done = _TOOL_CONTEXT._plan_done
        lines = [f"Active plan ({len(plan_done)}/{len(plan_steps)} done):"]
        for i, s in enumerate(plan_steps, 1):
            mark = "✓" if (i - 1) in plan_done else "○"
            lines.append(f"  [{mark}] {i}. {s}")
        lines.append("Use plan_status to mark steps complete as you finish them.")
        messages.append({
            "role": "user",
            "content": "\n".join(lines),
            "_transient": True,
        })


def _execute_tools(
    remaining: list[dict],
    messages: list[dict],
    write_gate: WriteSafetyGate,
    read_gate: ReadSafetyGate,
    *,
    on_tool_start: Callable[..., Any] | None = None,
    on_tool_end: Callable[..., Any] | None = None,
    on_tool_output: Callable[..., Any] | None = None,
    approve_callback: Callable[..., Any] | None = None,
    cancel_event: threading.Event | None = None,
    recent_tool_keys: list[str] | None = None,
    tool_keys_lock: threading.Lock | None = None,
) -> list[tuple[dict, "ToolResult"]]:
    """Execute a list of tool calls, respecting _pipe dependencies.

    Uses Kahn's algorithm for topological sort when _pipe deps are present.
    Independent tools run in parallel via ThreadPoolExecutor.
    Returns a list of (tool_call_dict, ToolResult) tuples.
    """
    from tools import ToolResult as TR
    import json as _json

    # --- Tool piping: extract _pipe deps from remaining tools ---
    pipe_deps: dict[int, tuple[int, str]] = {}  # target_idx -> (source_idx, param)
    pipe_results: dict[int, "ToolResult"] = {}   # idx -> result (for substitution)
    for i, tc in enumerate(remaining):
        raw = tc["function"].get("arguments", "{}")
        try:
            ad = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            continue
        pipe_cfg = ad.pop("_pipe", None)
        if isinstance(pipe_cfg, dict) and "from" in pipe_cfg:
            pipe_deps[i] = (int(pipe_cfg["from"]), pipe_cfg.get("into", ""))
        tc["function"]["arguments"] = _json.dumps(ad)

    def _apply_pipe(tc: dict, i: int,
                    pipe_deps: dict, pipe_results: dict, _json: Any) -> None:
        """Substitute piped result into tc's arguments in-place."""
        if i not in pipe_deps:
            return
        src_idx, into_param = pipe_deps[i]
        src_result = pipe_results.get(src_idx)
        if src_result is None:
            return
        args_dict = _json.loads(tc["function"]["arguments"])
        if not into_param:
            for k, v in args_dict.items():
                if isinstance(v, str):
                    into_param = k
                    break
        if into_param and into_param in args_dict:
            args_dict[into_param] = src_result.content.strip()
            tc["function"]["arguments"] = _json.dumps(args_dict)

    # No piping — simple parallel or sequential execution
    if not pipe_deps:
        if len(remaining) == 1:
            tc = remaining[0]
            if cancel_event is not None and cancel_event.is_set():
                return []
            if on_tool_start is not None:
                on_tool_start(tool_summary(tc))
            result = execute_tool(tc, write_gate, read_gate,
                                  on_output=on_tool_output,
                                  approve_callback=approve_callback)
            _append_tool_result(messages, tc, result, on_tool_end,
                                recent_keys=recent_tool_keys,
                                lock=tool_keys_lock)
            return [(tc, result)]

        if on_tool_start is not None:
            for tc in remaining:
                on_tool_start(tool_summary(tc), True)

        def _run_tool(tc: dict) -> tuple[dict, "ToolResult"]:
            return tc, execute_tool(tc, write_gate, read_gate,
                                    on_output=on_tool_output,
                                    approve_callback=approve_callback)

        parallel_results: list[tuple] = []
        with ThreadPoolExecutor(max_workers=len(remaining)) as pool:
            futures = {pool.submit(_run_tool, tc): tc for tc in remaining}
            for future in as_completed(futures):
                if cancel_event is not None and cancel_event.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    return parallel_results
                tc, result = future.result()
                _append_tool_result(messages, tc, result, on_tool_end,
                                    recent_keys=recent_tool_keys,
                                    lock=tool_keys_lock)
                parallel_results.append((tc, result))
        return parallel_results

    # --- Piping: topological sort into execution groups ---
    import collections
    children: dict[int, list[int]] = {i: [] for i in range(len(remaining))}
    indeg: dict[int, int] = {i: 0 for i in range(len(remaining))}
    for tgt, (src, _) in pipe_deps.items():
        children.setdefault(src, []).append(tgt)
        indeg[tgt] = indeg.get(tgt, 0) + 1

    queue = collections.deque([i for i in range(len(remaining)) if indeg[i] == 0])
    groups: list[list[int]] = []
    seen = 0
    while queue:
        group = list(queue)
        groups.append(group)
        queue.clear()
        for node in group:
            seen += 1
            for child in children.get(node, []):
                indeg[child] -= 1
                if indeg[child] == 0:
                    queue.append(child)

    if seen != len(remaining):
        # Cycle detected — fall back to sequential execution
        if on_tool_start is not None:
            for tc in remaining:
                on_tool_start(tool_summary(tc))
        for i, tc in enumerate(remaining):
            if cancel_event is not None and cancel_event.is_set():
                break
            result = execute_tool(tc, write_gate, read_gate,
                                  on_output=on_tool_output,
                                  approve_callback=approve_callback)
            pipe_results[i] = result
            _append_tool_result(messages, tc, result, on_tool_end,
                                recent_keys=recent_tool_keys)
        return []

    # Execute groups in order (parallel within group, sequential across groups)
    all_results: list[tuple] = []
    for group in groups:
        if on_tool_start is not None:
            for i in group:
                on_tool_start(tool_summary(remaining[i]),
                              parallel=len(group) > 1)

        if len(group) == 1:
            i = group[0]
            tc = remaining[i]
            _apply_pipe(tc, i, pipe_deps, pipe_results, _json)
            if cancel_event is not None and cancel_event.is_set():
                break
            result = execute_tool(tc, write_gate, read_gate,
                                  on_output=on_tool_output,
                                  approve_callback=approve_callback)
            pipe_results[i] = result
            _append_tool_result(messages, tc, result, on_tool_end,
                                recent_keys=recent_tool_keys)
            all_results.append((tc, result))
        else:
            results_lock = threading.Lock()

            def _run_piped(i: int) -> tuple[int, dict, "ToolResult"]:
                tc = remaining[i]
                _apply_pipe(tc, i, pipe_deps, pipe_results, _json)
                return i, tc, execute_tool(tc, write_gate, read_gate,
                                           on_output=on_tool_output,
                                           approve_callback=approve_callback)

            with ThreadPoolExecutor(max_workers=len(group)) as pool:
                futures = {pool.submit(_run_piped, i): i for i in group}
                for future in as_completed(futures):
                    if cancel_event is not None and cancel_event.is_set():
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    i, tc, result = future.result()
                    with results_lock:
                        pipe_results[i] = result
                    _append_tool_result(messages, tc, result, on_tool_end,
                                        recent_keys=recent_tool_keys)
                    all_results.append((tc, result))

    return all_results


def run_agent_turn(
    messages: list[dict],
    config: AgentConfig,
    write_gate: WriteSafetyGate,
    read_gate: ReadSafetyGate,
    *,
    on_token: Callable[[str], Any] | None = None,
    on_tool_start: Callable[..., Any] | None = None,
    on_tool_end: Callable[..., Any] | None = None,
    on_tool_output: Callable[..., Any] | None = None,
    approve_callback: Callable[..., Any] | None = None,
    cancel_event: threading.Event | None = None,
    max_turns: int = 100,
    session: requests.Session | None = None,
    memory_store: Any = None,
) -> dict | None:
    """Run one full agent turn — possibly multiple API calls if tools are used.

    Calls the LLM, executes any tool calls, feeds results back, and repeats
    until the model returns a plain text response or the turn is cancelled.

    *messages* is mutated in place: assistant and tool messages are appended.
    Returns the final assistant message dict, or ``None`` if cancelled.
    *max_turns* is a hard safety cap (default 100).

    If *memory_store* is provided, the scratchpad is read from it and
    injected as context at the start of the turn.

    Multiple independent tool calls are executed in parallel via a thread pool.
    If *session* is a requests.Session, it is reused across API calls for
    connection reuse. If None, the requests module is used (test-friendly).

    Every 5 tool-using turns, a system reminder is injected to keep the agent
    on track and let it decide whether to continue or wrap up.
    """
    global _scratchpad_injected, _git_diff_injected
    _scratchpad_injected = False
    _git_diff_injected = False

    # Clear the incremental message-cleaning cache so it doesn't grow unbounded
    _clean_messages_cache.clear()

    total_usage: dict[str, int] = {}
    turn_count: int = 0
    recent_tool_keys: list[str] = []  # circuit breaker tracking
    tool_keys_lock: threading.Lock = threading.Lock()

    _original_session = session  # track whether we own the session for cleanup
    if session is None:
        session = requests  # test-friendly: mockable via patch("llm.requests.post")
    clear_tool_cache()

    try:
        for _ in range(max_turns):
            turn_count += 1
            if cancel_event is not None and cancel_event.is_set():
                return None

            # --- inject all context for this turn ---
            _inject_context(
                messages,
                turn_count=turn_count,
                memory_store=memory_store,
                read_gate=read_gate,
                recent_tool_keys=recent_tool_keys,
                cancel_event=cancel_event,
            )

            # Collect tools executed incrementally during the stream.
            executed_tool_indices: set[int] = set()
            deferred_stream_results: list[tuple] = []  # (tc, result)

            def _on_tool_ready(tc: dict) -> None:
                """Execute a tool immediately when its args form valid JSON."""
                idx = tc.pop("_index", -1)
                if idx in executed_tool_indices:
                    return
                executed_tool_indices.add(idx)
                if cancel_event is not None and cancel_event.is_set():
                    return
                if on_tool_start is not None:
                    on_tool_start(tool_summary(tc))
                result = execute_tool(tc, write_gate, read_gate,
                                      on_output=on_tool_output,
                                      approve_callback=approve_callback)
                deferred_stream_results.append((tc, result))
                detail = format_tool_detail(result, max_len=300)
                if on_tool_end is not None:
                    on_tool_end(result.success, detail)

            msg = call_deepseek(messages, config, on_token=on_token,
                                session=session, on_tool_ready=_on_tool_ready,
                                cancel_event=cancel_event)

            if cancel_event is not None and cancel_event.is_set():
                return None

            # Strip internal tracking fields
            fired_indices = set(msg.pop("_fired_indices", []))
            executed_tool_indices |= fired_indices

            # Accumulate token usage across all API calls in this turn
            if "_usage" in msg:
                total_usage = {
                    "prompt_tokens": total_usage.get("prompt_tokens", 0)
                        + msg["_usage"].get("prompt_tokens", 0),
                    "completion_tokens": total_usage.get("completion_tokens", 0)
                        + msg["_usage"].get("completion_tokens", 0),
                    "total_tokens": total_usage.get("total_tokens", 0)
                        + msg["_usage"].get("total_tokens", 0),
                }

            if not msg.get("tool_calls"):
                if total_usage:
                    msg["_total_usage"] = total_usage
                if turn_count > 1:
                    msg["_turn_count"] = turn_count
                messages.append(msg)
                _save_turn_summary(turn_count, msg, [], messages)
                return msg

            raw_tool_calls = msg["tool_calls"]
            remaining = [
                tc for i, tc in enumerate(raw_tool_calls)
                if i not in executed_tool_indices
            ]

            if not remaining:
                messages.append(msg)
                for tc, result in deferred_stream_results:
                    _append_tool_result(messages, tc, result, on_tool_end,
                                        recent_keys=recent_tool_keys,
                                        lock=tool_keys_lock)
                _save_turn_summary(turn_count, msg, deferred_stream_results, messages)
                continue

            # Keep all tool_calls so deferred results have a reference
            msg["tool_calls"] = raw_tool_calls
            messages.append(msg)
            # Flush deferred tool results from streaming execution
            for tc, result in deferred_stream_results:
                _append_tool_result(messages, tc, result, on_tool_end,
                                    recent_keys=recent_tool_keys,
                                    lock=tool_keys_lock)

            # Execute remaining tools with piping support
            tool_results = _execute_tools(
                remaining,
                messages,
                write_gate,
                read_gate,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
                on_tool_output=on_tool_output,
                approve_callback=approve_callback,
                cancel_event=cancel_event,
                recent_tool_keys=recent_tool_keys,
                tool_keys_lock=tool_keys_lock,
            )
            _save_turn_summary(turn_count, msg, tool_results, messages)

        # Exceeded max_turns — return last assistant message (still has tool_calls)
        if 'msg' not in locals():
            return None  # max_turns was 0, no API call made
        if total_usage:
            msg["_total_usage"] = total_usage
        if turn_count > 1:
            msg["_turn_count"] = turn_count
        return msg
    finally:
        # Only close the session if we created it; caller-managed sessions
        # (passed via the session parameter) are the caller's responsibility.
        if session is not _original_session and hasattr(session, "close"):
            session.close()


def _append_tool_result(
    messages: list[dict],
    tc: dict,
    result: "ToolResult",
    on_tool_end: Callable[..., Any] | None = None,
    recent_keys: list[str] | None = None,
    lock: threading.Lock | None = None,
) -> None:
    """Append a tool result message and fire the on_tool_end callback."""
    from tools import ToolResult as TR
    detail = format_tool_detail(result, max_len=300)
    if on_tool_end is not None:
        on_tool_end(result.success, detail)
    messages.append({
        "role": "tool",
        "tool_call_id": tc["id"],
        "content": result.to_json(),
    })
    # Track for circuit breaker
    if recent_keys is not None:
        if lock is not None:
            with lock:
                recent_keys.append(_tool_call_key(tc))
                while len(recent_keys) > _CIRCUIT_WINDOW:
                    recent_keys.pop(0)
        else:
            recent_keys.append(_tool_call_key(tc))
            while len(recent_keys) > _CIRCUIT_WINDOW:
                recent_keys.pop(0)
