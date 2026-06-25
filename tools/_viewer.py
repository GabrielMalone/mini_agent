#!/usr/bin/env python3
"""Viewer subagent -- extracts task-relevant code snippets from files.

The SWE-Edit pattern: a cheap "Viewer" subagent filters files to return only
the lines relevant to a natural-language query, preventing context pollution
in the main agent's context window.  This is especially impactful for large
files (e.g. 1000+ line TSX components) where dumping the whole file wastes
tokens and dilutes the model's attention.

Architecture:
    Main Agent (reasoning)
        │
        └── Viewer (this module)
            └── Extracts relevant line ranges via heuristics + optional LLM
                Returns: JSON array of [[start_line, end_line], ...]

For now this is a deterministic heuristic-based implementation that:
  1. Searches for query terms in file content
  2. Returns contiguous blocks around matches with surrounding context
  3. Merges overlapping/nearby blocks
  4. Optionally delegates to an LLM for semantic relevance (if configured)
"""

from __future__ import annotations

import re
from typing import Optional

from tools import _register, _summarize, _TOOL_CONTEXT
from tools.result import ToolResult
from core.safety import WriteSafetyGate
from tools._file_utils import _READ_FILES


def extract_relevant_snippets(
    content: str,
    query: str,
    context_lines: int = 10,
    max_blocks: int = 5,
    min_block_lines: int = 3,
) -> list[tuple[int, int]]:
    """Extract line ranges from *content* relevant to *query*.

    Args:
        content: Full file content as a string.
        query: Natural-language description of what to find (e.g.
               "the handleSubmit function and its callers").
        context_lines: Number of surrounding context lines per match.
        max_blocks: Maximum number of non-contiguous blocks to return.
        min_block_lines: Minimum lines for a standalone block.

    Returns:
        List of (start_line, end_line) tuples, 1-indexed, sorted by start_line.
        start_line is inclusive, end_line is exclusive (Python slice style).
    """
    lines = content.split("\n")
    n_lines = len(lines)
    if n_lines == 0:
        return []

    # Build search tokens from the query
    tokens = _tokenize_query(query)

    # Score each line for relevance
    scores: list[float] = [0.0] * n_lines
    for i, line in enumerate(lines):
        scores[i] = _line_relevance(line, tokens)

    # Find peak regions and expand with context
    blocks = _find_blocks(scores, context_lines, max_blocks, min_block_lines)
    return blocks


def _tokenize_query(query: str) -> list[str]:
    """Extract meaningful search tokens from a natural-language query."""
    # Split on non-alphanumeric boundaries, filter short tokens and stop words
    raw_tokens = re.split(r"[^a-zA-Z0-9_]+", query.lower())
    tokens = [t for t in raw_tokens if len(t) >= 3]
    # Filter out common stop words
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "what",
        "where", "when", "which", "does", "have", "been", "were", "are",
        "its", "not", "but", "all", "can", "has", "had", "how", "will",
    }
    return [t for t in tokens if t not in stop]


def _line_relevance(line: str, tokens: list[str]) -> float:
    """Score a single line's relevance to the token list.

    Returns a float 0.0-1.0.  Exact symbol matches get higher weight.
    """
    if not tokens:
        return 0.0
    line_lower = line.lower()
    score = 0.0
    for token in tokens:
        # Exact word-boundary match (e.g. "handleSubmit")
        if re.search(rf"\b{re.escape(token)}\b", line_lower):
            score += 1.0
        # Substring match (e.g. "submit" matches "handleSubmit")
        elif token in line_lower:
            score += 0.5
        # CamelCase decomposition: "handleSubmit" -> ["handle", "submit"]
        elif _camel_match(token, line_lower):
            score += 0.3
    # Normalize by token count
    return min(score / len(tokens), 1.0)


def _camel_match(token: str, line_lower: str) -> bool:
    """Check if token matches a camelCase/PascalCase/snake_case component."""
    # Split token into sub-tokens at case/snake boundaries
    sub_tokens = re.findall(r"[a-z0-9]+|[A-Z][a-z0-9]*", token)
    sub_tokens_lower = [st.lower() for st in sub_tokens if len(st) >= 3]
    return any(
        re.search(rf"\b{re.escape(st)}\b", line_lower)
        for st in sub_tokens_lower
    )


def _find_blocks(
    scores: list[float],
    context: int,
    max_blocks: int,
    min_block_lines: int,
) -> list[tuple[int, int]]:
    """Convert per-line scores into merged block ranges.

    1-indexed, end-exclusive.
    """
    n = len(scores)
    threshold = 0.15  # Minimum score to consider a line "relevant"

    # Find all lines above threshold
    relevant = [i for i, s in enumerate(scores) if s >= threshold]
    if not relevant:
        # Fall back to the highest-scoring line
        if n == 0:
            return []
        best = max(range(n), key=lambda i: scores[i])
        relevant = [best]

    # Expand each relevant line with context
    ranges: list[tuple[int, int]] = []
    for idx in relevant:
        start = max(0, idx - context)
        end = min(n, idx + context + 1)
        ranges.append((start, end))

    # Merge overlapping or adjacent ranges
    ranges.sort()
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1] + context:
            # Merge: extend the previous block
            prev_start, prev_end = merged.pop()
            merged.append((prev_start, max(prev_end, end)))
        else:
            merged.append((start, end))

    # Filter blocks that are too small
    merged = [(s, e) for s, e in merged if e - s >= min_block_lines]

    # Cap at max_blocks, keeping the widest ones
    if len(merged) > max_blocks:
        merged.sort(key=lambda x: x[1] - x[0], reverse=True)
        merged = merged[:max_blocks]
        merged.sort(key=lambda x: x[0])

    # Convert to 1-indexed, end-exclusive
    return [(s + 1, e) for s, e in merged]


def extract_relevant_content(
    content: str,
    query: str,
    context_lines: int = 10,
    max_blocks: int = 5,
) -> str:
    """Return the file content filtered to only the relevant snippets.

    Convenience wrapper around extract_relevant_snippets that returns
    formatted content ready for display.

    Returns:
        String with snippet blocks separated by "// ... (N lines omitted) ..."
    """
    blocks = extract_relevant_snippets(content, query, context_lines, max_blocks)
    if not blocks:
        return content  # Fall back to full content

    lines = content.split("\n")
    n = len(lines)

    result_parts: list[str] = []
    prev_end = 0  # 0-indexed, exclusive

    for start_1idx, end_1idx in blocks:
        start_0 = start_1idx - 1
        end_0 = end_1idx  # Already exclusive in 0-index

        # Emit omission marker if there's a gap
        if start_0 > prev_end:
            omitted = start_0 - prev_end
            result_parts.append(f"// ... ({omitted} lines omitted) ...")

        # Emit the snippet with line numbers
        snippet = []
        for i in range(start_0, min(end_0, n)):
            snippet.append(f"{i + 1}: {lines[i]}")
        result_parts.append("\n".join(snippet))

        prev_end = end_0

    # Final omission marker
    if prev_end < n:
        omitted = n - prev_end
        result_parts.append(f"// ... ({omitted} lines omitted) ...")

    return "\n\n".join(result_parts)


@_register("view_file")
def _view_file_tool(args: dict, wg: WriteSafetyGate, _rg) -> ToolResult:
    """Viewer subagent tool: extract task-relevant snippets from a file.

    Args:
        path: File path to view
        query: Natural-language description of what to find
        context_lines: Lines of context around each match (default 10)
        max_blocks: Max number of snippet blocks (default 5)
    """
    path = args.get("path", "")
    query = args.get("query", "")
    if not path or not query:
        return ToolResult(
            success=False,
            content="view_file requires 'path' and 'query' parameters.",
        )

    safety_result = wg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"View blocked by safety layer: {safety_result.reason}",
        )
    resolved = safety_result.resolved_path

    if resolved not in _READ_FILES:
        _READ_FILES.add(resolved)

    context_lines = args.get("context_lines", 10)
    max_blocks = args.get("max_blocks", 5)

    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            file_content = f.read()
    except Exception as e:
        return ToolResult(success=False, content=f"Error reading '{resolved}': {e}")

    blocks = extract_relevant_snippets(file_content, query, context_lines, max_blocks)
    if not blocks:
        return ToolResult(
            success=True,
            content=f"No relevant snippets found in {resolved} for query: {query}",
        )

    # Format output
    file_lines = file_content.split("\n")
    n = len(file_lines)
    result_parts: list[str] = []
    prev_end = 0

    for start_1idx, end_1idx in blocks:
        start_0 = start_1idx - 1
        end_0 = end_1idx
        if start_0 > prev_end:
            omitted = start_0 - prev_end
            result_parts.append(f"// ... ({omitted} lines omitted) ...")
        snippet = []
        for i in range(start_0, min(end_0, n)):
            snippet.append(f"{i + 1}: {file_lines[i]}")
        result_parts.append("\n".join(snippet))
        prev_end = end_0

    if prev_end < n:
        omitted = n - prev_end
        result_parts.append(f"// ... ({omitted} lines omitted) ...")

    result = "\n\n".join(result_parts)
    snippet_count = len(blocks)
    total_lines = sum(e - s for s, e in blocks)
    return ToolResult(
        success=True,
        content=(
            f"Found {snippet_count} relevant snippet(s) ({total_lines} lines) "
            f"in {resolved} for query: {query}\n\n{result}"
        ),
    )


@_summarize("view_file")
def _view_file_summary(args: dict) -> str:
    path = args.get("path", "?")
    query = args.get("query", "")
    query_preview = query[:60] + ("..." if len(query) > 60 else "")
    return f'view_file({path}, "{query_preview}")'


def _viewer_llm_fallback(
    content: str, query: str, filepath: str
) -> Optional[list[tuple[int, int]]]:
    """Optional LLM-based viewer for semantic relevance.

    When the simple token-matching heuristic isn't sufficient, this can
    delegate to a cheap LLM (e.g. GPT-5-mini) to identify relevant line
    ranges.  The LLM is prompted to return a JSON array of
    [[start_line, end_line], ...].

    Returns None if LLM is not configured or unavailable.
    """
    try:
        from core.llm_client import get_llm_client
    except ImportError:
        return None

    try:
        client = get_llm_client()
    except Exception:
        return None

    if client is None:
        return None

    lines = content.split("\n")
    n_lines = len(lines)

    # Truncate content if too large (viewer model has limited context)
    max_input_lines = 2000
    if n_lines > max_input_lines:
        # Keep first 200 and last 200 lines + middle sampled
        head = lines[:200]
        tail = lines[-200:]
        mid_sample = lines[n_lines // 2 - 100 : n_lines // 2 + 100]
        truncated_content = (
            "\n".join(
                [f"{i + 1}: {l}" for i, l in enumerate(head)]
            )
            + f"\n// ... ({n_lines - 600} lines omitted) ...\n"
            + "\n".join(
                [f"{n_lines // 2 - 100 + i + 1}: {l}" for i, l in enumerate(mid_sample)]
            )
            + f"\n// ... ({n_lines - 600} lines omitted) ...\n"
            + "\n".join(
                [f"{n_lines - 200 + i + 1}: {l}" for i, l in enumerate(tail)]
            )
        )
    else:
        truncated_content = "\n".join(
            [f"{i + 1}: {l}" for i, l in enumerate(lines)]
        )

    prompt = (
        f"File: {filepath}\n"
        f"Query: {query}\n\n"
        f"Identify all line ranges in this file that are relevant to the query. "
        f"Return ONLY a JSON array of [start_line, end_line] tuples "
        f"(1-indexed, inclusive). For example: [[10, 25], [40, 55]].\n\n"
        f"Content:\n{truncated_content}"
    )

    try:
        response = client.complete(prompt, max_tokens=500)
        # Parse JSON from response
        json_match = re.search(r"\[\[.*?\]\]", response, re.DOTALL)
        if json_match:
            import json

            ranges = json.loads(json_match.group())
            # Validate and clamp
            valid: list[tuple[int, int]] = []
            for r in ranges:
                if isinstance(r, list) and len(r) == 2:
                    start, end = int(r[0]), int(r[1])
                    start = max(1, min(start, n_lines))
                    end = max(start, min(end, n_lines))
                    valid.append((start, end))
            return valid if valid else None
    except Exception:
        pass

    return None
