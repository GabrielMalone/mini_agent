#!/usr/bin/env python3
"""
memory.py — persistent conversation memory for mini_agent (SQLite backend).

Stores messages as rows in a local SQLite database.  Provides the same API
as the old JSON-backed MemoryStore so the orchestrator is unchanged.

Memory management (in order, applied on every save):
    1. Compress old tool results — keep only the first line for results
       more than N messages ago.
    2. Token-aware pruning — drop oldest turns until under max_tokens
       (preserving tool-call sequences and turn boundaries).
    3. Conversation summarization — when pruning removes messages, a
       synthetic "Earlier context" summary is injected so the agent
       retains awareness of what happened even when details are gone.

Migrates existing ``.mini_agent_memory.json`` files automatically on first run.
"""

import json
import os
import sqlite3
from typing import Optional


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,   -- JSON blob of the full message dict
    created_at TEXT    DEFAULT (datetime('now'))
)
"""

_INSERT  = "INSERT INTO messages (role, content) VALUES (?, ?)"
_SELECT  = "SELECT role, content FROM messages ORDER BY id ASC"
_DELETE  = "DELETE FROM messages"
_VACUUM  = "VACUUM"


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(msg: dict) -> int:
    """Rough token estimate for a single message.

    Heuristic: ~4 characters per token (works well for English/code).
    For tool results (JSON content), we parse and estimate just the
    content field — the JSON wrapper overhead is negligible.
    """
    if msg.get("role") == "tool":
        try:
            data = json.loads(msg["content"])
            text = data.get("content", "")
        except (json.JSONDecodeError, TypeError):
            text = msg.get("content", "")
    elif msg.get("role") == "assistant" and msg.get("tool_calls"):
        # Tool-call messages: count the arguments text
        total = len(msg.get("content", ""))
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            total += len(json.dumps(fn.get("arguments", "")))
        return max(1, total // 4)
    else:
        text = msg.get("content", "") or json.dumps(msg)

    return max(1, len(text) // 4)


def _total_tokens(messages: list[dict]) -> int:
    """Sum estimated tokens across all messages."""
    return sum(_estimate_tokens(m) for m in messages)


# ---------------------------------------------------------------------------
# Tool result compression
# ---------------------------------------------------------------------------

def _compress_tool_results(
    messages: list[dict],
    keep_recent: int = 6,
) -> list[dict]:
    """Shorten old tool results to their first line only.

    Tool results within the last *keep_recent* messages are left intact.
    Older ones are trimmed to the first line + truncation marker.
    """
    if len(messages) <= keep_recent:
        return messages

    # Messages that are "recent" (within the tail window) stay untouched
    cutoff = len(messages) - keep_recent
    for i, m in enumerate(messages):
        if i >= cutoff:
            break
        if m.get("role") != "tool":
            continue
        try:
            data = json.loads(m["content"])
            text = data.get("content", "")
        except (json.JSONDecodeError, TypeError):
            continue

        # Only compress if there's more than 5 lines
        lines = text.split("\n")
        if len(lines) <= 5:
            continue

        kept = "\n".join(lines[:5])
        # Trim very long first lines
        if len(kept) > 500:
            kept = kept[:500] + "…"

        new_content = kept + f"\n… (truncated at 5 lines — {len(lines)} total)"
        data["content"] = new_content
        m["content"] = json.dumps(data)

    return messages


# ---------------------------------------------------------------------------
# Conversation summarization
# ---------------------------------------------------------------------------

def _summarize_pruned(pruned: list[dict]) -> str:
    """Build a one-paragraph summary of pruned messages.

    The summary is injected as a synthetic 'user' message so the agent
    sees it as prior conversation context.
    """
    if not pruned:
        return ""

    files_read: list[str] = []
    files_written: list[str] = []
    files_edited: list[str] = []
    commands_run: list[str] = []
    turns: list[str] = []

    for m in pruned:
        role = m.get("role", "")
        if role == "user":
            content = m.get("content", "")
            preview = content[:120].replace("\n", " ")
            if len(content) > 120:
                preview += "…"
            turns.append(f"User: {preview}")

        elif role == "tool":
            try:
                data = json.loads(m["content"])
                text = data.get("content", "")
            except (json.JSONDecodeError, TypeError):
                text = m.get("content", "")

            if "bytes to" in text or "OK: wrote" in text or "OK: replaced" in text:
                # Extract path
                path = text.split(" to ")[-1].split("\n")[0] if " to " in text else text
                if len(path) > 80:
                    path = path[:80] + "…"
                if "replaced" in text:
                    files_edited.append(path)
                else:
                    files_written.append(path)

        elif role == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                if name == "read_file":
                    p = args.get("path", "?")
                    if p not in files_read:
                        files_read.append(p)
                elif name == "run_shell":
                    cmd = args.get("command", "?")
                    preview = cmd[:80]
                    if len(cmd) > 80:
                        preview += "…"
                    commands_run.append(preview)
                elif name == "web_search":
                    q = args.get("query", "?")
                    turns.append(f"Searched web: {q[:80]}")

    parts: list[str] = ["Earlier in this conversation:"]
    if turns:
        for t in turns[-3:]:  # last 3 user messages
            parts.append(f"- {t}")
    if files_read:
        unique = list(dict.fromkeys(files_read))  # dedupe, preserve order
        parts.append(f"- Files read: {', '.join(unique[:5])}")
    if files_written:
        unique = list(dict.fromkeys(files_written))
        parts.append(f"- Files written: {', '.join(unique[:5])}")
    if files_edited:
        unique = list(dict.fromkeys(files_edited))
        parts.append(f"- Files edited: {', '.join(unique[:5])}")
    if commands_run:
        parts.append(f"- Commands run: {', '.join(commands_run[:3])}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Token-aware pruning
# ---------------------------------------------------------------------------

def _prune_by_tokens(
    messages: list[dict],
    max_tokens: int,
    max_messages: int,
) -> tuple[list[dict], list[dict]]:
    """Trim *messages* from the front to stay within budget.

    Returns (kept_messages, pruned_messages).  Pruning preserves turn
    boundaries: cuts only at ``user`` message boundaries, so tool-call
    sequences are never split.  *max_messages* is a hard cap applied
    first, then *max_tokens* is the soft budget.
    """
    if not messages:
        return [], []

    # 1. Hard cap by message count
    if len(messages) > max_messages:
        excess = len(messages) - max_messages
        cut = excess
        for i in range(excess, len(messages)):
            if messages[i].get("role") == "user":
                cut = i
                break
        else:
            cut = excess
        pruned = messages[:cut]
        messages = messages[cut:]
    else:
        pruned = []

    # 2. Token budget — trim oldest turns until under limit
    while _total_tokens(messages) > max_tokens and len(messages) > 1:
        # Find first user message boundary
        cut = 0
        for i, m in enumerate(messages):
            if m.get("role") == "user":
                cut = i
                break
        if cut == 0:
            # No user message found — stop, can't safely prune further
            break
        pruned = pruned + messages[:cut]
        messages = messages[cut:]

    return messages, pruned


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class MemoryStore:
    """Persists conversation messages in a SQLite database.

    The system message is intentionally excluded from persistence.
    On load, callers are expected to prepend their own system prompt.

    *max_tokens* controls token-aware pruning: old turns are removed
    when the estimated token count exceeds the limit.  *max_messages*
    is a hard cap applied first.  Both preserve tool-call sequences
    and turn boundaries.

    Old tool results are compressed (first-line only) after they fall
    more than 6 messages behind the tail.  Pruned messages are summarized
    into a synthetic context message.
    """

    DEFAULT_MAX_MESSAGES = 500
    DEFAULT_MAX_TOKENS   = 800_000

    def __init__(
        self,
        filepath: str,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._filepath = filepath
        self._db_path = _db_path(filepath)
        self._max_messages = max_messages
        self._max_tokens = max_tokens

        # Migrate from old paths if needed
        _migrate_old_paths(filepath, self._db_path)

        # Ensure parent directory exists
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(_CREATE_TABLE)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scratchpad ("
                "id INTEGER PRIMARY KEY CHECK (id = 1),"
                "content TEXT NOT NULL DEFAULT ''"
                ")"
            )
            # Ensure a row always exists
            conn.execute("INSERT OR IGNORE INTO scratchpad (id, content) VALUES (1, '')")
            conn.commit()
        finally:
            conn.close()

        self._ensure_table()

    @property
    def filepath(self) -> str:
        return self._filepath

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> list[dict]:
        """Load saved messages, stripping incomplete tool-call sequences."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(_SELECT).fetchall()
        except sqlite3.Error:
            return []

        return _clean_messages([_row_to_msg(r) for r in rows])

    def save(self, messages: list[dict]) -> None:
        """Persist *messages* to the database.

        1. Strip system messages and incomplete tool-call sequences.
        2. Compress old tool results (first-line only).
        3. Prune by token budget, preserving turn boundaries.
        4. Summarize pruned messages into a context note.
        5. Write atomically to SQLite.
        """
        cleaned = _clean_messages(messages)
        cleaned = _compress_tool_results(cleaned, keep_recent=6)

        kept, pruned = _prune_by_tokens(
            cleaned, self._max_tokens, self._max_messages,
        )

        # Inject summary of pruned context
        if pruned:
            summary = _summarize_pruned(pruned)
            if summary:
                kept.insert(0, {"role": "user", "content": summary})

        self._ensure_parent()
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(_DELETE)
                conn.executemany(
                    _INSERT,
                    [(m["role"], json.dumps(m)) for m in kept],
                )
                conn.commit()
        except sqlite3.Error:
            pass  # fail gracefully — next save will retry

    def clear(self) -> None:
        """Remove all messages and reclaim disk space."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(_DELETE)
                conn.execute(_VACUUM)
        except sqlite3.Error:
            try:
                os.remove(self._db_path)
            except FileNotFoundError:
                pass

    def get_scratchpad(self) -> str:
        """Return the current scratchpad content (empty string if none)."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT content FROM scratchpad WHERE id = 1"
                ).fetchone()
                return row[0] if row else ""
        except sqlite3.Error:
            return ""

    def set_scratchpad(self, content: str) -> None:
        """Update the scratchpad content."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO scratchpad (id, content) VALUES (1, ?)",
                    (content,),
                )
        except sqlite3.Error:
            pass  # fail gracefully

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_parent(self) -> None:
        """Create parent directories of the database file if needed."""
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _ensure_table(self) -> None:
        """Create the messages table if it doesn't exist."""
        self._ensure_parent()
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(_CREATE_TABLE)
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _db_path(filepath: str) -> str:
    """Derive the SQLite database path from the configured filepath."""
    if filepath.endswith(".db"):
        return filepath
    base, _ = os.path.splitext(filepath)
    return base + ".db"


def _row_to_msg(row: tuple[str, str]) -> dict:
    """Decode a database row into a message dict."""
    try:
        return json.loads(row[1])
    except (json.JSONDecodeError, TypeError):
        return {"role": row[0], "content": ""}


def _clean_messages(messages: list[dict]) -> list[dict]:
    """Strip system messages, orphaned tool results, and incomplete tool-call sequences.

    Two-pass validation:

    1. **Backward pass** — remove ``tool`` messages whose ``tool_call_id``
       has no *preceding* assistant message with a matching ``tool_calls``
       entry.  This catches the "tool result before assistant" ordering bug
       that causes API 400 errors.

    2. **Forward pass** — truncate at any assistant message whose
       ``tool_calls`` have no matching ``tool`` results *after* it.  This
       catches incomplete / dangling tool-call sequences.
    """
    # ---- backward pass: remove orphaned tool results ----
    valid_ids: set[str] = set()  # tool_call_ids seen so far from assistants
    pass1: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            continue
        if m.get("role") == "tool":
            tcid = m.get("tool_call_id", "")
            if tcid and tcid not in valid_ids:
                continue  # orphaned — no preceding assistant owns this id
        pass1.append(m)
        # Accumulate valid ids from this message (only assistant with tool_calls)
        for tc in m.get("tool_calls", []):
            tcid = tc.get("id", "")
            if tcid:
                valid_ids.add(tcid)

    # ---- forward pass: truncate incomplete tool-call sequences ----
    result: list[dict] = []
    for i, m in enumerate(pass1):
        tool_ids = {tc["id"] for tc in m.get("tool_calls", [])}
        if tool_ids:
            remaining = pass1[i + 1:]
            matched = {
                r.get("tool_call_id")
                for r in remaining
                if r.get("role") == "tool"
            }
            if not tool_ids.issubset(matched):
                break  # incomplete — discard this assistant and everything after
        result.append(m)
    return result


def _migrate_old_paths(new_filepath: str, db_path: str) -> None:
    """Migrate from old naming schemes to the current db_path.

    Old scheme: config said .json, _db_path appended .db → .json.db
    New scheme: config says .db, _db_path uses it directly → .db

    Also migrates raw JSON files if present.
    """
    if os.path.exists(db_path):
        return  # already migrated

    # Old path: if config was .json, old db was .json.db
    base, ext = os.path.splitext(new_filepath)
    if ext != ".db":
        old_db = base + ".db"
        if os.path.isfile(old_db):
            try:
                os.rename(old_db, db_path)
                return
            except OSError:
                pass  # fall through — will start fresh

    # Old JSON file — migrate its contents
    if os.path.isfile(new_filepath):
        _migrate_json(new_filepath, db_path)


def _migrate_json(json_path: str, db_path: str) -> None:
    """Migrate an existing JSON memory file to SQLite."""
    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    if not isinstance(data, list):
        return

    cleaned = _clean_messages(data)
    if not cleaned:
        return

    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(_CREATE_TABLE)
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                _INSERT,
                [(m["role"], json.dumps(m)) for m in cleaned],
            )
            conn.commit()
    except sqlite3.Error:
        return
