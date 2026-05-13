#!/usr/bin/env python3
"""
agent_ops.py — multi-agent tools for mini_agent.

Tools: spawn_agent, agent_status, collect_agent, collect_any,
       agent_message, agent_read, agent_extend

spawn_agent launches a sub-agent in a background thread and returns
a task_id immediately (never blocks the parent).  agent_status polls
for completion.  collect_agent blocks until the sub-agent finishes
and returns the full result.  collect_any returns the first finishing
sub-agent from a set.
"""

import threading
import time
import uuid

from safety import ReadSafetyGate, WriteSafetyGate
from tools import _register, _summarize, ToolResult
from agent_runtime import AgentRuntime, SubAgentResult
from tools.agent_messages import (
    AgentMessage,
    MSG_TYPE_REGISTRY,
    _route_message,
    register_message_type,
)


# ---------------------------------------------------------------------------
# Shared state for inter-agent messaging
# ---------------------------------------------------------------------------

_AGENT_MSGS: list[dict] = []
_AGENT_MSGS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# spawn_agent
# ---------------------------------------------------------------------------

_MAX_CONCURRENT = 5          # hard cap on concurrent sub-agents
_DEFAULT_MAX_TURNS = 15      # default turn budget per sub-agent
_ABSOLUTE_MAX_TURNS = 25     # never allow more than this


def _parse_max_turns(raw) -> "int | ToolResult":
    """Parse and clamp max_turns from args."""
    try:
        mt = int(raw)
    except (TypeError, ValueError):
        return ToolResult(
            success=False,
            content=f"max_turns must be an integer, got: {raw}",
        )
    return max(1, min(mt, _ABSOLUTE_MAX_TURNS))


def _spawn_one(
    task: str,
    config,
    runtime: AgentRuntime,
    wg: WriteSafetyGate,
    rg: ReadSafetyGate,
    max_turns: int,
    *,
    cancel_event: threading.Event | None = None,
    visible: bool = False,
    shared_context: str = "",
    subscriptions: list[str] | None = None,
) -> str:
    """Spawn a single sub-agent thread. Returns the task_id."""
    from tools import _TOOL_CONTEXT
    from sub_agent import run_sub_agent

    task_id = str(uuid.uuid4())[:8]
    if cancel_event is None:
        cancel_event = threading.Event()

    def _runner() -> None:
        import sys as _sys
        tui_queue = _TOOL_CONTEXT.__dict__.get("_tui_queue")
        original_stream = config.stream
        try:
            if visible:
                config.stream = True
                if tui_queue is not None:
                    tui_queue.put(("sub_token", task_id, f"[sub {task_id}] START: {task[:80]}\n"))
                else:
                    print(f"\n  [sub {task_id}] START: {task[:80]}", file=_sys.stderr, flush=True)
            result = run_sub_agent(
                task=task,
                config=config,
                write_gate=wg,
                read_gate=rg,
                max_turns=max_turns,
                cancel_event=cancel_event,
                shared_context=shared_context,
                tui_queue=tui_queue if visible else None,
                tui_task_id=task_id if visible else "",
                task_id=task_id,
            )
            runtime.store_result(task_id, result)
            # Signal TUI to hide sub-agent streaming pane
            if tui_queue is not None:
                tui_queue.put(("sub_done", task_id))
                status = "completed" if result.success else "error"
                tui_queue.put(("sub_tree", "status", task_id, status))
        finally:
            config.stream = original_stream

    thread = threading.Thread(target=_runner, daemon=True, name=f"subagent-{task_id}")
    runtime.register(task_id, thread, cancel_event, max_turns)
    # Set subscriptions if provided
    if subscriptions is not None:
        runtime.set_subscriptions(task_id, subscriptions)
    # Push tree spawn message via the context queue
    from tools import _TOOL_CONTEXT
    tui_queue = _TOOL_CONTEXT.__dict__.get("_tui_queue")
    if tui_queue is not None:
        label = task[:60].replace("\n", " ")
        tui_queue.put(("sub_tree", "spawn", task_id, "", label))
    thread.start()
    return task_id


@_register("spawn_agent")
def _spawn_agent(args: dict, wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    """Spawn a sub-agent to work on a task in the background.

    Returns a task_id immediately.  Use agent_status to poll or
    collect_agent to block until completion.

    Supports batch spawn via 'tasks' (list of task strings) in addition
    to single 'task' spawn.
    """
    from tools import _TOOL_CONTEXT
    from agent_runtime import AgentRuntime

    # --- batch spawn (tasks=list) ---
    tasks_list = args.get("tasks", None)
    if tasks_list is not None:
        if not isinstance(tasks_list, list):
            return ToolResult(
                success=False,
                content="'tasks' must be a list of task description strings.",
            )
        valid_tasks = [t for t in tasks_list if isinstance(t, str) and t.strip()]
        if not valid_tasks:
            return ToolResult(
                success=False,
                content="No sub-agents could be spawned: 'tasks' must be a non-empty list.",
            )

        shared_context = args.get("shared_context", "")
        max_turns = _parse_max_turns(args.get("max_turns", _DEFAULT_MAX_TURNS))
        if isinstance(max_turns, ToolResult):
            return max_turns
        visible = args.get("visible", True)
        subscriptions = args.get("subscriptions", None)

        runtime: AgentRuntime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        if runtime is None:
            return ToolResult(
                success=False,
                content="Agent runtime not initialized. Multi-agent support is unavailable.",
            )

        config = _TOOL_CONTEXT.__dict__.get("_agent_config")
        if config is None:
            return ToolResult(
                success=False,
                content="Agent config not available in tool context.",
            )

        task_ids = []
        for task in valid_tasks:
            if runtime.active_count >= _MAX_CONCURRENT:
                break
            tid = _spawn_one(task, config, runtime, wg, rg, max_turns,
                             cancel_event=None, visible=visible,
                             shared_context=shared_context,
                             subscriptions=subscriptions)
            task_ids.append(tid)

        if not task_ids:
            return ToolResult(
                success=False,
                content=f"Too many sub-agents running ({runtime.active_count} active, "
                        f"max {_MAX_CONCURRENT}). Wait for some to complete before spawning more.",
            )

        return ToolResult(
            success=True,
            content=(
                f"Spawned {len(task_ids)} sub-agent(s): {', '.join(task_ids)}.\n"
                f"Use agent_status or collect_agent to check results.\n"
                f"Use collect_any() to grab the fastest completion."
            ),
        )

    # --- single task spawn ---
    task = args.get("task", "")
    if not task.strip():
        return ToolResult(
            success=False,
            content="Missing required parameter: 'task' (the task description for the sub-agent).",
        )

    max_turns = _parse_max_turns(args.get("max_turns", _DEFAULT_MAX_TURNS))
    if isinstance(max_turns, ToolResult):
        return max_turns
    visible = args.get("visible", True)
    shared_context = args.get("shared_context", "")
    subscriptions = args.get("subscriptions", None)

    runtime: AgentRuntime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
    if runtime is None:
        return ToolResult(
            success=False,
            content="Agent runtime not initialized. Multi-agent support is unavailable.",
        )

    if runtime.active_count >= _MAX_CONCURRENT:
        return ToolResult(
            success=False,
            content=(
                f"Too many sub-agents running ({runtime.active_count} active, "
                f"max {_MAX_CONCURRENT}). Wait for some to complete with "
                f"agent_status or collect_agent before spawning more."
            ),
        )

    config = _TOOL_CONTEXT.__dict__.get("_agent_config")
    if config is None:
        return ToolResult(
            success=False,
            content="Agent config not available in tool context.",
        )

    task_id = _spawn_one(task, config, runtime, wg, rg, max_turns,
                         cancel_event=None, visible=visible,
                         shared_context=shared_context,
                         subscriptions=subscriptions)

    return ToolResult(
        success=True,
        content=(
            f"Spawned sub-agent '{task_id}' with {max_turns} turn budget.\n"
            f"Task: {task[:200]}{'...' if len(task) > 200 else ''}\n"
            f"Use agent_status('{task_id}') to poll or "
            f"collect_agent('{task_id}') to block until done."
        ),
    )


@_summarize("spawn_agent")
def _spawn_agent_summary(args: dict) -> str:
    tasks_list = args.get("tasks")
    if tasks_list and isinstance(tasks_list, list):
        return f"spawn_agent(tasks=[{len(tasks_list)} items])"
    task = args.get("task", "?")
    preview = task[:60]
    if len(task) > 60:
        preview += "\u2026"
    return f"spawn_agent(\"{preview}\")"


# ---------------------------------------------------------------------------
# agent_status
# ---------------------------------------------------------------------------

@_register("agent_status")
def _agent_status(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Check the status of a sub-agent without blocking.

    Returns 'running', 'completed' with a result summary, or 'not_found'.
    """
    from tools import _TOOL_CONTEXT
    from agent_runtime import AgentRuntime

    task_id = args.get("task_id", "")
    if not task_id:
        return ToolResult(success=False, content="Missing required parameter: 'task_id'.")

    runtime: AgentRuntime | None = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
    if runtime is None:
        return ToolResult(
            success=False,
            content="Agent runtime not initialized.",
        )

    status = runtime.get_status(task_id)
    if status == "not_found":
        return ToolResult(
            success=True,
            content=f"Sub-agent '{task_id}' not found (may have completed or never existed).",
        )

    if status == "running":
        active = runtime.active_count
        return ToolResult(
            success=True,
            content=f"Sub-agent '{task_id}' is still running. ({active} total active)",
        )

    # Completed
    result = runtime.get_result(task_id)
    if result is None:
        return ToolResult(success=True, content=f"Sub-agent '{task_id}' completed but result not found.")

    summary = (
        f"Sub-agent '{task_id}': completed.\n"
        f"  Success: {result.success}\n"
        f"  Turns used: {result.turns_used}\n"
        f"  Tool calls: {result.tool_calls_made}\n"
        f"  Summary: {result.content[:500]}{'...' if len(result.content) > 500 else ''}"
    )
    if result.error:
        summary += f"\n  Error: {result.error}"

    return ToolResult(success=True, content=summary)


@_summarize("agent_status")
def _agent_status_summary(args: dict) -> str:
    return f"agent_status({args.get('task_id', '?')})"


# ---------------------------------------------------------------------------
# collect_agent
# ---------------------------------------------------------------------------

_COLLECT_TIMEOUT = 30  # seconds to wait for sub-agent completion (kept moderate
                       # so parent agent can check for user interjections between
                       # polls — use agent_extend + collect_agent again if needed)


@_register("collect_agent")
def _collect_agent(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Block until a sub-agent completes, then return its full result."""
    from tools import _TOOL_CONTEXT
    from agent_runtime import AgentRuntime

    task_id = args.get("task_id", "")
    if not task_id:
        return ToolResult(success=False, content="Missing required parameter: 'task_id'.")

    runtime: AgentRuntime | None = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
    if runtime is None:
        return ToolResult(
            success=False,
            content="Agent runtime not initialized.",
        )

    status = runtime.get_status(task_id)
    if status == "not_found":
        return ToolResult(
            success=False,
            content=f"Sub-agent '{task_id}' not found.",
        )

    if status == "running":
        # Wait for completion using condition.wait_for with predicate,
        # which atomically checks status under the lock to avoid lost wakeups.
        def _completed():
            return runtime.get_status(task_id) != "running"
        with runtime._condition:
            runtime._condition.wait_for(_completed, timeout=_COLLECT_TIMEOUT)

        # If still running after timeout, report back — don't cancel.
        # The parent can extend turns or collect again later.
        final_status = runtime.get_status(task_id)
        if final_status == "running":
            return ToolResult(
                success=False,
                content=(
                    f"Sub-agent '{task_id}' is still running after {_COLLECT_TIMEOUT}s. "
                    "It continues in the background. Use agent_status, agent_extend, "
                    "or collect_agent again to wait longer."
                ),
            )

    result = runtime.get_result(task_id)
    if result is None:
        return ToolResult(
            success=False,
            content=f"Sub-agent '{task_id}' completed but no result was stored.",
        )

    content = (
        f"Sub-agent '{task_id}' result:\n"
        f"  Success: {result.success}\n"
        f"  Turns used: {result.turns_used}\n"
        f"  Tool calls: {result.tool_calls_made}\n"
        f"  Content:\n{result.content}\n"
    )
    if result.scratchpad:
        content += f"  Scratchpad (final):\n{result.scratchpad[:500]}\n"
    if result.error:
        content += f"  Error: {result.error}\n"

    return ToolResult(
        success=result.success,
        content=content,
    )


@_summarize("collect_agent")
def _collect_agent_summary(args: dict) -> str:
    return f"collect_agent({args.get('task_id', '?')})"


# ---------------------------------------------------------------------------
# collect_any
# ---------------------------------------------------------------------------

_COLLECT_ANY_POLL = 0.2     # seconds between polls (unused, kept for reference)
_COLLECT_ANY_TIMEOUT = 10   # seconds to wait for any sub-agent (kept short so
                            # parent agent can check for user interjections between
                            # polls — the parent's natural turn cycle handles this)


@_register("collect_any")
def _collect_any(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Collect the first sub-agent that finishes.

    If any have already completed, returns immediately.
    Otherwise polls until one completes or timeout.
    """
    from tools import _TOOL_CONTEXT
    from agent_runtime import AgentRuntime

    runtime: AgentRuntime | None = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
    if runtime is None:
        return ToolResult(
            success=False,
            content="Agent runtime not initialized.",
        )

    task_ids = args.get("task_ids", None)
    if task_ids is not None and not isinstance(task_ids, list):
        return ToolResult(
            success=False,
            content="'task_ids' must be a list of task ID strings.",
        )

    # Determine which tasks to check
    if task_ids:
        candidates = task_ids
    else:
        with runtime._lock:
            # Include both running tasks and completed results
            candidates = set(runtime.tasks.keys()) | set(runtime.results.keys())

    if not candidates:
        return ToolResult(
            success=False,
            content="No sub-agents to collect.",
        )

    # Check for already-completed
    candidates = list(candidates)  # materialize for iteration
    for tid in candidates:
        status = runtime.get_status(tid)
        if status == "completed":
            result = runtime.get_result(tid)
            if result is not None:
                return _format_collect_any(tid, result)

    # Wait for any completion using condition.wait_for with predicate,
    # which atomically checks status under the lock to avoid lost wakeups.
    def _any_completed():
        for tid in candidates:
            if runtime.get_status(tid) == "completed":
                return True
        return False
    with runtime._condition:
        runtime._condition.wait_for(_any_completed, timeout=_COLLECT_ANY_TIMEOUT)

    for tid in candidates:
        status = runtime.get_status(tid)
        if status == "completed":
            result = runtime.get_result(tid)
            if result is not None:
                return _format_collect_any(tid, result)

    # Report which sub-agents are still running so the parent can retry
    still_running = [tid for tid in candidates if runtime.get_status(tid) == "running"]
    return ToolResult(
        success=False,
        content=(
            f"No sub-agent completed within {_COLLECT_ANY_TIMEOUT}s. "
            f"Still running: {still_running if still_running else 'none'}. "
            "Use collect_any again to retry."
        ),
    )


def _format_collect_any(task_id: str, result: SubAgentResult) -> ToolResult:
    """Format a collected sub-agent result."""
    content = (
        f"Sub-agent '{task_id}' finished first:\n"
        f"  Success: {result.success}\n"
        f"  Turns used: {result.turns_used}\n"
        f"  Tool calls: {result.tool_calls_made}\n"
        f"  Content:\n{result.content}\n"
    )
    if result.error:
        content += f"  Error: {result.error}\n"
    return ToolResult(success=result.success, content=content)


@_summarize("collect_any")
def _collect_any_summary(args: dict) -> str:
    tids = args.get("task_ids")
    if tids:
        return f"collect_any([{len(tids)} ids])"
    return "collect_any()"


# ---------------------------------------------------------------------------
# agent_message
# ---------------------------------------------------------------------------

@_register("agent_message")
def _agent_message(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Broadcast a message visible to parent and sibling sub-agents."""
    from tools import _TOOL_CONTEXT

    text = args.get("text", "")
    if not text:
        return ToolResult(
            success=False,
            content="Missing required parameter: 'text'.",
        )
    sender = args.get("from", "")

    # Create typed AgentMessage for routing
    try:
        msg = AgentMessage(
            type="text",
            sender=sender,
            payload={"body": text},
        )
    except ValueError as exc:
        return ToolResult(
            success=False,
            content=f"Invalid message: {exc}",
        )

    # Append to legacy flat list (backward compat)
    with _AGENT_MSGS_LOCK:
        _AGENT_MSGS.append(msg.to_legacy_dict())
        count = len(_AGENT_MSGS)

    # Route to subscribed inboxes
    runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
    if runtime is not None:
        _route_message(
            msg,
            runtime.inboxes,
            runtime.subscriptions,
            runtime._lock,
            target=None,
        )

    return ToolResult(
        success=True,
        content=f"Message broadcast. ({count} total messages)",
    )


@_summarize("agent_message")
def _agent_message_summary(args: dict) -> str:
    text = args.get("text", "?")
    preview = text[:50]
    if len(text) > 50:
        preview += "\u2026"
    return f"agent_message(\"{preview}\")"


# ---------------------------------------------------------------------------
# agent_read
# ---------------------------------------------------------------------------

@_register("agent_read")
def _agent_read(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Read broadcast messages from other sub-agents and the parent.

    Returns messages in chronological order.  Use 'since' to only
    get messages with index >= that value (for polling).
    """
    since = args.get("since", None)
    if since is not None:
        try:
            since = int(since)
        except (TypeError, ValueError):
            return ToolResult(
                success=False,
                content="'since' must be an integer index.",
            )

    with _AGENT_MSGS_LOCK:
        if since is not None:
            msgs = _AGENT_MSGS[since:]
        else:
            msgs = list(_AGENT_MSGS)

    if not msgs:
        return ToolResult(
            success=True,
            content="No new messages.",
        )

    lines = []
    base_idx = since if since is not None else 0
    for i, m in enumerate(msgs):
        idx = base_idx + i
        sender = f" from={m['from']}" if m.get("from") else ""
        lines.append(f"[{idx}]{sender} {m['text']}")

    return ToolResult(
        success=True,
        content="\n".join(lines),
    )


@_summarize("agent_read")
def _agent_read_summary(args: dict) -> str:
    since = args.get("since")
    if since is not None:
        return f"agent_read(since={since})"
    return "agent_read()"


# ---------------------------------------------------------------------------
# agent_handoff
# ---------------------------------------------------------------------------

@_register("agent_handoff")
def _agent_handoff(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Produce a typed result and route it to subscribed agents.

    Parameters:
        type: str              — message type (default \"handoff.result\")
        result: dict           — structured result payload
        correlation_id: str    — optional correlation ID
        target: str | None     — if set, deliver only to this task_id
    """
    from tools import _TOOL_CONTEXT

    msg_type = args.get("type", "handoff.result")
    if msg_type not in MSG_TYPE_REGISTRY:
        return ToolResult(
            success=False,
            content=f"Unknown handoff message type: {msg_type!r}. "
                    f"Use a registered type like 'handoff.result', 'handoff.ack', etc.",
        )

    result_payload = args.get("result", None)
    if result_payload is None:
        return ToolResult(
            success=False,
            content="Missing required parameter: 'result'.",
        )
    if not isinstance(result_payload, dict):
        return ToolResult(
            success=False,
            content="'result' must be a dict.",
        )

    correlation_id = args.get("correlation_id", None)
    target = args.get("target", None)
    sender = args.get("from", "")

    # Build the payload according to type
    payload = {}
    if msg_type == "handoff.result":
        payload = {"result": result_payload, "task": str(result_payload)}
    elif msg_type == "handoff.request":
        payload = {"task": str(result_payload), "input_schema": result_payload}
    elif msg_type == "handoff.ack":
        payload = {"accepted": bool(result_payload.get("accepted", True)),
                    "reason": str(result_payload.get("reason", ""))}
    elif msg_type == "status.heartbeat":
        payload = {"progress": str(result_payload.get("progress", "")),
                    "pct": float(result_payload.get("pct", 0))}
    elif msg_type == "status.error":
        payload = {"error": str(result_payload.get("error", "")),
                    "phase": str(result_payload.get("phase", ""))}
    elif msg_type == "coord.fan_out":
        payload = {"items": result_payload, "worker_type": str(result_payload.get("worker_type", ""))}
    elif msg_type == "coord.fan_in":
        payload = {"results": result_payload, "worker_count": int(result_payload.get("worker_count", 0))}
    elif msg_type == "coord.sync":
        payload = {"barrier": str(result_payload.get("barrier", "")),
                    "arrived": int(result_payload.get("arrived", 1)),
                    "total": int(result_payload.get("total", 1))}

    try:
        msg = AgentMessage(
            type=msg_type,
            sender=sender,
            payload=payload,
            correlation_id=correlation_id,
        )
    except ValueError as exc:
        return ToolResult(
            success=False,
            content=f"Invalid handoff message: {exc}",
        )

    # Append to legacy flat list (backward compat)
    with _AGENT_MSGS_LOCK:
        _AGENT_MSGS.append(msg.to_legacy_dict())
        count = len(_AGENT_MSGS)

    # Route to subscribed inboxes
    runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
    if runtime is not None:
        _route_message(
            msg,
            runtime.inboxes,
            runtime.subscriptions,
            runtime._lock,
            target=target,
        )

    target_info = f" to '{target}'" if target else ""
    return ToolResult(
        success=True,
        content=f"Handoff {msg_type!r} sent{target_info}. ({count} total messages)",
    )


@_summarize("agent_handoff")
def _agent_handoff_summary(args: dict) -> str:
    msg_type = args.get("type", "handoff.result")
    return f"agent_handoff(type=\"{msg_type}\")"


# ---------------------------------------------------------------------------
# agent_inbox
# ---------------------------------------------------------------------------

@_register("agent_inbox")
def _agent_inbox(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Read the typed inbox for a specific agent (task_id).

    Use 'since' to only get messages with index >= that value (for polling).
    """
    from tools import _TOOL_CONTEXT

    task_id = args.get("task_id", "")
    if not task_id:
        return ToolResult(
            success=False,
            content="Missing required parameter: 'task_id'.",
        )

    since = args.get("since", None)
    if since is not None:
        try:
            since = int(since)
        except (TypeError, ValueError):
            return ToolResult(
                success=False,
                content="'since' must be an integer index.",
            )

    runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
    if runtime is None:
        return ToolResult(
            success=False,
            content="Agent runtime not initialized.",
        )

    inbox = runtime.get_inbox(task_id)
    if inbox is None:
        return ToolResult(
            success=False,
            content=f"Sub-agent '{task_id}' not found.",
        )
    if since is not None:
        inbox = inbox[since:]

    if not inbox:
        return ToolResult(
            success=True,
            content="No new messages in inbox.",
        )

    lines = []
    for i, msg in enumerate(inbox):
        idx = (since if since is not None else 0) + i
        lines.append(f"[{idx}] [{msg.type}] from={msg.sender}: {msg.payload}")

    return ToolResult(
        success=True,
        content="\n".join(lines),
    )


@_summarize("agent_inbox")
def _agent_inbox_summary(args: dict) -> str:
    since = args.get("since")
    if since is not None:
        return f"agent_inbox({args.get('task_id', '?')}, since={since})"
    return f"agent_inbox({args.get('task_id', '?')})"


# ---------------------------------------------------------------------------
# agent_subscribe
# ---------------------------------------------------------------------------

@_register("agent_subscribe")
def _agent_subscribe(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Declare or update message type subscriptions for an agent at runtime.

    Parameters:
        task_id: str       — the agent to update
        types: list[str]   — message types to subscribe to (empty = all)
    """
    from tools import _TOOL_CONTEXT

    task_id = args.get("task_id", "")
    if not task_id:
        return ToolResult(
            success=False,
            content="Missing required parameter: 'task_id'.",
        )

    types = args.get("types", None)
    if types is not None and not isinstance(types, list):
        return ToolResult(
            success=False,
            content="'types' must be a list of message type strings.",
        )

    runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
    if runtime is None:
        return ToolResult(
            success=False,
            content="Agent runtime not initialized.",
        )

    if runtime.get_status(task_id) == "not_found":
        return ToolResult(
            success=False,
            content=f"Sub-agent '{task_id}' not found.",
        )

    if types is None:
        # Default: subscribe to all (clear subscriptions)
        runtime.set_subscriptions(task_id, [])
        return ToolResult(
            success=True,
            content=f"Sub-agent '{task_id}' now receives all message types (default).",
        )

    # Validate types
    unknown = [t for t in types if t not in MSG_TYPE_REGISTRY]
    if unknown:
        return ToolResult(
            success=False,
            content=f"Unknown message type(s): {unknown}. "
                    f"Known types: {sorted(MSG_TYPE_REGISTRY.keys())}",
        )

    runtime.set_subscriptions(task_id, types)
    return ToolResult(
        success=True,
        content=f"Sub-agent '{task_id}' subscribed to: {types}",
    )


@_summarize("agent_subscribe")
def _agent_subscribe_summary(args: dict) -> str:
    types = args.get("types", [])
    return f"agent_subscribe({args.get('task_id', '?')}, types={types})"


# ---------------------------------------------------------------------------
# agent_extend
# ---------------------------------------------------------------------------

@_register("agent_extend")
def _agent_extend(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Extend the turn budget of a running sub-agent."""
    from tools import _TOOL_CONTEXT
    from agent_runtime import AgentRuntime

    task_id = args.get("task_id", "")
    if not task_id:
        return ToolResult(
            success=False,
            content="Missing required parameter: 'task_id'.",
        )

    additional = args.get("additional", 10)
    try:
        additional = int(additional)
    except (TypeError, ValueError):
        return ToolResult(
            success=False,
            content=f"'additional' must be an integer, got: {additional}",
        )
    if additional < 1:
        return ToolResult(
            success=False,
            content="'additional' must be a positive integer.",
        )

    runtime: AgentRuntime | None = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
    if runtime is None:
        return ToolResult(
            success=False,
            content="Agent runtime not initialized.",
        )

    status = runtime.get_status(task_id)
    if status == "not_found":
        return ToolResult(
            success=False,
            content=f"Sub-agent '{task_id}' not found.",
        )

    if status == "completed":
        return ToolResult(
            success=True,
            content=f"Sub-agent '{task_id}' has already completed.",
        )

    ok = runtime.extend_turns(task_id, additional)
    if not ok:
        return ToolResult(
            success=False,
            content=f"Failed to extend turns for '{task_id}'.",
        )

    new_max = runtime.get_max_turns(task_id)
    return ToolResult(
        success=True,
        content=f"Extended sub-agent '{task_id}' by +{additional} turns "
                f"(new max: {new_max}).",
    )


@_summarize("agent_extend")
def _agent_extend_summary(args: dict) -> str:
    return f"agent_extend({args.get('task_id', '?')}, +{args.get('additional', 10)})"
