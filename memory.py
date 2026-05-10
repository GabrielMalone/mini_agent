#!/usr/bin/env python3
"""
memory.py — persistent conversation memory for mini_agent (SQLite backend).

Stores messages as rows in a local SQLite database.  Provides the same API
as the old JSON-backed MemoryStore so the orchestrator is unchanged.

Automatically prunes old messages to stay within a configurable limit,
preserving complete tool-call sequences and turn boundaries.

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
# MemoryStore
# ---------------------------------------------------------------------------

class MemoryStore:
    """Persists conversation messages in a SQLite database.

    The system message is intentionally excluded from persistence.
    On load, callers are expected to prepend their own system prompt.

    *max_messages* controls automatic pruning: old messages are removed
    when the count exceeds the limit, preserving complete tool-call
    sequences and turn boundaries.
    """

    DEFAULT_MAX_MESSAGES = 200

    def __init__(self, filepath: str, max_messages: int = DEFAULT_MAX_MESSAGES) -> None:
        self._filepath = filepath
        self._db_path = _db_path(filepath)
        self._max_messages = max_messages

        # Migrate old JSON file if present and DB doesn't exist yet
        if not os.path.exists(self._db_path) and os.path.isfile(filepath):
            _migrate_json(filepath, self._db_path)

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

        System messages and incomplete tool-call sequences are stripped
        before writing.  Old messages are pruned to stay within the
        configured *max_messages* limit, preserving turn boundaries.
        The entire message set is replaced atomically.
        """
        cleaned = _clean_messages(messages)
        cleaned = _prune_messages(cleaned, self._max_messages)
        self._ensure_parent()
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(_DELETE)
                conn.executemany(
                    _INSERT,
                    [(m["role"], json.dumps(m)) for m in cleaned],
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
    base, _ = os.path.splitext(filepath)
    return base + ".db"


def _row_to_msg(row: tuple[str, str]) -> dict:
    """Decode a database row into a message dict."""
    try:
        return json.loads(row[1])
    except (json.JSONDecodeError, TypeError):
        return {"role": row[0], "content": ""}


def _clean_messages(messages: list[dict]) -> list[dict]:
    """Strip system messages and incomplete tool-call sequences."""
    cleaned: list[dict] = []
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            continue
        tool_ids = {tc["id"] for tc in m.get("tool_calls", [])}
        if tool_ids:
            remaining = messages[i + 1:]
            matched = {
                r.get("tool_call_id")
                for r in remaining
                if r.get("role") == "tool"
            }
            if not tool_ids.issubset(matched):
                break  # incomplete — discard this and everything after
        cleaned.append(m)
    return cleaned


def _prune_messages(messages: list[dict], max_count: int) -> list[dict]:
    """Trim *messages* from the front to fit within *max_count*.

    Pruning preserves turn boundaries: a cut is only made at a ``user``
    message boundary, so tool-call sequences are never split and the
    context starts cleanly at the beginning of a turn.
    """
    if len(messages) <= max_count:
        return messages

    excess = len(messages) - max_count
    # Walk from the front looking for the first "user" message at or beyond
    # the cut point — this ensures clean turn boundaries.
    cut = excess
    for i in range(excess, len(messages)):
        if messages[i].get("role") == "user":
            cut = i
            break
    else:
        # No user message found after the cut — trim exactly excess
        cut = excess

    return messages[cut:]


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
