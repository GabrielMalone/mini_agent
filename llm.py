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

import requests

from config import AgentConfig
from terminal import c, _DIM
from tools import TOOLS


# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRYABLE_STATUSES: set[int] = {429, 500, 502, 503, 504}


def _request_with_retry(
    *args,
    stream: bool = False,
    **kwargs,
) -> requests.Response:
    """Send an HTTP request with retry on transient errors.

    Retries up to *_MAX_RETRIES* times with exponential backoff (1s, 2s, 4s)
    on 429 / 5xx status codes.  Non-retryable errors raise immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            r = requests.post(*args, stream=stream, **kwargs)
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

def call_deepseek(messages: list[dict], config: AgentConfig, on_token: callable = None) -> dict:
    """Send messages to DeepSeek, return the assistant message dict.

    DeepSeek thinking mode requires ``reasoning_content`` to be passed back
    on subsequent requests. The ``index`` field inside tool_calls must be
    stripped (it is an output-only artefact).

    Returns a message dict with ``content`` and optionally ``tool_calls``.
    When *stream* is True, text content is printed chunk-by-chunk as it
    arrives and tool_calls are accumulated from the stream (single-pass).

    Automatically retries on transient failures (429, 5xx) up to 3 times
    with exponential backoff."""
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
    reasoning_content, optional tool_calls)."""
    full_content = ""
    full_reasoning = ""
    tool_calls_by_index: dict[int, dict] = {}  # index → accumulated tc dict
    reasoning_header_printed = False

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

                # Text content — print and accumulate
                if "content" in delta and delta["content"]:
                    full_content += delta["content"]
                    if on_token:
                        on_token(delta["content"])
                    else:
                        print(delta["content"], end="", flush=True)

                # Reasoning content (thinking mode) — print dimmed to stderr
                if "reasoning_content" in delta and delta["reasoning_content"]:
                    if not reasoning_header_printed and not full_content:
                        print(c("  thinking…", _DIM), file=sys.stderr, flush=True)
                        reasoning_header_printed = True
                    full_reasoning += delta["reasoning_content"]
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

    if full_reasoning:
        print(file=sys.stderr, flush=True)  # newline after dimmed reasoning block

    if full_content:
        print(flush=True)  # final newline after streamed text

    msg: dict = {"role": "assistant", "content": full_content}
    if full_reasoning:
        msg["reasoning_content"] = full_reasoning

    if tool_calls_by_index:
        msg["tool_calls"] = [
            tool_calls_by_index[i]
            for i in sorted(tool_calls_by_index)
        ]

    return msg
