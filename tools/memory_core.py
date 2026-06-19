#!/usr/bin/env python3
"""
memory_core.py -- Hermes-style bounded persistent core memory tool.

The agent uses this to manage its core memory -- a bounded (~2,500 char)
snapshot injected frozen at session start.  Changes persist immediately
but appear in the system prompt NEXT session.
"""

from __future__ import annotations

from core.safety import ReadSafetyGate, WriteSafetyGate
from tools import _register, _summarize, ToolResult, _TOOL_CONTEXT


@_register("memory_core")
def _memory_core(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Manage persistent core memory (frozen snapshot injected at session start).

    Actions:
        add     -- append a line/entry to core memory
        replace -- replace the entire core memory content (when you need to
                  restructure or consolidate). Use this after reading the
                  current snapshot if you need to merge, dedup, or compress.
        remove  -- remove an entry by line number (1-indexed). The agent should
                  read the current snapshot first to identify the line number.
        read    -- read the current core memory content

    Core memory is hard-capped at ~2,500 chars. When full, **consolidate**
    (merge similar entries, remove stale ones) before adding more.
    Changes persist immediately but appear in the system prompt NEXT session.
    """

    action = args.get("action", "read")
    content = args.get("content", "")
    line_number = args.get("line", 0)

    memory_store = getattr(_TOOL_CONTEXT, "_memory_store", None)
    if memory_store is None:
        # Fallback: try SQLite directly via scratchpad_path
        db_path = getattr(_TOOL_CONTEXT, "scratchpad_path", None)
        if db_path:
            return _memory_core_fallback(db_path, action, content, line_number)
        return ToolResult(
            success=False,
            content="No persistent storage available for core memory.",
        )

    # Happy path: use MemoryStore
    try:
        current = memory_store.get_core_memory()
        info = memory_store.get_core_memory_info()
        char_limit = info["char_limit"]

        if action == "read":
            if current:
                bar_len = 50
                filled = min(len(current) * bar_len // max(char_limit, 1), bar_len)
                bar = "#" * filled + "." * (bar_len - filled)
                pct = min(len(current) * 100 // max(char_limit, 1), 100)
                return ToolResult(
                    success=True,
                    content=(
                        f"Core memory ({len(current)}/{char_limit} chars):\n\n"
                        f"{current}\n\n{bar} {pct}% full"
                    ),
                )
            return ToolResult(success=True, content="Core memory: (empty)")

        elif action == "add":
            new_content = (
                (current + "\n" + content).strip() if current else content
            )
            result = memory_store.write_core_memory(new_content)
            if result["ok"]:
                return ToolResult(success=True, content=result["message"])
            return ToolResult(success=False, content=result["message"])

        elif action == "replace":
            result = memory_store.write_core_memory(content)
            if result["ok"]:
                return ToolResult(success=True, content=result["message"])
            return ToolResult(success=False, content=result["message"])

        elif action == "remove":
            if not current:
                return ToolResult(
                    success=False,
                    content="Core memory is empty. Nothing to remove.",
                )
            lines = current.split("\n")
            if line_number < 1 or line_number > len(lines):
                return ToolResult(
                    success=False,
                    content=(
                        f"Line {line_number} out of range. "
                        f"Lines: 1-{len(lines)}."
                    ),
                )
            removed = lines.pop(line_number - 1)
            new_content = "\n".join(lines).strip()
            result = memory_store.write_core_memory(new_content)
            if result["ok"]:
                return ToolResult(
                    success=True,
                    content=(
                        f"Removed line {line_number}: "
                        f"\"{removed.strip()[:100]}\""
                    ),
                )
            return ToolResult(success=False, content=result["message"])

        else:
            return ToolResult(
                success=False,
                content=(
                    f"Unknown action: '{action}'. "
                    f"Use: add, replace, remove, read."
                ),
            )
    except Exception as e:
        return ToolResult(success=False, content=f"Core memory error: {e}")


# ---------------------------------------------------------------------------
# Structured observation tool (claude-mem inspired)
# ---------------------------------------------------------------------------

@_register("record_observation")
def _record_observation(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Record a structured observation about a tool call, discovery, or decision.

    Observations are typed, tagged, file-linked entries that persist across
    sessions. They're injected into future session context so the agent
    remembers what happened without re-reading full transcripts.

    Types (pick one):
        bugfix     -- a bug was fixed
        discovery  -- something new was learned or found
        decision   -- a design or architectural decision was made
        refactor   -- code was restructured without changing behavior
        other      -- anything else worth remembering

    Provide either 'narrative' (paragraph summary) or 'facts' (bullet points),
    or both. Concepts are tags for filtering. Files track what was touched.
    Content-based deduplication prevents duplicate entries.
    """
    memory_store = getattr(_TOOL_CONTEXT, "_memory_store", None)
    if memory_store is None:
        return ToolResult(
            success=False,
            content="No memory store available. Requires an active session.",
        )

    obs_type = args.get("type", "other")
    title = args.get("title", "")
    subtitle = args.get("subtitle", "")
    narrative = args.get("narrative", "")
    facts = args.get("facts")
    concepts = args.get("concepts")
    files_read = args.get("files_read")
    files_modified = args.get("files_modified")
    tool_name = args.get("tool_name", "")

    # Get session context
    session_id = getattr(_TOOL_CONTEXT, "scratchpad_path", None)
    if session_id:
        import os
        session_id = os.path.basename(session_id).replace(".db", "")

    obs_id = memory_store.record_observation(
        type=obs_type,
        title=title or None,
        subtitle=subtitle or None,
        narrative=narrative or None,
        facts=facts if isinstance(facts, list) else None,
        concepts=concepts if isinstance(concepts, list) else None,
        files_read=files_read if isinstance(files_read, list) else None,
        files_modified=files_modified if isinstance(files_modified, list) else None,
        tool_name=tool_name or None,
        session_id=session_id,
    )

    if obs_id:
        return ToolResult(
            success=True,
            content=f"Observation #{obs_id} recorded: [{obs_type}] {title[:80] if title else '(untitled)'}",
        )
    else:
        return ToolResult(
            success=True,
            content="Observation deduplicated (same content already exists for this session).",
        )


@_summarize("record_observation")
def _record_observation_summary(args: dict) -> str:
    obs_type = args.get("type", "other")
    title = args.get("title", "?")
    return f"record_observation({obs_type}, \"{title[:50]}\")"


# ---------------------------------------------------------------------------
# Session summary tool (claude-mem inspired)
# ---------------------------------------------------------------------------

@_register("write_session_summary")
def _write_session_summary(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Write a structured session summary for cross-session continuity.

    The summary is injected into future sessions so the agent picks up where
    it left off. Fill in the 5 canonical fields:

        request       -- what the user asked for this session
        investigated  -- what was looked into / explored
        learned       -- key discoveries and insights
        completed     -- what was accomplished / delivered
        next_steps    -- what remains to be done

    Also optionally provide files_read, files_edited lists.
    """
    memory_store = getattr(_TOOL_CONTEXT, "_memory_store", None)
    if memory_store is None:
        return ToolResult(
            success=False,
            content="No memory store available. Requires an active session.",
        )

    session_id = getattr(_TOOL_CONTEXT, "scratchpad_path", None)
    if session_id:
        import os
        session_id = os.path.basename(session_id).replace(".db", "")

    workspace = getattr(_TOOL_CONTEXT, "workspace", None)
    project = workspace if workspace else None

    request = args.get("request", "")
    investigated = args.get("investigated", "")
    learned = args.get("learned", "")
    completed = args.get("completed", "")
    next_steps = args.get("next_steps", "")
    files_read = args.get("files_read")
    files_edited = args.get("files_edited")
    notes = args.get("notes", "")

    summary_id = memory_store.store_session_summary(
        session_id=session_id or "",
        project=project,
        request=request or None,
        investigated=investigated or None,
        learned=learned or None,
        completed=completed or None,
        next_steps=next_steps or None,
        files_read=files_read if isinstance(files_read, list) else None,
        files_edited=files_edited if isinstance(files_edited, list) else None,
        notes=notes or None,
    )

    if summary_id:
        return ToolResult(
            success=True,
            content=f"Session summary #{summary_id} stored. Will be injected into next session context.",
        )
    else:
        return ToolResult(
            success=False,
            content="Failed to store session summary.",
        )


@_summarize("write_session_summary")
def _write_session_summary_summary(args: dict) -> str:
    request = args.get("request", "?")
    return f"write_session_summary(\"{request[:50]}\")"


# ---------------------------------------------------------------------------
# Read observations tool
# ---------------------------------------------------------------------------

@_register("read_observations")
def _read_observations(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Query structured observations from the session database.

    Filter by type (bugfix, discovery, decision, refactor, other),
    concepts (tags), or session_id. Returns observations ordered by recency.
    Use this to review past discoveries, decisions, or bugfixes.
    """
    memory_store = getattr(_TOOL_CONTEXT, "_memory_store", None)
    if memory_store is None:
        return ToolResult(
            success=False,
            content="No memory store available. Requires an active session.",
        )

    types = args.get("types")
    concepts = args.get("concepts")
    limit = args.get("limit", 20)
    offset = args.get("offset", 0)
    session_id = args.get("session_id")

    observations = memory_store.query_observations(
        types=types if isinstance(types, list) else None,
        concepts=concepts if isinstance(concepts, list) else None,
        limit=min(int(limit), 100),
        offset=int(offset),
        session_id=session_id or None,
    )

    if not observations:
        return ToolResult(
            success=True,
            content="No observations found matching the given filters.",
        )

    from memory.observations import estimate_observation_tokens
    economics = estimate_observation_tokens(observations)

    lines = [
        f"Observations ({len(observations)} total, ~{economics['estimated_tokens']} tokens):",
        "",
    ]
    for obs in observations:
        type_tag = f"[{obs.type}]"
        title_str = obs.title or "(untitled)"
        lines.append(f"#{obs.id} {type_tag} {title_str}")
        if obs.narrative:
            narrative_preview = obs.narrative[:200]
            if len(obs.narrative) > 200:
                narrative_preview += "..."
            lines.append(f"   {narrative_preview}")
        if obs.facts:
            for fact in obs.facts[:5]:
                lines.append(f"   • {fact}")
            if len(obs.facts) > 5:
                lines.append(f"   ... and {len(obs.facts) - 5} more facts")
        if obs.files_modified:
            lines.append(f"   Files: {', '.join(obs.files_modified[:5])}")
        lines.append("")

    return ToolResult(success=True, content="\n".join(lines))


@_summarize("read_observations")
def _read_observations_summary(args: dict) -> str:
    types = args.get("types", "all")
    return f"read_observations(types={types})"


@_summarize("memory_core")
def _memory_core_summary(args: dict) -> str:
    action = args.get("action", "?")
    content = args.get("content", "")
    preview = (
        content[:50] + ("..." if len(content) > 50 else "")
        if content else ""
    )
    return f"memory_core({action}, \"{preview}\")"


@_register("session_search")
def _session_search(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Search past session history using FTS5 full-text search.

    Use this when the user references something from a previous conversation
    ("we fixed this before," "use the approach from last time," "what did we
    change last week?"). Searches across all saved messages in the session DB.

    Returns up to 10 matching message excerpts ordered by relevance.
    """
    query = args.get("query", "")
    limit = args.get("limit", 10)

    if not query.strip():
        return ToolResult(
            success=False,
            content="Missing required parameter: 'query' (search terms).",
        )

    memory_store = getattr(_TOOL_CONTEXT, "_memory_store", None)
    if memory_store is None:
        db_path = getattr(_TOOL_CONTEXT, "scratchpad_path", None)
        if db_path:
            return _session_search_fallback(db_path, query, limit)
        return ToolResult(
            success=False,
            content="No persistent storage available for session search.",
        )

    try:
        results = memory_store.search_messages(query, limit=limit)
        if not results:
            return ToolResult(
                success=True,
                content=f"No results found for: \"{query[:200]}\"",
            )
        lines = [f"Search results for \"{query[:200]}\":\n"]
        for r in results:
            content = r["content"]
            if len(content) > 300:
                content = content[:300] + "..."
            lines.append(
                f"  [{r['rowid']}] (rank={r['rank']:.2f}) {content}"
            )
        return ToolResult(success=True, content="\n".join(lines))
    except Exception as e:
        return ToolResult(
            success=False, content=f"Session search error: {e}"
        )


@_summarize("session_search")
def _session_search_summary(args: dict) -> str:
    query = args.get("query", "?")
    return f"session_search(\"{query[:60]}\")"


def _session_search_fallback(
    db_path: str, query: str, limit: int,
) -> ToolResult:
    """Fallback FTS5 search without MemoryStore."""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=5000")
        safe_query = query.replace('"', '""')
        rows = conn.execute(
            "SELECT rowid, content, rank"
            " FROM messages_fts"
            " WHERE messages_fts MATCH ?"
            " ORDER BY rank"
            " LIMIT ?",
            (f'"{safe_query}"', limit),
        ).fetchall()
        if not rows:
            conn.close()
            return ToolResult(
                success=True,
                content=f"No results found for: \"{query[:200]}\"",
            )
        lines = [f"Search results for \"{query[:200]}\":\n"]
        for r in rows:
            content = r[1]
            if len(content) > 300:
                content = content[:300] + "..."
            lines.append(
                f"  [{r[0]}] (rank={r[2]:.2f}) {content}"
            )
        conn.close()
        return ToolResult(success=True, content="\n".join(lines))
    except Exception as e:
        return ToolResult(
            success=False, content=f"Session search error: {e}"
        )


def _memory_core_fallback(
    db_path: str, action: str, content: str, line_number: int,
) -> ToolResult:
    """Fallback implementation that uses SQLite directly (no MemoryStore)."""
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=5000")

        if action == "read":
            row = conn.execute(
                "SELECT content, char_limit FROM core_memory WHERE id = 1"
            ).fetchone()
            if row:
                c = row[0]
                cl = row[1]
                bar_filled = min(len(c) * 50 // max(cl, 1), 50)
                bar = "#" * bar_filled + "." * (50 - bar_filled)
                result = ToolResult(
                    success=True,
                    content=(
                        f"Core memory ({len(c)}/{cl} chars):\n\n"
                        f"{c if c else '(empty)'}\n\n"
                        f"{bar} {len(c) * 100 // max(cl, 1)}% full\n"
                    ),
                )
                conn.close()
                return result
            conn.close()
            return ToolResult(success=True, content="Core memory: (empty)")

        elif action == "add":
            row = conn.execute(
                "SELECT content, char_limit FROM core_memory WHERE id = 1"
            ).fetchone()
            if row:
                current = row[0]
                char_limit = row[1]
                new_content = (
                    (current + "\n" + content).strip() if current else content
                )
                if len(new_content) > char_limit:
                    remaining = (
                        char_limit - len(current) if current else char_limit
                    )
                    conn.close()
                    return ToolResult(
                        success=False,
                        content=(
                            f"Cannot add: would exceed {char_limit} char limit. "
                            f"({len(new_content)} chars with addition, only "
                            f"{remaining} remaining). Consolidate memory first: "
                            f"merge similar entries, remove stale ones."
                        ),
                    )
                conn.execute(
                    "UPDATE core_memory SET content = ? WHERE id = 1",
                    (new_content,),
                )
                conn.commit()
                rem = char_limit - len(new_content)
                conn.close()
                return ToolResult(
                    success=True,
                    content=(
                        f"Added to core memory. {rem} chars remaining "
                        f"({len(new_content)}/{char_limit} used)."
                    ),
                )
            conn.close()
            return ToolResult(success=False, content="Core memory table not found.")

        elif action == "replace":
            row = conn.execute(
                "SELECT char_limit FROM core_memory WHERE id = 1"
            ).fetchone()
            char_limit = row[0] if row else 2500
            if len(content) > char_limit:
                conn.close()
                return ToolResult(
                    success=False,
                    content=(
                        f"Cannot replace: content ({len(content)} chars) exceeds "
                        f"limit of {char_limit} chars. Consolidate first."
                    ),
                )
            conn.execute(
                "UPDATE core_memory SET content = ? WHERE id = 1", (content,)
            )
            conn.commit()
            rem = char_limit - len(content)
            conn.close()
            return ToolResult(
                success=True,
                content=(
                    f"Core memory replaced. {rem} chars remaining "
                    f"({len(content)}/{char_limit} used)."
                ),
            )

        elif action == "remove":
            row = conn.execute(
                "SELECT content FROM core_memory WHERE id = 1"
            ).fetchone()
            if not row or not row[0]:
                conn.close()
                return ToolResult(
                    success=False,
                    content="Core memory is empty. Nothing to remove.",
                )
            lines = row[0].split("\n")
            if line_number < 1 or line_number > len(lines):
                conn.close()
                return ToolResult(
                    success=False,
                    content=(
                        f"Line {line_number} out of range. "
                        f"Lines: 1-{len(lines)}."
                    ),
                )
            removed = lines.pop(line_number - 1)
            new_content = "\n".join(lines).strip()
            conn.execute(
                "UPDATE core_memory SET content = ? WHERE id = 1",
                (new_content,),
            )
            conn.commit()
            conn.close()
            return ToolResult(
                success=True,
                content=(
                    f"Removed line {line_number}: "
                    f"\"{removed.strip()[:100]}\""
                ),
            )

        else:
            conn.close()
            return ToolResult(
                success=False,
                content=(
                    f"Unknown action: '{action}'. "
                    f"Use: add, replace, remove, read."
                ),
            )
    except Exception as e:
        return ToolResult(success=False, content=f"Core memory error: {e}")
