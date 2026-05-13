#!/usr/bin/env python3
"""
sub_agent.py — sub-agent engine for mini_agent multi-agent support.

Provides:
    SubAgentResult  — structured result from a completed sub-agent run
    AgentRuntime    — thread-safe registry of running sub-agent tasks
    run_sub_agent   — spawns an isolated agent loop in a background thread

A sub-agent gets its own message list, tool cache, and scratchpad.
It shares the parent's workspace, safety gates, and API config.
The runtime registry is designed to be extended later for inter-agent
communication (inboxes), dependency tracking, and persistent agents.
"""

from __future__ import annotations

import threading
import uuid

from safety import ReadSafetyGate, WriteSafetyGate
from agent_runtime import SubAgentResult, AgentRuntime


# ---------------------------------------------------------------------------
# Sub-agent loop (runs in a background thread)
# ---------------------------------------------------------------------------

def run_sub_agent(
    task: str,
    config,  # AgentConfig
    write_gate: WriteSafetyGate,
    read_gate: ReadSafetyGate,
    *,
    max_turns: int = 15,
    cancel_event: threading.Event | None = None,
    parent_depth: int = 0,
    max_depth: int = 3,
    shared_context: str = "",
    stream: bool = False,
    tui_queue=None,       # Queue for TUI subagent pane streaming
    tui_task_id: str = "",  # task_id for TUI streaming prefix
    task_id: str = "",     # task_id for direct runtime lookup (avoids O(N) scan)
) -> SubAgentResult:
    """Run a sub-agent loop in the current thread (called from a background thread).

    The sub-agent gets:
    - A fresh messages list (system prompt + task as user message)
    - Its own tool cache
    - Its own _MODIFIED_FILES tracking
    - Its own scratchpad (in-memory only — no SQLite for sub-agents)

    Sub-agents CAN spawn further sub-agents up to *max_depth*.
    Current depth is *parent_depth* + 1.  Tools are blocked when at max_depth.

    Returns a SubAgentResult with success, content, and metadata.
    """
    current_depth = parent_depth + 1
    from config import AgentConfig
    from llm import call_deepseek
    from tools import (
        execute_tool, clear_tool_cache, tool_summary,
        _TOOL_CACHE, _MODIFIED_FILES, _CACHEABLE,
    )
    from tools.schema import TOOLS
    from prompt import build_system_prompt

    # --- build messages for sub-agent ---
    messages: list[dict] = [
        {"role": "system", "content": _SUB_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": build_system_prompt(config)},
    ]
    if current_depth >= max_depth:
        messages.append({
            "role": "system",
            "content": (
                f"You are at depth {current_depth}/{max_depth}. "
                "You CANNOT spawn further sub-agents — you are a leaf worker. "
                "Complete your task directly."
            ),
        })
    else:
        messages.append({
            "role": "system",
            "content": (
                f"You are at depth {current_depth}/{max_depth}. "
                "You MAY spawn sub-agents (spawn_agent) for independent subtasks. "
                "Your sub-agents will be at depth {current_depth + 1}.\n"
                "\n"
                "Orchestrator rules (same as the parent):\n"
                "- Once you spawn sub-agents, you are an orchestrator for THOSE tasks. "
                "Do NOT duplicate, pre-empt, or race the work you delegated.\n"
                "- After spawning, your job is to monitor (agent_status), collect "
                "(collect_agent/collect_any), and extend (agent_extend). You may also "
                "work on independent tasks you did NOT delegate.\n"
                "- Poll sub-agents every turn. If all are running, report status and wait.\n"
                "- Extend proactively: after ~10 turns of work, grant +10 more with "
                "agent_extend (max 35 total).\n"
                "- Use collect_any to grab the first ready result and keep the pipeline moving.\n"
                "- Only cancel if an agent repeats the same error 3+ times or exhausts 35 turns.\n"
                "- Track task IDs, what each agent is doing, and collection status "
                "in your scratchpad (write_scratchpad)."
            ),
        })
    if shared_context:
        messages.append({
            "role": "system",
            "content": (
                "Shared context from parent agent (API contracts, coordination info, etc.):\n"
                + shared_context
            ),
        })
    messages.append({"role": "user", "content": task})

    turn_count = 0
    tool_calls_made = 0
    local_modified: set[str] = set()
    local_cache: dict = {}

    # Override tool dispatch for sub-agent: block spawn_agent/agent_status/collect_agent
    # to prevent recursion.  We monkey-patch only what the sub-agent sees.

    import json
    import requests

    # main loop — uses while + dynamic max_turns re-read so parent can extend budget
    while turn_count < max_turns:
        turn_count += 1
        # Re-read max_turns from runtime (parent may have extended it)
        from tools import _TOOL_CONTEXT
        runtime_ctx = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        if runtime_ctx is not None and task_id:
            updated = runtime_ctx.get_max_turns(task_id)
            if updated is not None and updated > max_turns:
                max_turns = updated
        if cancel_event is not None and cancel_event.is_set():
            return SubAgentResult(
                success=False,
                content="Cancelled by parent.",
                turns_used=turn_count,
                tool_calls_made=tool_calls_made,
                error="Cancelled",
            )

        # Token budget awareness (same as parent loop)
        if turn_count > 1:
            from memory import _total_tokens
            estimate = _total_tokens(messages)
            budget = 64000
            pct = min(100, estimate * 100 // budget)
            messages.append({
                "role": "user",
                "content": (
                    f"[Context: ~{estimate}//{budget} tokens ({pct}%). "
                    f"Be concise if nearing limit.]"
                ),
                "_transient": True,
            })

        # Call the LLM — stream to TUI subagent pane or stderr if config.stream is set
        on_token = None
        if config.stream:
            if tui_queue is not None:
                def _on_token_sub(t: str) -> None:
                    tui_queue.put(("sub_token", tui_task_id, t))
                on_token = _on_token_sub
            else:
                import sys as _sys
                def _on_token_stderr(t: str) -> None:
                    _sys.stderr.write(t)
                    _sys.stderr.flush()
                on_token = _on_token_stderr
        msg = call_deepseek(
            messages, config,
            session=requests,
            cancel_event=cancel_event,
            on_token=on_token,
        )

        if cancel_event is not None and cancel_event.is_set():
            return SubAgentResult(
                success=False,
                content="Cancelled by parent.",
                turns_used=turn_count,
                tool_calls_made=tool_calls_made,
                error="Cancelled",
            )

        if msg is None:
            return SubAgentResult(
                success=False,
                content="No response from LLM.",
                turns_used=turn_count,
                tool_calls_made=tool_calls_made,
                error="No response",
            )

        # No tool calls → final answer
        tool_calls = msg.get("tool_calls", [])
        if not tool_calls:
            messages.append(msg)
            content = msg.get("content", "")
            return SubAgentResult(
                success=True,
                content=content[:2000],  # cap to avoid blowing parent context
                turns_used=turn_count,
                tool_calls_made=tool_calls_made,
            )

        # Execute tool calls
        messages.append(msg)
        for tc in tool_calls:
            tool_calls_made += 1
            fn = tc.get("function", {})
            name = fn.get("name", "")

            # --- Depth guard: block spawn/status/collect at max depth ---
            if current_depth >= max_depth and name in ("spawn_agent", "agent_status", "collect_agent", "collect_any", "agent_extend"):
                from tools import ToolResult as TR
                result = TR(
                    success=False,
                    content=(
                        f"Tool '{name}' is not available at max depth ({max_depth}). "
                        "Complete your assigned task directly."
                    ),
                )
            else:
                # Execute with the parent's gates (sub-agent shares workspace)
                try:
                    # Check local cache for read-only tools
                    raw_args = fn.get("arguments", "{}")
                    try:
                        parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    except json.JSONDecodeError:
                        parsed = {}

                    if name in _CACHEABLE:
                        cache_key = json.dumps([name, parsed], sort_keys=True)
                        if cache_key in local_cache:
                            result = local_cache[cache_key]
                        else:
                            result = execute_tool(tc, write_gate, read_gate)
                            local_cache[cache_key] = result
                    else:
                        result = execute_tool(tc, write_gate, read_gate)

                    # Track files modified
                    if name in ("write_file", "edit_file") and result.success:
                        filepath = parsed.get("path", "")
                        if filepath:
                            local_modified.add(filepath)
                except Exception as exc:
                    from tools import ToolResult as TR
                    result = TR(
                        success=False,
                        content=f"Tool execution error: {exc}",
                    )

            # Append tool result message
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result.to_json(),
            })

    # Exhausted turns
    return SubAgentResult(
        success=False,
        content="Sub-agent exceeded turn budget.",
        turns_used=turn_count,
        tool_calls_made=tool_calls_made,
        error="Turn budget exhausted",
    )


# ---------------------------------------------------------------------------
# Sub-agent system prompt
# ---------------------------------------------------------------------------

_SUB_AGENT_SYSTEM_PROMPT = (
    "You are a sub-agent — a worker that completes one specific task "
    "delegated to you by a parent agent.\n"
    "\n"
    "Behavior:\n"
    "- Work on the task you were given. Do not expand scope.\n"
    "- Use tools as needed to complete your work.\n"
    "- When done, produce a concise final answer summarizing what you did, "
    "what files you changed, and any results.\n"
    "- Do not ask clarifying questions — just do the work and report back.\n"
    "- If you encounter an error you cannot fix, report it clearly in your "
    "final answer rather than looping.\n"
    "- Keep your response focused and under 2000 characters.\n"
    "\n"
    "You MAY spawn sub-agents (spawn_agent) to parallelize independent "
    "subtasks. When you do, follow the same orchestrator rules as the "
    "parent: monitor, collect, and extend — but do NOT duplicate work "
    "you've delegated. Your sub-agents inherit your depth + 1.\n"
)
