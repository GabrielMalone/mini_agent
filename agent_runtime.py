#!/usr/bin/env python3
"""
agent_runtime.py — thread-safe sub-agent registry and result type.

Separated from sub_agent.py and tools/__init__.py to avoid circular imports.
Both modules import from here; this module imports from nothing in the project.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Structured result
# ---------------------------------------------------------------------------

@dataclass
class SubAgentResult:
    """Result returned when a sub-agent completes (or fails).

    Mirrors ToolResult so the parent can consume it like any other tool output.
    """
    success: bool
    content: str              # final answer or summary
    turns_used: int = 0
    tool_calls_made: int = 0
    scratchpad: str = ""      # final scratchpad state for parent context
    error: str | None = None

    def to_json(self) -> str:
        import json
        return json.dumps({
            "success": self.success,
            "content": self.content,
            "turns_used": self.turns_used,
            "tool_calls_made": self.tool_calls_made,
        })


# ---------------------------------------------------------------------------
# Thread-safe runtime registry (extensible)
# ---------------------------------------------------------------------------

class AgentRuntime:
    """Thread-safe registry for running sub-agent tasks.

    Designed so fields can be added later without breaking callers:
        - inboxes: dict[str, list]     (inter-agent messages)
        - deps: dict[str, list[str]]   (dependency tracking)
        - keep_alive: set[str]         (persistent agents)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.tasks: dict[str, threading.Thread] = {}
        self.results: dict[str, SubAgentResult] = {}
        self.cancel_events: dict[str, threading.Event] = {}

    # ---- spawn ----

    def register(self, task_id: str, thread: threading.Thread,
                 cancel_event: threading.Event) -> None:
        with self._lock:
            self.tasks[task_id] = thread
            self.cancel_events[task_id] = cancel_event

    def store_result(self, task_id: str, result: SubAgentResult) -> None:
        with self._lock:
            self.results[task_id] = result
            # Clean up task and cancel event to avoid memory leak
            self.tasks.pop(task_id, None)
            self.cancel_events.pop(task_id, None)

    # ---- query ----

    def get_status(self, task_id: str) -> str:
        """Return 'running', 'completed', or 'not_found'."""
        with self._lock:
            if task_id in self.results:
                return "completed"
            if task_id in self.tasks:
                return "running"
            return "not_found"

    def get_result(self, task_id: str) -> SubAgentResult | None:
        with self._lock:
            return self.results.get(task_id)

    def cancel(self, task_id: str) -> bool:
        """Request cancellation of a running sub-agent. Returns True if found."""
        with self._lock:
            event = self.cancel_events.get(task_id)
            if event is not None:
                event.set()
                return True
            return False

    def cancel_all(self) -> int:
        """Cancel all running sub-agents. Returns count of cancelled agents."""
        with self._lock:
            count = 0
            for event in self.cancel_events.values():
                if not event.is_set():
                    event.set()
                    count += 1
            return count

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for tid in self.tasks
                if tid not in self.results
            )
