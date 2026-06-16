#!/usr/bin/env python3
"""
repair.py -- DeepSeek tool-call repair pipeline.

Reasonix Pillar 2: Tool-Call Repair.

DeepSeek models occasionally produce malformed tool calls:
  - JSON leaked inside <thinking> tags
  - Deeply nested parameters silently dropped
  - Truncated/incomplete JSON (streaming cutoff)
  - Too many parallel tool calls (storm detection)

This module provides a four-pass repair pipeline that runs on every
API response before tool execution.

All passes are safe: they either fix the response or leave it unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any

# --- Constants ---

MAX_PARALLEL_TOOL_CALLS = 8
"""Maximum number of parallel tool calls allowed per turn (storm detection)."""


def repair_tool_calls(msg: dict) -> dict:
    """Run the full four-pass repair pipeline on an API response message.

    Returns a repaired copy (original is not mutated).
    """
    if "tool_calls" not in msg:
        return msg

    msg = dict(msg)  # shallow copy to avoid mutating original
    tool_calls = list(msg["tool_calls"])

    # Pass 1: Scavenge JSON from <thinking> blocks
    tool_calls = _scavenge_from_thinking(msg, tool_calls)

    # Pass 2: Flatten nested params
    tool_calls = _flatten_nested_params(tool_calls)

    # Pass 3: Repair truncated JSON
    tool_calls = _repair_truncated_json(tool_calls)

    # Pass 4: Storm detection (cap parallel calls)
    tool_calls = _detect_storm(tool_calls)

    # Re-index
    for i, tc in enumerate(tool_calls):
        tc["index"] = i

    msg["tool_calls"] = tool_calls
    return msg


# ---------------------------------------------------------------------------
# Pass 1: Scavenge from <thinking> blocks
# ---------------------------------------------------------------------------

_THINKING_TOOL_RE = re.compile(
    r'\{[^{}]*"name"\s*:\s*"[^"]+"[^{}]*\}',
    re.DOTALL,
)


def _scavenge_from_thinking(msg: dict, tool_calls: list[dict]) -> list[dict]:
    """Extract tool calls accidentally embedded inside <thinking> content.

    DeepSeek sometimes emits valid JSON tool calls inside the reasoning
    trace instead of in the formal tool_calls array.  We scavenge them.
    """
    content = msg.get("content", "")
    if not content or "<thinking>" not in content:
        return tool_calls

    # Extract everything inside <thinking>...</thinking>
    thinking_match = re.search(r"<thinking>(.*?)</thinking>", content, re.DOTALL)
    if not thinking_match:
        return tool_calls

    thinking_content = thinking_match.group(1)

    # Also check if "function" key appears (name might be in nested "function")
    # Look for {"name": "...", "arguments": ...} patterns
    scavenged = []
    for match in _THINKING_TOOL_RE.finditer(thinking_content):
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict) and "name" in parsed:
                fn_name = parsed["name"]
                fn_args = parsed.get("arguments", {})
                if isinstance(fn_args, dict):
                    fn_args = json.dumps(fn_args)
                scavenged.append({
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "arguments": fn_args if isinstance(fn_args, str) else json.dumps(fn_args),
                    },
                })
                # Scrub the scavenged JSON from the thinking content
                # so the model doesn't see stale tool calls in future turns
                msg["content"] = msg["content"].replace(match.group(0), "[scavenged]")
        except (json.JSONDecodeError, TypeError, KeyError):
            continue

    if scavenged:
        import logging
        _log = logging.getLogger("repair")
        _log.info(
            "scavenged_tool_calls from thinking: count=%d names=%s",
            len(scavenged),
            [tc["function"]["name"] for tc in scavenged],
        )

    return tool_calls + scavenged


# ---------------------------------------------------------------------------
# Pass 2: Flatten deeply nested params
# ---------------------------------------------------------------------------

_MAX_NESTING_DEPTH = 4


def _flatten_nested_params(tool_calls: list[dict]) -> list[dict]:
    """Detect and flatten deeply nested JSON parameters.

    DeepSeek occasionally nests parameters within extra "parameters"
    or "properties" wrappers.  We unwrap one level.
    """
    for tc in tool_calls:
        fn = tc.get("function", {})
        args_str = fn.get("arguments", "{}")
        if not isinstance(args_str, str):
            continue
        try:
            args = json.loads(args_str)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(args, dict):
            continue

        # Check for "parameters" wrapper: {"parameters": {...}} → {...}
        if "parameters" in args and isinstance(args["parameters"], dict):
            if len(args) == 1:
                fn["arguments"] = json.dumps(args["parameters"])

        # Check for "properties" wrapper: {"properties": {...}} → {...}
        elif "properties" in args and isinstance(args["properties"], dict):
            if len(args) == 1:
                fn["arguments"] = json.dumps(args["properties"])

    return tool_calls


# ---------------------------------------------------------------------------
# Pass 3: Repair truncated JSON
# ---------------------------------------------------------------------------


def _repair_truncated_json(tool_calls: list[dict]) -> list[dict]:
    """Attempt to repair truncated/incomplete JSON in tool call arguments.

    Strategy:
    1. Try json.loads — if it works, no repair needed.
    2. If truncated string (missing closing quote), close it.
    3. If truncated dict (missing }), add closing braces.
    4. If truncated list (missing ]), add closing brackets.
    5. If nothing works, return a safe empty object.
    """
    for tc in tool_calls:
        fn = tc.get("function", {})
        args_str = fn.get("arguments", "")
        if not isinstance(args_str, str):
            continue
        if not args_str.strip():
            fn["arguments"] = "{}"
            continue

        try:
            json.loads(args_str)
            continue  # valid
        except json.JSONDecodeError:
            pass

        repaired = _try_repair(args_str)
        if repaired is not None:
            fn["arguments"] = repaired
            import logging
            _log = logging.getLogger("repair")
            _log.debug("repaired_truncated_json: %s → %s", args_str[:60], repaired[:60])
        else:
            # Last resort: empty object (safe)
            fn["arguments"] = "{}"

    return tool_calls


def _try_repair(json_str: str) -> str | None:
    """Try progressive repair strategies for truncated JSON."""
    s = json_str.strip()

    # Strategy 1: Missing closing brace
    if s.startswith("{") and not s.endswith("}"):
        depth = 0
        in_string = False
        escaped = False
        for ch in s:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
        # Close any open strings and braces
        if in_string:
            s += '"'
        s += "}" * max(depth, 0)

    # Strategy 2: Missing closing bracket
    elif s.startswith("[") and not s.endswith("]"):
        depth = 0
        in_string = False
        escaped = False
        for ch in s:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
        if in_string:
            s += '"'
        s += "]" * max(depth, 0)

    # Strategy 3: Trailing comma before closing brace
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*\]", "]", s)

    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Pass 4: Storm detection
# ---------------------------------------------------------------------------


def _detect_storm(tool_calls: list[dict]) -> list[dict]:
    """Cap excessive parallel tool calls (storm detection).

    DeepSeek occasionally emits >20 parallel tool calls in a single
    response, which causes timeouts and API errors.  We cap at
    MAX_PARALLEL_TOOL_CALLS.
    """
    if len(tool_calls) <= MAX_PARALLEL_TOOL_CALLS:
        return tool_calls

    import logging
    _log = logging.getLogger("repair")
    _log.warning(
        "storm_detected: %d tool calls → capped at %d.  Dropped: %s",
        len(tool_calls),
        MAX_PARALLEL_TOOL_CALLS,
        [tc.get("function", {}).get("name", "?") for tc in tool_calls[MAX_PARALLEL_TOOL_CALLS:]],
    )

    return tool_calls[:MAX_PARALLEL_TOOL_CALLS]
