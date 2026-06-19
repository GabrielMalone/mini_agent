#!/usr/bin/env python3
"""
context.py -- agent context and thread-safe context-variable proxy.

Extracted from tools/__init__.py to keep the dispatch module focused.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field


# Context keys used across tools and llm
CTX_SCRATCHPAD_PATH = "scratchpad_path"
CTX_SCRATCHPAD_UPDATED = "_scratchpad_updated"
CTX_TURN_HISTORY = "_turn_history"  # dict[int, str] -- turn number -> summary
CTX_PLAN_STEPS = "_plan_steps"      # list[str] -- from plan tool
CTX_PLAN_DONE = "_plan_done"        # set[int] -- completed step indices
CTX_PLAN_LAST_ADVANCED = "_plan_last_advanced_turn"  # int -- turn when last step advanced


@dataclass
class AgentContext:
    """Mutable context shared across tools and the agent loop.

    Initialized once at startup via ``set_context()``, then read/written
    by tools and ``llm.py`` through the ``_TOOL_CONTEXT`` proxy.

    All fields are declared as dataclass fields with type annotations and
    safe defaults, making every context field discoverable without grepping
    for ``hasattr`` / ``getattr`` guards.
    """

    # -- Core session state (set via set_context) --
    scratchpad_path: str | None = None
    exa_api_key: str | None = None
    openai_api_key: str | None = None
    workspace: str | None = None
    discord_guild_id: int | None = None
    discord_token: str | None = None

    # -- Plan tracking --
    _plan_steps: list[str] = field(default_factory=list)
    _plan_done: set[int] = field(default_factory=set)
    _plan_last_advanced_turn: int = 0

    # -- Turn history & counters --
    _turn_history: dict[int, str] = field(default_factory=dict)
    _turn_count: int = 0
    _min_turn: int = 0
    _consecutive_read_only_turns: int = 0
    _last_msg_count: int = 0
    _system_reminder_last_msg_count: int = 0

    # -- Scratchpad (SQLite-backed) --
    _scratchpad_updated: bool = False
    _scratchpad_injected: bool = False

    # -- One-time context injection flags --
    _git_diff_injected: bool = False
    _handoff_injected: bool = False
    _state_txt_injected: bool = False
    _tasks_injected: bool = False
    _core_memory_injected: bool = False
    _session_summary_injected: bool = False

    # -- Git state --
    _session_start_head: str | None = None
    _session_id: str | None = None

    # -- Stores & services (set by init_session / agent loop) --
    _memory_store: object | None = None          # MemoryStore
    _failure_pattern_store: object | None = None  # FailurePatternStore
    _self_critique: object | None = None          # SelfCritique
    _tool_graph: object | None = None             # ToolGraph
    _mistake_notebook: object | None = None       # MistakeNotebook
    _agent_runtime: object | None = None          # AgentRuntime (sub-agent orchestration)
    _agent_config: object | None = None           # AgentConfig
    _agent_depth: int = 0                          # recursion guard for sub-agents
    _read_gate: object | None = None              # ReadSafetyGate
    _provider: str = "deepseek"                    # API provider label

    # -- Sub-agent callback (Electron IPC bridge) --
    _subagent_callback: object | None = None  # callable | None

    # -- Orchestration / verification state --
    _last_orch_state: str | None = None
    _last_pending_reported: int = 0
    _last_verification_turn: int = 0
    _last_verified_modified: set[str] = field(default_factory=set)
    _confidence_nudge_last_turn: int = 0

    # -- Cache telemetry (set by api.py) --
    _cache_stats: dict | None = None
    _semantic_cache_stats: dict | None = None
    _cache_turn_history: list = field(default_factory=list)
    _cache_alert_last_turn: int = 0

    # -- Failure pattern learning (in-memory) --
    _failure_patterns: dict = field(default_factory=dict)


_TOOL_CONTEXT_VAR: contextvars.ContextVar[AgentContext] = contextvars.ContextVar(
    "tool_context", default=AgentContext()
)


class _ContextProxy:
    """Proxy that transparently delegates attribute access to the current
    ``AgentContext`` inside a ``ContextVar``.  Each thread / async task
    gets its own copy, so concurrent tool execution (background shells,
    sub-agents, etc.) cannot cross-contaminate context state."""

    __slots__ = ("_cv",)

    def __init__(self, cv: contextvars.ContextVar):
        super().__setattr__("_cv", cv)

    def __getattr__(self, name: str):
        return getattr(self._cv.get(), name)

    def __setattr__(self, name: str, value):
        if name == "_cv":
            super().__setattr__(name, value)
        else:
            setattr(self._cv.get(), name, value)

    def __delattr__(self, name: str):
        try:
            delattr(self._cv.get(), name)
        except AttributeError:
            pass  # Best-effort: attribute may be a class-level default already deleted

    @property
    def __dict__(self):
        return self._cv.get().__dict__

    def get(self) -> AgentContext:
        """Explicit accessor for the raw ``AgentContext`` (rarely needed)."""
        return self._cv.get()


_TOOL_CONTEXT = _ContextProxy(_TOOL_CONTEXT_VAR)


# P1.4: Dispatch mapping for set_context -- replaces if/elif chain
_CTX_DISPATCH = {
    "scratchpad_path": lambda ctx, v: setattr(ctx, "scratchpad_path", v),
    "exa_api_key": lambda ctx, v: setattr(ctx, "exa_api_key", v),
    "openai_api_key": lambda ctx, v: setattr(ctx, "openai_api_key", v),
    "workspace": lambda ctx, v: setattr(ctx, "workspace", v),
    "discord_guild_id": lambda ctx, v: setattr(ctx, "discord_guild_id", v),
    "discord_token": lambda ctx, v: setattr(ctx, "discord_token", v),
}


def set_context(**kwargs) -> None:
    """Set module-level context accessible to tool implementations."""
    ctx = _TOOL_CONTEXT
    for key, value in kwargs.items():
        handler = _CTX_DISPATCH.get(key)
        if handler is not None:
            handler(ctx, value)
        else:
            setattr(ctx, key, value)
