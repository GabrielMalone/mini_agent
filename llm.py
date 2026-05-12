#!/usr/bin/env python3
"""
llm.py — DeepSeek API communication for mini_agent.

Provides ``call_deepseek()`` for non-streaming and streaming API
requests, ``run_agent_turn()`` orchestrator, circuit breaker,
tool piping (Kahns algorithm), and turn-summary persistence.
Retry logic lives in ``retry.py``; SSE parsing in ``stream.py``.
requests with automatic retry on transient failures, and ``_parse_stream()``
for SSE parsing with tool-call accumulation and connection-drop resilience.
"""

import json
import os
import subprocess as _sp
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from retry import _request_with_retry
from stream import _parse_stream, THINKING_START, THINKING_END

from config import AgentConfig
from terminal import c, _DIM
from tools import TOOLS, execute_tool, tool_summary, clear_tool_cache
from safety import ReadSafetyGate, WriteSafetyGate

# Thinking-mode delimiters sent through the on_token stream
# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def call_deepseek(
    messages: list[dict],
    config: AgentConfig,
    on_token: callable = None,
    session: requests.Session | None = None,
    on_tool_ready: callable = None,
    cancel_event: threading.Event | None = None,
) -> dict:
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

    clean_messages = []
    for i, m in enumerate(messages):
        m2 = {k: v for k, v in m.items()
              if not k.startswith("_")}  # strip internal tracking fields
        if "tool_calls" in m2:
            m2["tool_calls"] = [
                {k: v for k, v in tc.items() if k != "index"}
                for tc in m2["tool_calls"]
            ]
        # Prompt caching: mark the first system message for server-side caching.
        # DeepSeek reuses the cached prefix on subsequent calls within a session.
        if i == 0 and m2.get("role") == "system":
            m2["cache_control"] = {"type": "ephemeral"}
        clean_messages.append(m2)

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
        except Exception:
            err = r.text
        print(f"\n[API {r.status_code}] {err}", file=sys.stderr, flush=True)
    r.raise_for_status()

    if config.stream:
        return _parse_stream(r, on_token, on_tool_ready)
    else:
        return r.json()["choices"][0]["message"]


# Circuit breaker — guards against repeated identical tool calls
# ---------------------------------------------------------------------------

_CIRCUIT_WINDOW = 6       # lookback window size
_CIRCUIT_THRESHOLD = 3    # trip after this many identical calls in the window


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
    from collections import Counter
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
    deferred_results: list,
    messages: list[dict],
) -> None:
    """Save a concise summary of this turn for later recall via recall_turn()."""
    from tools import _TOOL_CONTEXT

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
        parts.append(f"  Result: {ok} {summary}")
    _TOOL_CONTEXT._turn_history[turn] = "\n".join(parts)


def run_agent_turn(
    messages: list[dict],
    config: AgentConfig,
    write_gate: WriteSafetyGate,
    read_gate: ReadSafetyGate,
    *,
    on_token: callable = None,
    on_tool_start: callable = None,
    on_tool_end: callable = None,
    on_tool_output: callable = None,
    approve_callback: callable = None,
    cancel_event: threading.Event = None,
    max_turns: int = 100,
    session=None,
    memory_store=None,
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
    PROGRESS_INTERVAL = 5

    # --- inject scratchpad context ---
    if memory_store is not None:
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

    total_usage: dict[str, int] = {}
    turn_count = 0
    recent_tool_keys: list[str] = []  # circuit breaker tracking

    # --- inject recent git changes (first turn only) ---
    if turn_count == 0 and memory_store is not None:
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
        except Exception:
            pass
    if session is None:
        session = requests  # test-friendly: mockable via patch("llm.requests.post")
    clear_tool_cache()
    try:
        for _ in range(max_turns):
            turn_count += 1
            if cancel_event is not None and cancel_event.is_set():
                return None

            # Token budget awareness — inject context usage at turn start
            if turn_count > 1:
                from memory import _total_tokens
                estimate = _total_tokens(messages)
                # Rough max: model's context window minus headroom
                budget = 64000
                pct = min(100, estimate * 100 // budget)
                messages.append({
                    "role": "user",
                    "content": (
                        f"[Context: ~{estimate}//{budget} tokens ({pct}%). "
                        f"Be concise if nearing limit.]"
                    ),
                    "_transient": True,
                })

            # Periodic progress check — inject a system reminder
            if turn_count > 1 and turn_count % PROGRESS_INTERVAL == 0:
                reminder = (
                    f"You have been working for {turn_count} turns. "
                    "Briefly assess your progress: are you making headway, "
                    "stuck in a loop, or done? If you can wrap up now, "
                    "give the final answer. If you truly need more turns, "
                    "continue — but be specific about what remains."
                )
                messages.append({"role": "user", "content": reminder, "_transient": True})

            # Pre-turn checkpoint — remind agent of files modified and tests to run
            if turn_count == 2 and hasattr(read_gate, "workspace_root"):
                from tools import _MODIFIED_FILES
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

            # Circuit breaker check — inject warning if identical tool calls repeat
            warning = _check_circuit(recent_tool_keys)
            if warning:
                messages.append({"role": "user", "content": warning, "_transient": True})

            # Scratchpad staleness nudge — warn if not updated in several turns
            from tools import _TOOL_CONTEXT
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

            # Inject active plan status at turn start
            from tools import _TOOL_CONTEXT
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

            # Collect tools executed incrementally during the stream.
            # Tool results MUST be appended AFTER the assistant message (which
            # contains tool_calls), not before — otherwise DeepSeek returns 400
            # ("Messages with role 'tool' must be a response to a preceding
            # message with 'tool_calls'").
            executed_tool_indices: set[int] = set()
            deferred_stream_results: list[tuple] = []  # (tc, result)

            def _on_tool_ready(tc: dict) -> None:
                """Execute a tool immediately when its args form valid JSON."""
                # The parser tags each fired tool with its index
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
                # Defer message append — assistant msg with tool_calls must
                # be inserted first to satisfy API message ordering rules.
                deferred_stream_results.append((tc, result))
                # Still fire the end callback immediately for UI updates
                detail = result.content[:300]
                if len(result.content) > 300:
                    detail += "…"
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

            # Keep ALL tool_calls on the assistant message for valid API
            # ordering.  Deferred results are flushed right after this, and
            # remaining tools execute next, so every tool_call gets a result.
            raw_tool_calls = msg["tool_calls"]
            remaining = [
                tc for i, tc in enumerate(raw_tool_calls)
                if i not in executed_tool_indices
            ]
            # Do NOT filter tool_calls on the assistant — keep them all intact
            # so deferred tool results have a preceding tool_calls reference.
            # Flush deferred tool results after the assistant message
            # (which keeps ALL tool_calls for valid API ordering).
            if not remaining:
                messages.append(msg)
                for tc, result in deferred_stream_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result.to_json(),
                    })
                    recent_tool_keys.append(_tool_call_key(tc))
                    while len(recent_tool_keys) > _CIRCUIT_WINDOW:
                        recent_tool_keys.pop(0)
                _save_turn_summary(turn_count, msg, deferred_stream_results, messages)
                continue

            # Keep all tool_calls so deferred results have a reference
            msg["tool_calls"] = raw_tool_calls
            messages.append(msg)
            # Flush deferred tool results from streaming execution
            for tc, result in deferred_stream_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result.to_json(),
                })
                recent_tool_keys.append(_tool_call_key(tc))
                while len(recent_tool_keys) > _CIRCUIT_WINDOW:
                    recent_tool_keys.pop(0)

            # --- Tool piping: extract _pipe deps from remaining tools ---
            # _pipe format: {"from": <source_index_in_remaining>, "into": "<param_name>"}
            # Source index is relative to `remaining` list.
            import json as _json
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

            if not pipe_deps:
                # No piping — original behavior
                if len(remaining) == 1:
                    tc = remaining[0]
                    if cancel_event is not None and cancel_event.is_set():
                        return None
                    if on_tool_start is not None:
                        on_tool_start(tool_summary(tc))
                    result = execute_tool(tc, write_gate, read_gate,
                                          on_output=on_tool_output,
                                          approve_callback=approve_callback)
                    _append_tool_result(messages, tc, result, on_tool_end,
                                        recent_keys=recent_tool_keys)
                    _save_turn_summary(turn_count, msg, [(tc, result)], messages)
                    continue

                if on_tool_start is not None:
                    for tc in remaining:
                        on_tool_start(tool_summary(tc), True)

                def _run_tool(tc):
                    return tc, execute_tool(tc, write_gate, read_gate,
                                            on_output=on_tool_output,
                                            approve_callback=approve_callback)

                with ThreadPoolExecutor(max_workers=len(remaining)) as pool:
                    futures = {pool.submit(_run_tool, tc): tc for tc in remaining}
                    for future in as_completed(futures):
                        if cancel_event is not None and cancel_event.is_set():
                            pool.shutdown(wait=False, cancel_futures=True)
                            return None
                        tc, result = future.result()
                        _append_tool_result(messages, tc, result, on_tool_end,
                                            recent_keys=recent_tool_keys)

                _save_turn_summary(turn_count, msg, [], messages)
                continue

            # --- Piping: topological sort into execution groups ---
            # Build adjacency: source_idx -> [target indices that depend on it]
            children: dict[int, list[int]] = {i: [] for i in range(len(remaining))}
            indeg: dict[int, int] = {i: 0 for i in range(len(remaining))}
            for tgt, (src, _) in pipe_deps.items():
                children.setdefault(src, []).append(tgt)
                indeg[tgt] = indeg.get(tgt, 0) + 1

            # Kahn's algorithm → ordered groups
            import collections
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
                _save_turn_summary(turn_count, msg, [], messages)
                continue

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
                    # Substitute pipe input if this tool has a dep
                    if i in pipe_deps:
                        src_idx, into_param = pipe_deps[i]
                        src_result = pipe_results.get(src_idx)
                        if src_result is not None:
                            args_dict = _json.loads(tc["function"]["arguments"])
                            if not into_param:
                                # Default: first string parameter
                                for k, v in args_dict.items():
                                    if isinstance(v, str):
                                        into_param = k
                                        break
                            if into_param and into_param in args_dict:
                                args_dict[into_param] = src_result.content.strip()
                                tc["function"]["arguments"] = _json.dumps(args_dict)
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
                    results_lock = __import__("threading").Lock()

                    def _run_piped(i):
                        tc = remaining[i]
                        if i in pipe_deps:
                            src_idx, into_param = pipe_deps[i]
                            src_result = pipe_results.get(src_idx)
                            if src_result is not None:
                                args_dict = _json.loads(tc["function"]["arguments"])
                                if not into_param:
                                    for k, v in args_dict.items():
                                        if isinstance(v, str):
                                            into_param = k
                                            break
                                if into_param and into_param in args_dict:
                                    args_dict[into_param] = src_result.content.strip()
                                    tc["function"]["arguments"] = _json.dumps(args_dict)
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

            _save_turn_summary(turn_count, msg, all_results, messages)

        # Exceeded max_turns — return last assistant message (still has tool_calls)
        if total_usage:
            msg["_total_usage"] = total_usage
        if turn_count > 1:
            msg["_turn_count"] = turn_count
        return msg
    finally:
        if hasattr(session, "close"):
            session.close()


def _append_tool_result(
    messages: list[dict],
    tc: dict,
    result,
    on_tool_end: callable = None,
    recent_keys: list[str] | None = None,
) -> None:
    """Append a tool result message and fire the on_tool_end callback."""
    detail = result.content[:300]
    if len(result.content) > 300:
        detail += "…"
    if on_tool_end is not None:
        on_tool_end(result.success, detail)
    messages.append({
        "role": "tool",
        "tool_call_id": tc["id"],
        "content": result.to_json(),
    })
    # Track for circuit breaker
    if recent_keys is not None:
        recent_keys.append(_tool_call_key(tc))
        while len(recent_keys) > _CIRCUIT_WINDOW:
            recent_keys.pop(0)
