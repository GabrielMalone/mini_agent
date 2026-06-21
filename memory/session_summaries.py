#!/usr/bin/env python3
"""
session_summaries.py -- structured session summaries for mini_agent.

Inspired by claude-mem's session summary model:
  - At the end of each session, the agent (or auto-summarizer) records
    a structured summary with 5 canonical fields:
      - request:    what the user asked for
      - investigated: what was looked into
      - learned:    key discoveries
      - completed:  what got done
      - next_steps: what remains
  - Summaries are injected into the next session's context so the agent
    picks up where it left off without re-reading the full transcript.

Schema:
  session_summaries(
    id, session_id, project, prompt_number,
    request, investigated, learned, completed, next_steps,
    files_read, files_edited, notes,
    created_at, created_at_epoch
  )
"""

from __future__ import annotations

import json
import sqlite3
import time
import warnings

SESSION_SUMMARIES_DDL = """
CREATE TABLE IF NOT EXISTS session_summaries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT    NOT NULL,
    project           TEXT,
    prompt_number     INTEGER,
    request           TEXT,
    investigated      TEXT,
    learned           TEXT,
    completed         TEXT,
    next_steps        TEXT,
    files_read        TEXT,   -- JSON array of strings
    files_edited      TEXT,   -- JSON array of strings
    notes             TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at_epoch  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_summaries_session  ON session_summaries(session_id);
CREATE INDEX IF NOT EXISTS idx_session_summaries_created  ON session_summaries(created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_session_summaries_project  ON session_summaries(project);
"""


class SessionSummary:
    """A single session summary."""

    __slots__ = (
        "id",
        "session_id",
        "project",
        "prompt_number",
        "request",
        "investigated",
        "learned",
        "completed",
        "next_steps",
        "files_read",
        "files_edited",
        "notes",
        "created_at",
        "created_at_epoch",
    )

    def __init__(
        self,
        *,
        id: int = 0,
        session_id: str = "",
        project: str | None = None,
        prompt_number: int | None = None,
        request: str | None = None,
        investigated: str | None = None,
        learned: str | None = None,
        completed: str | None = None,
        next_steps: str | None = None,
        files_read: list[str] | None = None,
        files_edited: list[str] | None = None,
        notes: str | None = None,
        created_at: str = "",
        created_at_epoch: int = 0,
    ):
        self.id = id
        self.session_id = session_id
        self.project = project
        self.prompt_number = prompt_number
        self.request = request
        self.investigated = investigated
        self.learned = learned
        self.completed = completed
        self.next_steps = next_steps
        self.files_read = files_read or []
        self.files_edited = files_edited or []
        self.notes = notes
        self.created_at = created_at
        self.created_at_epoch = created_at_epoch

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "project": self.project,
            "prompt_number": self.prompt_number,
            "request": self.request,
            "investigated": self.investigated,
            "learned": self.learned,
            "completed": self.completed,
            "next_steps": self.next_steps,
            "files_read": self.files_read,
            "files_edited": self.files_edited,
            "notes": self.notes,
            "created_at": self.created_at,
            "created_at_epoch": self.created_at_epoch,
        }

    @classmethod
    def from_row(cls, row: tuple) -> "SessionSummary":
        (
            id_,
            session_id,
            project,
            prompt_number,
            request,
            investigated,
            learned,
            completed,
            next_steps,
            files_read_json,
            files_edited_json,
            notes,
            created_at,
            created_at_epoch,
        ) = row
        return cls(
            id=id_,
            session_id=session_id,
            project=project,
            prompt_number=prompt_number,
            request=request,
            investigated=investigated,
            learned=learned,
            completed=completed,
            next_steps=next_steps,
            files_read=_parse_list(files_read_json),
            files_edited=_parse_list(files_edited_json),
            notes=notes,
            created_at=created_at,
            created_at_epoch=created_at_epoch,
        )

    def render_for_context(self) -> str:
        """Render this summary as a compact text block for context injection."""
        parts = []
        if self.request:
            parts.append(f"Request: {self.request}")
        if self.investigated:
            parts.append(f"Investigated: {self.investigated}")
        if self.learned:
            parts.append(f"Learned: {self.learned}")
        if self.completed:
            parts.append(f"Completed: {self.completed}")
        if self.next_steps:
            parts.append(f"Next steps: {self.next_steps}")
        if self.files_read:
            parts.append(f"Files read: {', '.join(self.files_read[:10])}")
        if self.files_edited:
            parts.append(f"Files edited: {', '.join(self.files_edited[:10])}")
        return "\n".join(parts)


def _parse_list(raw: str | None) -> list[str]:
    """Parse a JSON string array."""
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
    return json.dumps(items or [], ensure_ascii=False)


def ensure_summaries_table(conn: sqlite3.Connection) -> None:
    """Create the session_summaries table if it doesn't exist."""
    try:
        conn.executescript(SESSION_SUMMARIES_DDL)
        conn.commit()
    except sqlite3.Error as e:
        warnings.warn(f"Failed to create session_summaries table: {e}", stacklevel=2)


def store_summary(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    project: str | None = None,
    prompt_number: int | None = None,
    request: str | None = None,
    investigated: str | None = None,
    learned: str | None = None,
    completed: str | None = None,
    next_steps: str | None = None,
    files_read: list[str] | None = None,
    files_edited: list[str] | None = None,
    notes: str | None = None,
) -> int | None:
    """Store a session summary. Returns id or None on error."""
    try:
        conn.execute(
            """INSERT INTO session_summaries
               (session_id, project, prompt_number,
                request, investigated, learned, completed, next_steps,
                files_read, files_edited, notes, created_at_epoch)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                project,
                prompt_number,
                request,
                investigated,
                learned,
                completed,
                next_steps,
                _to_json_list(files_read),
                _to_json_list(files_edited),
                notes,
                int(time.time()),
            ),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except sqlite3.Error as e:
        warnings.warn(f"Failed to store session summary: {e}", stacklevel=2)
        return None


def get_recent_summaries(
    conn: sqlite3.Connection,
    limit: int = 5,
    project: str | None = None,
) -> list[SessionSummary]:
    """Get most recent session summaries, optionally filtered by project."""
    try:
        if project:
            rows = conn.execute(
                """SELECT
                    id, session_id, project, prompt_number,
                    request, investigated, learned, completed, next_steps,
                    files_read, files_edited, notes,
                    created_at, created_at_epoch
                FROM session_summaries
                WHERE project = ?
                ORDER BY created_at_epoch DESC
                LIMIT ?""",
                (project, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT
                    id, session_id, project, prompt_number,
                    request, investigated, learned, completed, next_steps,
                    files_read, files_edited, notes,
                    created_at, created_at_epoch
                FROM session_summaries
                ORDER BY created_at_epoch DESC
                LIMIT ?""",
                (limit,),
            ).fetchall()
        return [SessionSummary.from_row(r) for r in rows]
    except sqlite3.Error as e:
        warnings.warn(f"Failed to query session summaries: {e}", stacklevel=2)
        return []


def get_most_recent_summary(
    conn: sqlite3.Connection,
    project: str | None = None,
) -> SessionSummary | None:
    """Get the single most recent session summary."""
    summaries = get_recent_summaries(conn, limit=1, project=project)
    return summaries[0] if summaries else None


def render_summaries_for_context(summaries: list[SessionSummary]) -> str:
    """Render multiple summaries as a compact context block."""
    if not summaries:
        return ""
    blocks = []
    for i, s in enumerate(summaries):
        block = s.render_for_context()
        if i > 0:
            blocks.append(f"---\n{block}")
        else:
            blocks.append(block)
    return "\n".join(blocks)
