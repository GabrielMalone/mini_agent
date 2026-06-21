#!/usr/bin/env python3
"""
prefix.py -- Immutable prefix cache for DeepSeek prefix-cache stability.

Reasonix Pillar 1: Cache-First Loop.

DeepSeek's automatic prefix caching activates when the EXACT byte prefix
of the current request matches the previous request.  Most agent loops
break this by reordering, rewriting, or injecting fresh context each turn.

This module partitions the prompt into three zones:

  ┌──────────────────────────────┐
  │ IMMUTABLE PREFIX              │ ← system + tool_specs + few_shots
  │   hashed once, never changes  │   pinned for session lifetime
  ├──────────────────────────────┤
  │ APPEND-ONLY LOG               │ ← conversation turns [asst₁][tool₁]...
  │   grows monotonically         │   never rewritten or reordered
  ├──────────────────────────────┤
  │ VOLATILE SCRATCH              │ ← transient context (_transient msgs)
  │   reset each turn             │   NEVER sent upstream
  └──────────────────────────────┘

Invariants:
  1. Prefix is hashed once (SHA-256), pinned for the session.
  2. Log entries are strictly append-only.
  3. Scratch is distilled through compaction before folding into the log.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ImmutablePrefix:
    """System prompt + tool specs + few-shot examples. Frozen at session start.

    Any change to the prefix changes the SHA-256 fingerprint, which
    invalidates DeepSeek's prefix cache.  Build this ONCE and reuse
    it for every API call in the session.
    """

    system: str
    tool_specs: list[dict]     # serialized with sort_keys=True
    few_shots: list[dict] = field(default_factory=list)

    _hash: str = field(init=False, default="")

    def __post_init__(self) -> None:
        raw = json.dumps(
            {
                "system": self.system,
                "tools": self.tool_specs,
                "few_shots": self.few_shots,
            },
            sort_keys=True,
            ensure_ascii=True,
        )
        object.__setattr__(self, "_hash", hashlib.sha256(raw.encode()).hexdigest())

    @property
    def fingerprint(self) -> str:
        """64-char SHA-256 hex digest of the canonical prefix bytes."""
        return self._hash

    @property
    def short_fingerprint(self) -> str:
        """First 8 chars of fingerprint for display."""
        return self._hash[:8] if self._hash else "none"

    def to_message(self) -> dict:
        """Return a single system message representing the prefix."""
        return {"role": "system", "content": self.system}


class SessionPrefixCache:
    """Per-session manager for the immutable prefix.

    Tracks the prefix fingerprint across turns so we can detect drift
    (e.g. if tool specs change mid-session, invalidating the cache).
    """

    def __init__(self) -> None:
        self._prefix: ImmutablePrefix | None = None
        self._lock = threading.Lock()
        self._fingerprint_history: list[str] = []  # for debugging

    @property
    def prefix(self) -> ImmutablePrefix | None:
        return self._prefix

    @property
    def is_established(self) -> bool:
        return self._prefix is not None

    def establish(self, system: str, tool_specs: list[dict], few_shots: list[dict] | None = None) -> ImmutablePrefix:
        """Set the immutable prefix for this session. Call once at startup.

        Returns the new prefix.  If called again with the same content,
        returns the existing prefix (no-op).  If called with different
        content, the fingerprint changes (cache is invalidated).
        """
        new_prefix = ImmutablePrefix(
            system=system,
            tool_specs=tool_specs,
            few_shots=few_shots or [],
        )
        with self._lock:
            if self._prefix is not None and self._prefix.fingerprint == new_prefix.fingerprint:
                return self._prefix  # no change
            old_fp = self._prefix.fingerprint if self._prefix else "none"
            self._prefix = new_prefix
            self._fingerprint_history.append(old_fp)
            # Cap history
            if len(self._fingerprint_history) > 32:
                self._fingerprint_history = self._fingerprint_history[-32:]
        return new_prefix

    def fingerprint_changed(self) -> bool:
        """True if the prefix fingerprint changed since last establish()."""
        return len(self._fingerprint_history) > 0 and self._fingerprint_history[-1] != "none"

    def build_system_message(self, config: Any) -> str:
        """Build the canonical system prompt string.

        Extracted from api.py/call_llm so we have a single source of truth.
        The caller should pass get_active_tools() result as tool_specs.
        """
        from core.prompt import build_system_prompt
        system = build_system_prompt(config)
        if hasattr(config, "system_extension") and config.system_extension:
            system += "\n\n" + config.system_extension
        return system


# Module-level singleton (shared across the Python process).
# Call SessionPrefixCache.establish() once from bootstrap/init_session.
_session_prefix_cache = SessionPrefixCache()


def get_session_prefix_cache() -> SessionPrefixCache:
    """Return the module-level SessionPrefixCache singleton."""
    return _session_prefix_cache


def get_prefix_fingerprint() -> str | None:
    """Return the current prefix fingerprint, or None if not established."""
    p = _session_prefix_cache.prefix
    return p.fingerprint if p else None
