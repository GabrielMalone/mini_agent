#!/usr/bin/env python3
"""
compaction.py -- Context compaction at turn boundaries for cache stability.

Reasonix Pillar 1 (Cache-First Loop) + Pillar 3 (Cost Control).

Two mechanisms:

1. Tool-result truncation (Reasonix Pillar 1):
   Every tool result exceeding TURN_END_RESULT_CAP_TOKENS (3000) is
   truncated at turn-end.  The model had the full text during the turn
   that read it; subsequent turns see a compact summary and can re-read
   if needed.  One extra read_file call is vastly cheaper than dragging
   12KB through every future prompt.

2. Proactive/emergency compaction (Reasonix Pillar 3):
   When context token count exceeds 40% of limit, proactively compact.
   When it exceeds 80%, emergency compact (aggressive truncation).

All compaction is APPEND-ONLY -- we never rewrite the prefix or reorder
log entries.  Compacted content is added as a new message at the end,
never inserted at the front.
"""

from __future__ import annotations

from typing import Any

from memory.memory_prune import _estimate_tokens

# --- Thresholds ---

TURN_END_RESULT_CAP_TOKENS = 3000
"""Max tokens for a tool result retained in subsequent turns.

The model sees the full result during the turn it was produced.
Subsequent turns get a compacted summary.  The agent can always
re-read the full file if needed.
"""

TURN_END_RESULT_CAP_CHARS = 8000
"""Character cap for tool results.  Approximate -- 1 token ≈ 4 chars."""

COMPACTION_RATIO_PROACTIVE = 0.40
"""Context ratio (tokens/limit) that triggers proactive compaction."""

COMPACTION_RATIO_EMERGENCY = 0.80
"""Context ratio (tokens/limit) that triggers emergency compaction."""


def compact_tool_results_at_turn_end(
    messages: list[dict],
    cap_tokens: int = TURN_END_RESULT_CAP_TOKENS,
    cap_chars: int = TURN_END_RESULT_CAP_CHARS,
) -> int:
    """Truncate oversized tool results in-place. Returns count of compacted.

    Only compacts messages where _turn_end_compacted is not already set.
    Sets the marker so each message is compacted at most once.

    IMPORTANT: Call this AFTER the model has seen the full results for
    the current turn (i.e. at turn boundary before the next API call).
    """
    compacted = 0
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        if msg.get("_turn_end_compacted"):
            continue
        content = msg.get("content", "")
        if not content:
            continue
        token_est = _estimate_tokens(msg)
        if token_est <= cap_tokens and len(content) <= cap_chars:
            continue

        # Keep: first 15% + last 60% of content.  Cut the middle.
        head_keep = max(int(len(content) * 0.15), 200)
        tail_keep = max(int(len(content) * 0.60), 1500)
        head = content[:head_keep]
        tail = content[-tail_keep:]
        truncated_len = len(content) - head_keep - tail_keep

        if truncated_len > 0:
            msg["content"] = (
                head
                + f"\n\n... [compacted {truncated_len:} chars / ~{truncated_len // 4:} tokens] ...\n\n"
                + tail
            )
            msg["_turn_end_compacted"] = True
            msg["_original_length"] = len(content)
            compacted += 1

    return compacted


def should_compact(token_count: int, context_limit: int) -> str | None:
    """Return 'proactive', 'emergency', or None based on context ratio.

    Call this BEFORE each API call to decide whether to trigger compaction.
    """
    if context_limit <= 0:
        return None
    ratio = token_count / context_limit
    if ratio > COMPACTION_RATIO_EMERGENCY:
        return "emergency"
    if ratio > COMPACTION_RATIO_PROACTIVE:
        return "proactive"
    return None


def append_compaction_summary(
    messages: list[dict],
    pruned: list[dict],
    summary_text: str,
) -> None:
    """Append a compaction summary message (never insert at front!).

    Contrast with memory.py's current `kept.insert(0, summary_msg)` which
    breaks DeepSeek's prefix cache.  We APPEND the summary so the prefix
    stays byte-stable.
    """
    if not summary_text.strip():
        return
    msg = {
        "role": "user",
        "content": (
            "[CONTEXT COMPACTION] The following turns have been summarized "
            "to stay within context limits.  The conversation prefix is "
            "unchanged, so cached data is preserved:\n\n"
            + summary_text
        ),
    }
    messages.append(msg)


def estimate_context_tokens(messages: list[dict]) -> int:
    """Estimate total token count for a message list.

    Uses the same tokenizer-agnostic heuristic as memory.py.
    """
    return sum(_estimate_tokens(m) for m in messages)
