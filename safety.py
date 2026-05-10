#!/usr/bin/env python3
"""
safety.py — file-read and file-write safety layer for mini_agent.

Enforces:
    1. All reads/writes must land inside a configured workspace root.
    2. Overwrites trigger a confirmation check (unless explicitly allowed).
    3. All results are returned as structured dataclasses — never raw exceptions.
"""

import os
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Read safety
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReadSafetyResult:
    allowed: bool
    reason: str
    resolved_path: str


class ReadSafetyGate:
    """Gate that validates file-read operations before execution."""

    def __init__(self, workspace_root: str) -> None:
        self._root = os.path.realpath(os.path.abspath(workspace_root))

    @property
    def workspace_root(self) -> str:
        return self._root

    def check(self, path: str) -> ReadSafetyResult:
        """Validate a proposed read path.

        Returns a structured result — never throws.
        """
        resolved = os.path.realpath(os.path.abspath(path))

        if not resolved.startswith(self._root + os.sep) and resolved != self._root:
            return ReadSafetyResult(
                allowed=False,
                reason=f"Path '{resolved}' is outside workspace root '{self._root}'.",
                resolved_path=resolved,
            )

        return ReadSafetyResult(
            allowed=True,
            reason="OK",
            resolved_path=resolved,
        )


# ---------------------------------------------------------------------------
# Write safety
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WriteSafetyResult:
    allowed: bool
    reason: str
    resolved_path: str


class WriteSafetyGate:
    """Gate that validates file-write operations before execution."""

    def __init__(self, workspace_root: str, *, allow_overwrites: bool = False) -> None:
        self._root = os.path.realpath(os.path.abspath(workspace_root))
        self._allow_overwrites = allow_overwrites

    @property
    def workspace_root(self) -> str:
        return self._root

    def check(self, path: str) -> WriteSafetyResult:
        """Validate a proposed write path.

        Returns a structured result — never throws.
        """
        # Resolve the intended absolute path
        resolved = os.path.realpath(os.path.abspath(path))

        # 1. Workspace boundary check
        if not resolved.startswith(self._root + os.sep) and resolved != self._root:
            return WriteSafetyResult(
                allowed=False,
                reason=f"Path '{resolved}' is outside workspace root '{self._root}'.",
                resolved_path=resolved,
            )

        # 2. Overwrite check (only for existing files, not directories)
        if os.path.isfile(resolved) and not self._allow_overwrites:
            return WriteSafetyResult(
                allowed=False,
                reason=f"File '{resolved}' already exists and overwrites are not permitted. "
                        "Set allow_overwrites=True to bypass.",
                resolved_path=resolved,
            )

        return WriteSafetyResult(
            allowed=True,
            reason="OK",
            resolved_path=resolved,
        )
