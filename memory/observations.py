#!/usr/bin/env python3
"""
observations.py -- structured observation storage for mini_agent.

Inspired by claude-mem's observation model:
  - Every tool call that modifies state or produces meaningful output
    can be auto-captured as a typed observation.
  - Observations are typed: bugfix, discovery, decision, refactor, other.
  - Each observation carries a narrative (paragraph), facts (bullets),
    concepts (tags), and file paths touched.
  - Content-based deduplication via content_hash prevents duplicate entries.

Schema (stored in the shared SQLite database):
  observations(
    id, type, title, subtitle, narrative, facts, concepts,
    files_read, files_modified, tool_name, session_id,
    content_hash, created_at, created_at_epoch
  )
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import warnings

# --- Observation types (mirrors claude-mem) ---
OBSERVATION_TYPES = frozenset(
    {
        "bugfix",
        "discovery",
        "decision",
        "refactor",
        "other",
    }
)

# --- Schema DDL (idempotent) ---
OBSERVATIONS_DDL = """
CREATE TABLE IF NOT EXISTS observations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    type              TEXT    NOT NULL DEFAULT 'other'
                              CHECK(type IN ('bugfix','discovery','decision','refactor','other')),
    title             TEXT,
    subtitle          TEXT,
    narrative         TEXT,
    facts             TEXT,   -- JSON array of strings
    concepts          TEXT,   -- JSON array of strings
    files_read        TEXT,   -- JSON array of strings
    files_modified    TEXT,   -- JSON array of strings
    tool_name         TEXT,   -- which tool created this
    session_id        TEXT,   -- content_session_id
    prompt_number     INTEGER,
    content_hash      TEXT    NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at_epoch  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observations_type      ON observations(type);
CREATE INDEX IF NOT EXISTS idx_observations_session   ON observations(session_id);
CREATE INDEX IF NOT EXISTS idx_observations_created   ON observations(created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_observations_hash      ON observations(content_hash, created_at_epoch);
CREATE UNIQUE INDEX IF NOT EXISTS ux_observations_dedup
    ON observations(session_id, content_hash);
"""

# --- Python types ---


class Observation:
    """A single structured observation."""

    __slots__ = (
        "id",
        "type",
        "title",
        "subtitle",
        "narrative",
        "facts",
        "concepts",
        "files_read",
        "files_modified",
        "tool_name",
        "session_id",
        "prompt_number",
        "content_hash",
        "created_at",
        "created_at_epoch",
    )

    def __init__(
        self,
        *,
        id: int = 0,
        type: str = "other",
        title: str | None = None,
        subtitle: str | None = None,
        narrative: str | None = None,
        facts: list[str] | None = None,
        concepts: list[str] | None = None,
        files_read: list[str] | None = None,
        files_modified: list[str] | None = None,
        tool_name: str | None = None,
        session_id: str | None = None,
        prompt_number: int | None = None,
        content_hash: str = "",
        created_at: str = "",
        created_at_epoch: int = 0,
    ):
        self.id = id
        self.type = type
        self.title = title
        self.subtitle = subtitle
        self.narrative = narrative
        self.facts = facts or []
        self.concepts = concepts or []
        self.files_read = files_read or []
        self.files_modified = files_modified or []
        self.tool_name = tool_name
        self.session_id = session_id
        self.prompt_number = prompt_number
        self.content_hash = content_hash
        self.created_at = created_at
        self.created_at_epoch = created_at_epoch

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "subtitle": self.subtitle,
            "narrative": self.narrative,
            "facts": self.facts,
            "concepts": self.concepts,
            "files_read": self.files_read,
            "files_modified": self.files_modified,
            "tool_name": self.tool_name,
            "session_id": self.session_id,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "created_at_epoch": self.created_at_epoch,
        }

    @classmethod
    def from_row(cls, row: tuple) -> "Observation":
        """Build from a SQLite row tuple."""
        (
            id_,
            type_,
            title,
            subtitle,
            narrative,
            facts_json,
            concepts_json,
            files_read_json,
            files_modified_json,
            tool_name,
            session_id,
            prompt_number,
            content_hash,
            created_at,
            created_at_epoch,
        ) = row
        return cls(
            id=id_,
            type=type_,
            title=title,
            subtitle=subtitle,
            narrative=narrative,
            facts=_parse_json_list(facts_json),
            concepts=_parse_json_list(concepts_json),
            files_read=_parse_json_list(files_read_json),
            files_modified=_parse_json_list(files_modified_json),
            tool_name=tool_name,
            session_id=session_id,
            prompt_number=prompt_number,
            content_hash=content_hash,
            created_at=created_at,
            created_at_epoch=created_at_epoch,
        )


def _parse_json_list(raw: str | None) -> list[str]:
    """Parse a JSON string array, returning [] on any failure."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def _to_json_list(items: list[str] | None) -> str:
    """Serialize a list of strings to JSON."""
    return json.dumps(items or [], ensure_ascii=False)


def compute_content_hash(
    tool_name: str,
    title: str,
    narrative: str | None,
    facts: list[str] | None,
) -> str:
    """Compute a deterministic content hash for deduplication."""
    payload = json.dumps(
        {
            "tool": tool_name,
            "title": title,
            "narrative": narrative or "",
            "facts": facts or [],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --- Store operations (operate on a sqlite3.Connection) ---


def ensure_observations_table(conn: sqlite3.Connection) -> None:
    """Create the observations table and indexes if they don't exist."""
    try:
        conn.executescript(OBSERVATIONS_DDL)
        conn.commit()
    except sqlite3.Error as e:
        warnings.warn(f"Failed to create observations table: {e}", stacklevel=2)


def store_observation(
    conn: sqlite3.Connection,
    *,
    type: str = "other",
    title: str | None = None,
    subtitle: str | None = None,
    narrative: str | None = None,
    facts: list[str] | None = None,
    concepts: list[str] | None = None,
    files_read: list[str] | None = None,
    files_modified: list[str] | None = None,
    tool_name: str | None = None,
    session_id: str | None = None,
    prompt_number: int | None = None,
) -> int | None:
    """Insert an observation. Returns id or None on dedup/error."""
    content_hash = compute_content_hash(
        tool_name or "unknown",
        title or "",
        narrative,
        facts,
    )

    try:
        conn.execute(
            """INSERT OR IGNORE INTO observations
               (type, title, subtitle, narrative, facts, concepts,
                files_read, files_modified, tool_name, session_id,
                prompt_number, content_hash, created_at_epoch)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                type if type in OBSERVATION_TYPES else "other",
                title,
                subtitle,
                narrative,
                _to_json_list(facts),
                _to_json_list(concepts),
                _to_json_list(files_read),
                _to_json_list(files_modified),
                tool_name,
                session_id,
                prompt_number,
                content_hash,
                int(time.time()),
            ),
        )
        conn.commit()
        changes = conn.execute("SELECT changes()").fetchone()[0]
        if changes > 0:
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return None  # deduped
    except sqlite3.Error as e:
        warnings.warn(f"Failed to store observation: {e}", stacklevel=2)
        return None


def query_observations(
    conn: sqlite3.Connection,
    *,
    types: list[str] | None = None,
    concepts: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
    session_id: str | None = None,
) -> list[Observation]:
    """Query observations with optional filtering."""
    sql = """SELECT
        id, type, title, subtitle, narrative,
        facts, concepts, files_read, files_modified,
        tool_name, session_id, prompt_number,
        content_hash, created_at, created_at_epoch
    FROM observations WHERE 1=1"""
    params: list = []

    if types:
        placeholders = ",".join(["?"] * len(types))
        sql += f" AND type IN ({placeholders})"
        params.extend(types)

    if concepts:
        # Filter: observation concepts intersect with requested concepts
        concept_clauses = []
        for c in concepts:
            concept_clauses.append("concepts LIKE ?")
            params.append(f'%"{c}"%')
        if concept_clauses:
            sql += f" AND ({' OR '.join(concept_clauses)})"

    if session_id:
        sql += " AND session_id = ?"
        params.append(session_id)

    sql += " ORDER BY created_at_epoch DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    try:
        rows = conn.execute(sql, params).fetchall()
        return [Observation.from_row(r) for r in rows]
    except sqlite3.Error as e:
        warnings.warn(f"Failed to query observations: {e}", stacklevel=2)
        return []


def count_observations(conn: sqlite3.Connection) -> int:
    """Return total observation count."""
    try:
        row = conn.execute("SELECT COUNT(*) FROM observations").fetchone()
        return row[0] if row else 0
    except sqlite3.Error:
        return 0


def get_recent_observation_types(
    conn: sqlite3.Connection, limit: int = 20
) -> list[str]:
    """Return the types of the most recent observations (for context injection)."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT type FROM observations ORDER BY created_at_epoch DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []


def estimate_observation_tokens(observations: list[Observation]) -> dict:
    """Compute token economics for a set of observations (like claude-mem)."""
    CHARS_PER_TOKEN = 4
    total_chars = 0
    for obs in observations:
        total_chars += len(obs.narrative or "")
        total_chars += sum(len(f) for f in (obs.facts or []))
        total_chars += len(obs.title or "")
        total_chars += len(obs.subtitle or "")

    tokens = max(1, total_chars // CHARS_PER_TOKEN)
    return {
        "observation_count": len(observations),
        "estimated_tokens": tokens,
        "total_chars": total_chars,
    }
