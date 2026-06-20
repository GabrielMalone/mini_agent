#!/usr/bin/env python3
"""checkpoint.py -- Git-based checkpoint system for mini_agent.

Creates git commits before risky operations (write_file, edit_file,
dangerous run_shell), enabling instant rollback if something goes wrong.

Inspired by Dirac's CheckpointManager.  Falls back gracefully to
file-level _BACKUPS when git is unavailable.

Architecture:
    CheckpointManager  -- per-workspace git checkpoint lifecycle
    checkpoint()       -- create a labeled git commit (no-op if clean)
    restore_file()     -- restore single file to last checkpoint
    restore_all()      -- restore entire workspace to last checkpoint
    list_checkpoints() -- enumerate recent checkpoint commits
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Checkpoint:
    """A single git checkpoint snapshot."""

    sha: str
    message: str
    timestamp: float


class CheckpointManager:
    """Per-workspace git checkpoint lifecycle.

    Thread-safe.  Use get_checkpoint_manager() to get the singleton.
    """

    # Class-level storage: workspace_root -> CheckpointManager
    _instances: dict[str, CheckpointManager] = {}
    _lock = threading.Lock()

    # Maximum checkpoints to retain in the log
    MAX_CHECKPOINTS = 50

    @classmethod
    def get(cls, workspace_root: str) -> CheckpointManager:
        """Get or create the singleton CheckpointManager for a workspace."""
        # Normalize: resolve symlinks (macOS /var -> /private/var) so all
        # path representations map to the same CheckpointManager instance.
        normalized = os.path.realpath(os.path.abspath(workspace_root))
        with cls._lock:
            if normalized not in cls._instances:
                cls._instances[normalized] = cls(normalized)
            return cls._instances[normalized]

    @classmethod
    def reset(cls) -> None:
        """Reset all checkpoint managers (for testing)."""
        with cls._lock:
            cls._instances.clear()

    def __init__(self, workspace_root: str) -> None:
        self.workspace_root = os.path.abspath(workspace_root)
        self._checkpoints: list[Checkpoint] = []
        self._checked_this_turn: bool = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Git detection
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Check if git is available and workspace is a git repo."""
        if not shutil.which("git"):
            return False
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _is_clean(self) -> bool:
        """Check if the working tree is clean (no uncommitted changes)."""
        try:
            result = subprocess.run(
                ["git", "diff-index", "--quiet", "HEAD", "--"],
                cwd=self.workspace_root,
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return True  # assume clean on error

    # ------------------------------------------------------------------
    # Checkpoint lifecycle
    # ------------------------------------------------------------------

    def checkpoint(self, label: str = "") -> str | None:
        """Create a git checkpoint commit.  Returns commit SHA or None.

        If the working tree is clean (no changes), this is a no-op.
        Thread-safe: only one checkpoint per turn is created
        (subsequent calls within the same turn are ignored via
        _checked_this_turn).

        On failure (no git, dirty submodules, etc.), returns None
        gracefully -- the system falls back to _BACKUPS per-file undo.
        """
        if self._checked_this_turn:
            return None

        if not self.is_available():
            return None

        with self._lock:
            # Double-check: another thread may have set the flag
            if self._checked_this_turn:
                return None

            try:
                # Stage everything (including untracked files)
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=self.workspace_root,
                    capture_output=True,
                    timeout=15,
                    check=True,
                )

                # Only commit if there are staged changes
                diff_result = subprocess.run(
                    ["git", "diff", "--cached", "--quiet"],
                    cwd=self.workspace_root,
                    capture_output=True,
                    timeout=10,
                )

                if diff_result.returncode == 0:
                    # No changes to commit -- still mark as checked
                    self._checked_this_turn = True
                    return None

                # Build a descriptive commit message
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                message = f"[mini_agent checkpoint] {label} ({ts})"

                result = subprocess.run(
                    ["git", "commit", "-m", message, "--no-verify"],
                    cwd=self.workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )

                if result.returncode != 0:
                    self._checked_this_turn = True
                    return None

                # Extract SHA from commit output
                sha_result = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=self.workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                sha = sha_result.stdout.strip() if sha_result.returncode == 0 else "unknown"

                cp = Checkpoint(
                    sha=sha,
                    message=label,
                    timestamp=time.time(),
                )
                self._checkpoints.append(cp)

                # Prune old checkpoints from log
                if len(self._checkpoints) > self.MAX_CHECKPOINTS:
                    self._checkpoints = self._checkpoints[-self.MAX_CHECKPOINTS:]

                self._checked_this_turn = True
                return sha

            except Exception:
                self._checked_this_turn = True
                return None

    def reset_turn(self) -> None:
        """Reset the per-turn checkpoint flag (call at start of each turn)."""
        self._checked_this_turn = False

    # ------------------------------------------------------------------
    # Restore operations
    # ------------------------------------------------------------------

    def restore_file(self, path: str) -> bool:
        """Restore a single file to its state at the last checkpoint.

        Uses ``git checkout <path>`` to revert the file.  Returns True
        if the file was restored, False otherwise.
        """
        if not self.is_available():
            return False

        try:
            result = subprocess.run(
                ["git", "checkout", "--", path],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def restore_all(self) -> bool:
        """Restore the entire workspace to the last checkpoint.

        Uses ``git checkout .`` followed by ``git clean -fd`` to
        remove untracked files.  Returns True on success.
        """
        if not self.is_available():
            return False

        try:
            # Revert tracked files
            result = subprocess.run(
                ["git", "checkout", "."],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return False

            # Clean untracked files
            result = subprocess.run(
                ["git", "clean", "-fd"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_checkpoints(self) -> list[dict]:
        """Return recent checkpoint entries as a list of dicts."""
        with self._lock:
            return [
                {
                    "sha": cp.sha,
                    "message": cp.message,
                    "timestamp": cp.timestamp,
                }
                for cp in self._checkpoints[-20:]  # last 20
            ]

    def last_checkpoint_sha(self) -> str | None:
        """Return the SHA of the most recent checkpoint, or None."""
        with self._lock:
            if self._checkpoints:
                return self._checkpoints[-1].sha
            return None

    def checkpoint_count(self) -> int:
        """Return the number of checkpoints created this session."""
        with self._lock:
            return len(self._checkpoints)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

import shutil  # noqa: E402 (import at top for clarity, but needed here)


def get_checkpoint_manager(workspace_root: str) -> CheckpointManager:
    """Get the singleton CheckpointManager for a workspace."""
    # Normalize path so callers with different representations
    # (e.g. /var vs /private/var on macOS) hit the same instance.
    normalized = os.path.realpath(os.path.abspath(workspace_root))
    return CheckpointManager.get(normalized)


def checkpoint_before_risky(workspace_root: str, label: str = "") -> str | None:
    """Convenience: create a checkpoint before a risky operation."""
    cm = get_checkpoint_manager(workspace_root)
    return cm.checkpoint(label)


def reset_turn_checkpoint(workspace_root: str) -> None:
    """Reset the per-turn checkpoint flag."""
    cm = get_checkpoint_manager(workspace_root)
    cm.reset_turn()
