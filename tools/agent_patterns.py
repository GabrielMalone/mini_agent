#!/usr/bin/env python3
"""
agent_patterns.py — multi-agent coordination pattern helpers.

Provides Python-API helpers (not tools) that the parent agent or
orchestrator can call to coordinate sub-agents:

    fan_out()     — spawn N workers from a list of task descriptions
    fan_in()      — collect all results from a list of task_ids
    pipeline()    — run stages in sequence, each receiving the prior's handoff
    barrier()     — block until all task_ids have sent coord.sync for a barrier
    scatter_gather() — fan-out with per-worker input slices
"""

from __future__ import annotations

import time
import threading

from agent_runtime import AgentRuntime, SubAgentResult


def fan_out(
    descriptions: list[str],
    shared_input: dict | None = None,
    runtime: AgentRuntime | None = None,
    config=None,
    wg=None,
    rg=None,
    max_turns: int = 15,
    visible: bool = False,
    subscriptions: list[str] | None = None,
) -> list[str]:
    """Spawn N workers from a list of task descriptions.

    Returns a list of task_ids that can be passed to fan_in().

    Args:
        descriptions: List of task strings, one per worker.
        shared_input: Optional dict passed as shared_context to all workers.
        runtime: AgentRuntime instance (pulled from _TOOL_CONTEXT if None).
        config: AgentConfig instance.
        wg, rg: Safety gates.
        max_turns: Turn budget per worker.
        visible: Stream sub-agent output.
        subscriptions: Message types each worker subscribes to.

    Returns:
        List of task_id strings.
    """
    from tools import _TOOL_CONTEXT
    from tools.agent_ops import _spawn_one, _MAX_CONCURRENT

    if runtime is None:
        runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        if runtime is None:
            raise RuntimeError("Agent runtime not initialized.")

    if config is None:
        config = _TOOL_CONTEXT.__dict__.get("_agent_config")
        if config is None:
            raise RuntimeError("Agent config not available.")

    import json
    shared_ctx = ""
    if shared_input:
        shared_ctx = json.dumps(shared_input)

    task_ids = []
    for desc in descriptions:
        if runtime.active_count >= _MAX_CONCURRENT:
            break
        tid = _spawn_one(
            desc, config, runtime, wg, rg, max_turns,
            cancel_event=None, visible=visible,
            shared_context=shared_ctx,
        )
        if subscriptions:
            runtime.set_subscriptions(tid, subscriptions)
        task_ids.append(tid)

    return task_ids


def fan_in(
    task_ids: list[str],
    runtime: AgentRuntime | None = None,
    timeout: float = 120.0,
) -> list[SubAgentResult]:
    """Collect results from all task_ids. Blocks until all complete or timeout.

    Returns results in the same order as task_ids (None for timed-out tasks).
    """
    from tools import _TOOL_CONTEXT

    if runtime is None:
        runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        if runtime is None:
            raise RuntimeError("Agent runtime not initialized.")

    results: list[SubAgentResult | None] = [None] * len(task_ids)
    pending = set(range(len(task_ids)))

    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for i in list(pending):
            tid = task_ids[i]
            status = runtime.get_status(tid)
            if status == "completed":
                results[i] = runtime.get_result(tid)
                pending.discard(i)
                continue
            if status == "not_found":
                # Task was never found — treat as timed out
                results[i] = None
                pending.discard(i)
        if pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            with runtime._condition:
                runtime._condition.wait(timeout=min(0.5, remaining))

    return results


def pipeline(
    stages: list[dict],
    runtime: AgentRuntime | None = None,
    config=None,
    wg=None,
    rg=None,
    max_turns: int = 15,
    timeout: float = 300.0,
) -> SubAgentResult | None:
    """Run stages in sequence, each receiving the prior stage's result.

    Each stage is a dict with:
        task: str             — task description
        subscriptions: list[str] — message types to subscribe to

    Each stage after the first subscribes to "handoff.result" and receives
    the previous stage's output via its inbox.

    Returns the final stage's SubAgentResult, or None if any stage fails.
    """
    from tools import _TOOL_CONTEXT
    from tools.agent_ops import _spawn_one

    if runtime is None:
        runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        if runtime is None:
            raise RuntimeError("Agent runtime not initialized.")

    if config is None:
        config = _TOOL_CONTEXT.__dict__.get("_agent_config")
        if config is None:
            raise RuntimeError("Agent config not available.")

    prev_result = None
    for i, stage in enumerate(stages):
        task = stage["task"]
        subs = stage.get("subscriptions", [])

        shared_ctx = ""
        if i > 0 and prev_result is not None:
            import json
            # Pass previous result as shared context
            shared_ctx = json.dumps({
                "previous_result": prev_result.to_dict(),
                "stage": i,
            })

        tid = _spawn_one(
            task, config, runtime, wg, rg, max_turns,
            cancel_event=None, visible=False,
            shared_context=shared_ctx,
        )
        runtime.set_subscriptions(tid, subs)

        # Wait for this stage to complete
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = runtime.get_status(tid)
            if status == "completed":
                prev_result = runtime.get_result(tid)
                break
            if status == "not_found":
                prev_result = None
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                runtime.cancel(tid)
                prev_result = None
                break
            with runtime._condition:
                runtime._condition.wait(timeout=min(0.5, remaining))

        if prev_result is None or not prev_result.success:
            return prev_result

    return prev_result


def barrier(
    name: str,
    task_ids: list[str],
    runtime: AgentRuntime | None = None,
    timeout: float = 120.0,
) -> bool:
    """Block until all task_ids have sent a coord.sync message for *name*.

    Returns True if all agents reached the barrier, False on timeout.
    """
    from tools import _TOOL_CONTEXT

    if runtime is None:
        runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        if runtime is None:
            raise RuntimeError("Agent runtime not initialized.")

    total = len(task_ids)
    arrived: set[str] = set()

    deadline = time.monotonic() + timeout
    while len(arrived) < total and time.monotonic() < deadline:
        for tid in task_ids:
            if tid in arrived:
                continue
            inbox = runtime.get_inbox(tid)
            for msg in inbox:
                if msg.type == "coord.sync" and msg.payload.get("barrier") == name:
                    arrived.add(tid)
                    break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.2, remaining))

    return len(arrived) >= total


def scatter_gather(
    items: list,
    worker_task_template: str,
    runtime: AgentRuntime | None = None,
    config=None,
    wg=None,
    rg=None,
    max_turns: int = 15,
    timeout: float = 120.0,
) -> list[SubAgentResult | None]:
    """Fan-out with per-worker input slices.

    Each worker gets one item from *items* injected into its task description.
    Uses shared_context to pass the item data.

    Args:
        items: List of items to distribute (one per worker).
        worker_task_template: Task description with "{item}" placeholder.
    """
    descriptions = [
        worker_task_template.replace("{item}", str(item))
        for item in items
    ]

    task_ids = fan_out(
        descriptions,
        shared_input=None,
        runtime=runtime,
        config=config,
        wg=wg,
        rg=rg,
        max_turns=max_turns,
    )

    if not task_ids:
        return []

    return fan_in(task_ids, runtime=runtime, timeout=timeout)
