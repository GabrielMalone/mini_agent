#!/usr/bin/env python3
"""
llm.py — DeepSeek API communication for mini_agent.

Provides ``call_deepseek()`` for non-streaming and streaming (single-pass)
requests with automatic retry on transient failures, and ``_parse_stream()``
for SSE parsing with tool-call accumulation and connection-drop resilience.
"""

import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from config import AgentConfig
from terminal import c, _DIM
from tools import TOOLS, execute_tool, tool_summary, clear_tool_cache
from safety import ReadSafetyGate, WriteSafetyGate

# Thinking-mode delimiters sent through the on_token stream
THINKING_START = "\n[thinking] "
THINKING_END = "\n[/thinking]"


# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRYABLE_STATUSES: set[int] = {429, 500, 502, 503, 504}


def _request_with_retry(
    session,  # requests.Session or the requests module itself
    *args,
    stream: bool = False,
    **kwargs,
) -> requests.Response:
    """Send an HTTP request with retry on transient errors.

    Retries up to *_MAX_RETRIES* times with exponential backoff (1s, 2s, 4s)
    on 429 / 5xx status codes.  Non-retryable errors raise immediately.

    *session* is a requests.Session for connection reuse, or the requests
    module itself (for testability — tests patch requests.post).
    """
    post = session.post if hasattr(session, "post") else requests.post
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            r = post(*args, stream=stream, **kwargs)
            if r.ok or r.status_code not in _RETRYABLE_STATUSES:
                return r
            # Transient error — retry
            if attempt < _MAX_RETRIES:
                delay = 2 ** attempt  # 1, 2, 4
                print(
                    f"  ⚠ API {r.status_code}, retrying in {delay}s "
                    f"(attempt {attempt + 1}/{_MAX_RETRIES})",
                    file=sys.stderr, flush=True,
                )
                time.sleep(delay)
            else:
                return r  # exhausted retries, let caller handle
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = 2 ** attempt
                print(
                    f"  ⚠ network error ({exc}), retrying in {delay}s "
                    f"(attempt {attempt + 1}/{_MAX_RETRIES})",
                    file=sys.stderr, flush=True,
                )
                time.sleep(delay)
            else:
                raise  # exhausted retries, re-raise

    if last_exc is not None:
        raise last_exc
    return r  # pragma: no cover


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def call_deepseek(
    messages: list[dict],
    config: AgentConfig,
    on_token: callable = None,
    session: requests.Session | None = None,
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
    for m in messages:
        m2 = dict(m)  # shallow copy so we don't mutate the original
        if "tool_calls" in m2:
            m2["tool_calls"] = [
                {k: v for k, v in tc.items() if k != "index"}
                for tc in m2["tool_calls"]
            ]
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
    )

    if not r.ok:
        try:
            err = r.json()
        except Exception:
            err = r.text
        print(f"\n[API {r.status_code}] {err}", file=sys.stderr, flush=True)
    r.raise_for_status()

    if config.stream:
        return _parse_stream(r, on_token)
    else:
        return r.json()["choices"][0]["message"]


def _parse_stream(response: requests.Response, on_token: callable = None) -> dict:
    """Parse an SSE streamed response, printing text as it arrives.

    Accumulates both text content and tool_calls from deltas.  Tool call
    arguments arrive in fragments across multiple chunks and are reassembled
    by index.  Reasoning (thinking) content is printed dimmed in real-time
    for debugging visibility.

    If *on_token* is provided, it is called with each content token (str)
    instead of printing to stdout.

    If the connection drops mid-stream, whatever was accumulated so far is
    returned rather than crashing — a warning is printed to stderr.

    Returns a reconstructed message dict (role, content, optional
    reasoning_content, optional tool_calls).
    """
    full_content = ""
    full_reasoning = ""
    tool_calls_by_index: dict[int, dict] = {}  # index → accumulated tc dict
    reasoning_header_printed = False
    usage: dict | None = None

    if not on_token:
        print(flush=True)  # separate streaming output from the prompt line

    try:
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[len("data: "):]
            if data_str.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                delta = chunk["choices"][0].get("delta", {})

                # Usage may appear in any chunk (usually the last)
                if "usage" in chunk and chunk["usage"]:
                    usage = chunk["usage"]

                # Text content — print and accumulate
                if "content" in delta and delta["content"]:
                    if full_reasoning and not full_content:
                        # First content token after reasoning — signal end of thinking
                        if on_token:
                            on_token(THINKING_END)
                    full_content += delta["content"]
                    if on_token:
                        on_token(delta["content"])
                    else:
                        print(delta["content"], end="", flush=True)

                # Reasoning content (thinking mode) — forward via on_token or print
                if "reasoning_content" in delta and delta["reasoning_content"]:
                    if not reasoning_header_printed and not full_content:
                        if on_token:
                            on_token(THINKING_START)
                        else:
                            print(c("  thinking…", _DIM), file=sys.stderr, flush=True)
                        reasoning_header_printed = True
                    full_reasoning += delta["reasoning_content"]
                    if on_token:
                        on_token(delta["reasoning_content"])
                    else:
                        print(c(delta["reasoning_content"], _DIM), end="", flush=True)

                # Tool calls — accumulate fragments by index
                if "tool_calls" in delta:
                    for tc_delta in delta["tool_calls"]:
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_calls_by_index:
                            tool_calls_by_index[idx] = {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        tc = tool_calls_by_index[idx]
                        if "id" in tc_delta:
                            tc["id"] = tc_delta["id"]
                        if "type" in tc_delta:
                            tc["type"] = tc_delta["type"]
                        if "function" in tc_delta:
                            fn_delta = tc_delta["function"]
                            if "name" in fn_delta and fn_delta["name"]:
                                tc["function"]["name"] += fn_delta["name"]
                            if "arguments" in fn_delta:
                                tc["function"]["arguments"] += fn_delta["arguments"]
            except Exception:
                continue
    except (
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ConnectionError,
        requests.exceptions.StreamConsumedError,
        ConnectionError,
        OSError,
    ) as exc:
        print(
            f"\n  ⚠ stream interrupted ({exc}) — using partial response",
            file=sys.stderr, flush=True,
        )

    if full_reasoning and not on_token:
        print(file=sys.stderr, flush=True)  # newline after dimmed reasoning block

    if full_content and not on_token:
        print(flush=True)  # final newline after streamed text

    msg: dict = {"role": "assistant", "content": full_content}
    if full_reasoning:
        msg["reasoning_content"] = full_reasoning
    if usage:
        msg["_usage"] = usage

    if tool_calls_by_index:
        msg["tool_calls"] = [
            tool_calls_by_index[i]
            for i in sorted(tool_calls_by_index)
        ]

    return msg


# ---------------------------------------------------------------------------
# Shared agent loop — used by both terminal REPL and TUI
# ---------------------------------------------------------------------------

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
    cancel_event: threading.Event = None,
    max_turns: int = 100,
    session=None,
) -> dict | None:
    """Run one full agent turn — possibly multiple API calls if tools are used.

    Calls the LLM, executes any tool calls, feeds results back, and repeats
    until the model returns a plain text response or the turn is cancelled.

    *messages* is mutated in place: assistant and tool messages are appended.
    Returns the final assistant message dict, or ``None`` if cancelled.
    *max_turns* is a hard safety cap (default 100).

    Multiple independent tool calls are executed in parallel via a thread pool.
    If *session* is a requests.Session, it is reused across API calls for
    connection reuse. If None, the requests module is used (test-friendly).

    Every 5 tool-using turns, a system reminder is injected to keep the agent
    on track and let it decide whether to continue or wrap up.
    """
    PROGRESS_INTERVAL = 5

    total_usage: dict[str, int] = {}
    turn_count = 0
    if session is None:
        session = requests  # test-friendly: mockable via patch("llm.requests.post")
    clear_tool_cache()
    try:
        for _ in range(max_turns):
            turn_count += 1
            if cancel_event is not None and cancel_event.is_set():
                return None

            # Periodic progress check — inject a system reminder
            if turn_count > 1 and turn_count % PROGRESS_INTERVAL == 0:
                reminder = (
                    f"You have been working for {turn_count} turns. "
                    "Briefly assess your progress: are you making headway, "
                    "stuck in a loop, or done? If you can wrap up now, "
                    "give the final answer. If you truly need more turns, "
                    "continue — but be specific about what remains."
                )
                messages.append({"role": "user", "content": reminder})

            msg = call_deepseek(messages, config, on_token=on_token, session=session)

            if cancel_event is not None and cancel_event.is_set():
                return None

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
                return msg

            messages.append(msg)
            tool_calls = msg["tool_calls"]

            # Single tool — run inline (no thread overhead)
            if len(tool_calls) == 1:
                tc = tool_calls[0]
                if cancel_event is not None and cancel_event.is_set():
                    return None
                if on_tool_start is not None:
                    on_tool_start(tool_summary(tc))
                result = execute_tool(tc, write_gate, read_gate, on_output=on_tool_output)
                _append_tool_result(messages, tc, result, on_tool_end)
                continue

            # Multiple tools — run in parallel thread pool
            if on_tool_start is not None:
                for tc in tool_calls:
                    on_tool_start(tool_summary(tc), True)

            def _run_tool(tc):
                return tc, execute_tool(tc, write_gate, read_gate, on_output=on_tool_output)

            with ThreadPoolExecutor(max_workers=len(tool_calls)) as pool:
                futures = {pool.submit(_run_tool, tc): tc for tc in tool_calls}
                for future in as_completed(futures):
                    if cancel_event is not None and cancel_event.is_set():
                        pool.shutdown(wait=False, cancel_futures=True)
                        return None
                    tc, result = future.result()
                    _append_tool_result(messages, tc, result, on_tool_end)

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
