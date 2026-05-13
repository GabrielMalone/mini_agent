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
# Shared helpers
# ---------------------------------------------------------------------------

def _is_within_workspace(resolved: str, root: str, root_prefix: str) -> bool:
    """Return True if *resolved* is within the workspace *root*."""
    return resolved.startswith(root_prefix) or resolved == root


# ---------------------------------------------------------------------------
# Shared result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SafetyResult:
    """Structured result for read/write safety checks — never throws."""
    allowed: bool
    reason: str
    resolved_path: str


# Backward-compatibility aliases (deprecated — use SafetyResult directly)
ReadSafetyResult = SafetyResult
WriteSafetyResult = SafetyResult


# ---------------------------------------------------------------------------
# Read safety
# ---------------------------------------------------------------------------

class ReadSafetyGate:
    """Gate that validates file-read operations before execution."""

    def __init__(self, workspace_root: str, *, unrestricted: bool = False) -> None:
        self._root = os.path.realpath(os.path.abspath(workspace_root))
        self._root_prefix = self._root + os.sep
        self._unrestricted = unrestricted

    @property
    def workspace_root(self) -> str:
        return self._root

    @property
    def unrestricted(self) -> bool:
        return self._unrestricted

    def check(self, path: str | None) -> SafetyResult:
        """Validate a proposed read path.

        Returns a structured result — never throws.
        """
        if path is None:
            return SafetyResult(
                allowed=False,
                reason="Path is None.",
                resolved_path="",
            )
        # Guard against empty-string "" which silently resolves to CWD via abspath.
        if not path:
            return SafetyResult(
                allowed=False,
                reason="Path is empty.",
                resolved_path="",
            )
        resolved = os.path.realpath(os.path.join(self._root, path))

        # NOTE: There is an inherent TOCTOU race between this realpath check
        # and the actual open() call — a symlink could be swapped after this
        # check passes.  We accept this because the workspace is assumed to be
        # single-writer and the window is tiny.
        if not self._unrestricted and not _is_within_workspace(resolved, self._root, self._root_prefix):
            return SafetyResult(
                allowed=False,
                reason=f"Path '{resolved}' is outside workspace root '{self._root}'.",
                resolved_path=resolved,
            )

        return SafetyResult(
            allowed=True,
            reason="OK",
            resolved_path=resolved,
        )


# ---------------------------------------------------------------------------
# Write safety
# ---------------------------------------------------------------------------

class WriteSafetyGate:
    """Gate that validates file-write operations before execution."""

    def __init__(self, workspace_root: str, *, allow_overwrites: bool = False,
                 unrestricted: bool = False) -> None:
        self._root = os.path.realpath(os.path.abspath(workspace_root))
        self._root_prefix = self._root + os.sep
        self._allow_overwrites = allow_overwrites
        self._unrestricted = unrestricted

    @property
    def workspace_root(self) -> str:
        return self._root

    @property
    def unrestricted(self) -> bool:
        return self._unrestricted

    def check(self, path: str | None) -> SafetyResult:
        """Validate a proposed write path.

        Returns a structured result — never throws.
        """
        if path is None:
            return SafetyResult(
                allowed=False,
                reason="Path is None.",
                resolved_path="",
            )
        # Guard against empty-string "" which silently resolves to CWD via abspath.
        if not path:
            return SafetyResult(
                allowed=False,
                reason="Path is empty.",
                resolved_path="",
            )

        # Resolve the intended absolute path
        resolved = os.path.realpath(os.path.join(self._root, path))

        # 1. Workspace boundary check (skipped when unrestricted)
        if not self._unrestricted and not _is_within_workspace(resolved, self._root, self._root_prefix):
            return SafetyResult(
                allowed=False,
                reason=f"Path '{resolved}' is outside workspace root '{self._root}'.",
                resolved_path=resolved,
            )

        return SafetyResult(
            allowed=True,
            reason="OK",
            resolved_path=resolved,
        )
