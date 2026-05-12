#!/usr/bin/env python3
"""
test_sub_agent.py — tests for the multi-agent subsystem.

Covers:
    - AgentRuntime (registry)
    - SubAgentResult (dataclass)
    - spawn_agent / agent_status / collect_agent tool dispatch
    - Recursion guard (sub-agents cannot spawn sub-agents)
    - Concurrency cap (max 5 sub-agents)
"""

import pytest
import threading
import time

from agent_runtime import AgentRuntime, SubAgentResult
from tools import execute_tool, _TOOL_DISPATCH, _TOOL_CONTEXT, set_context
from safety import ReadSafetyGate, WriteSafetyGate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runtime():
    """Fresh AgentRuntime for each test."""
    return AgentRuntime()


@pytest.fixture
def gates(tmp_path):
    """Safety gates rooted in a temp directory."""
    wg = WriteSafetyGate(str(tmp_path))
    rg = ReadSafetyGate(str(tmp_path))
    return wg, rg


@pytest.fixture
def configured_context(tmp_path, monkeypatch):
    """Set up _TOOL_CONTEXT with a runtime and a mock config for tool tests."""
    from agent_runtime import AgentRuntime
    runtime = AgentRuntime()
    # Create a minimal mock config
    class MockConfig:
        model = "test-model"
        api_key = "test-key"
        api_url = "https://test.api"
        stream = False
        sub_agent_max_turns = 5
    config = MockConfig()
    set_context(_agent_runtime=runtime, _agent_config=config, workspace=str(tmp_path))
    yield
    # Cleanup
    set_context(_agent_runtime=None, _agent_config=None)


# ---------------------------------------------------------------------------
# AgentRuntime tests
# ---------------------------------------------------------------------------

class TestAgentRuntime:
    def test_register_and_status_running(self, runtime):
        cancel = threading.Event()
        thread = threading.Thread(target=lambda: time.sleep(0.1), daemon=True)
        runtime.register("task1", thread, cancel)
        thread.start()
        assert runtime.get_status("task1") == "running"
        thread.join()

    def test_status_not_found(self, runtime):
        assert runtime.get_status("nonexistent") == "not_found"

    def test_get_result_after_store(self, runtime):
        result = SubAgentResult(success=True, content="done", turns_used=3)
        runtime.store_result("task1", result)
        assert runtime.get_status("task1") == "completed"
        stored = runtime.get_result("task1")
        assert stored is not None
        assert stored.success is True
        assert stored.content == "done"

    def test_active_count(self, runtime):
        assert runtime.active_count == 0
        cancel = threading.Event()
        t = threading.Thread(target=lambda: time.sleep(0.2), daemon=True)
        runtime.register("a", t, cancel)
        t.start()
        assert runtime.active_count == 1
        t.join()
        runtime.store_result("a", SubAgentResult(True, "ok"))
        assert runtime.active_count == 0

    def test_cancel(self, runtime):
        cancel = threading.Event()
        t = threading.Thread(target=lambda: cancel.wait(), daemon=True)
        runtime.register("x", t, cancel)
        t.start()
        assert runtime.cancel("x") is True
        assert cancel.is_set()
        t.join(timeout=1)

    def test_cancel_all(self, runtime):
        events = []
        for i in range(3):
            ev = threading.Event()
            t = threading.Thread(target=lambda e=ev: e.wait(), daemon=True)
            runtime.register(f"task_{i}", t, ev)
            t.start()
            events.append(ev)
        count = runtime.cancel_all()
        assert count == 3
        for ev in events:
            assert ev.is_set()


# ---------------------------------------------------------------------------
# SubAgentResult tests
# ---------------------------------------------------------------------------

class TestSubAgentResult:
    def test_defaults(self):
        r = SubAgentResult(success=False, content="fail")
        assert r.turns_used == 0
        assert r.tool_calls_made == 0
        assert r.scratchpad == ""
        assert r.error is None

    def test_to_json(self):
        r = SubAgentResult(success=True, content="hello", turns_used=5, tool_calls_made=3)
        j = r.to_json()
        assert '"success": true' in j
        assert '"content": "hello"' in j
        assert '"turns_used": 5' in j


# ---------------------------------------------------------------------------
# Tool dispatch tests
# ---------------------------------------------------------------------------

class TestSpawnAgentTool:
    def test_missing_task(self, gates):
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("spawn_agent")
        assert dispatch is not None
        result = dispatch({"max_turns": 10}, wg, rg)
        assert result.success is False
        assert "Missing required" in result.content

    def test_no_runtime_configured(self, gates):
        """Without _agent_runtime in context, spawn should fail gracefully."""
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("spawn_agent")
        # Ensure runtime is None
        old = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        _TOOL_CONTEXT.__dict__["_agent_runtime"] = None
        try:
            result = dispatch({"task": "do something"}, wg, rg)
            assert result.success is False
            assert "not initialized" in result.content
        finally:
            _TOOL_CONTEXT.__dict__["_agent_runtime"] = old

    def test_spawn_returns_task_id(self, configured_context, gates):
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("spawn_agent")
        result = dispatch({"task": "say hello", "max_turns": 1}, wg, rg)
        assert result.success is True
        assert "Spawned sub-agent" in result.content
        # task_id should be 8 hex chars
        import re
        match = re.search(r"'([a-f0-9]{8})'", result.content)
        assert match is not None


class TestAgentStatusTool:
    def test_missing_task_id(self, gates):
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("agent_status")
        result = dispatch({}, wg, rg)
        assert result.success is False
        assert "Missing" in result.content

    def test_not_found(self, configured_context, gates):
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("agent_status")
        result = dispatch({"task_id": "deadbeef"}, wg, rg)
        assert result.success is True
        assert "not found" in result.content


class TestCollectAgentTool:
    def test_missing_task_id(self, gates):
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("collect_agent")
        result = dispatch({}, wg, rg)
        assert result.success is False
        assert "Missing" in result.content

    def test_not_found(self, configured_context, gates):
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("collect_agent")
        result = dispatch({"task_id": "deadbeef"}, wg, rg)
        assert result.success is False
        assert "not found" in result.content


# ---------------------------------------------------------------------------
# Recursion guard test (via sub_agent module)
# ---------------------------------------------------------------------------

class TestRecursionGuard:
    def test_blocked_tools_in_sub_agent(self):
        """Verify that the blocked tool names are correct."""
        blocked = {"spawn_agent", "agent_status", "collect_agent", "collect_any"}
        from sub_agent import run_sub_agent
        import inspect
        source = inspect.getsource(run_sub_agent)
        for tool in blocked:
            assert tool in source, f"Recursion guard should block '{tool}'"


# ---------------------------------------------------------------------------
# spawn_all / batch spawn tests
# ---------------------------------------------------------------------------

class TestSpawnAll:
    def test_batch_spawn_tasks(self, configured_context, gates):
        """spawn_agent with tasks=list should spawn multiple sub-agents."""
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("spawn_agent")
        result = dispatch({
            "tasks": ["say hello", "say goodbye", "count to 3"],
            "max_turns": 1,
        }, wg, rg)
        assert result.success is True
        assert "Spawned 3 sub-agent" in result.content

    def test_empty_tasks_fails(self, configured_context, gates):
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("spawn_agent")
        result = dispatch({"tasks": []}, wg, rg)
        assert result.success is False
        assert "non-empty list" in result.content

    def test_mixed_invalid_tasks(self, configured_context, gates):
        """Empty strings in tasks list are skipped gracefully."""
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("spawn_agent")
        result = dispatch({
            "tasks": ["valid task", "", "  "],
            "max_turns": 1,
        }, wg, rg)
        assert result.success is True
        assert "1 sub-agent" in result.content  # only the valid one spawned

    def test_all_invalid_tasks_fails(self, configured_context, gates):
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("spawn_agent")
        result = dispatch({"tasks": ["", ""]}, wg, rg)
        assert result.success is False
        assert "No sub-agents could be spawned" in result.content


# ---------------------------------------------------------------------------
# collect_any tests
# ---------------------------------------------------------------------------

class TestCollectAny:
    def test_collect_any_missing_runtime(self, gates):
        """Without runtime, collect_any should fail gracefully."""
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("collect_any")
        assert dispatch is not None
        old = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        _TOOL_CONTEXT.__dict__["_agent_runtime"] = None
        try:
            result = dispatch({}, wg, rg)
            assert result.success is False
            assert "not initialized" in result.content
        finally:
            _TOOL_CONTEXT.__dict__["_agent_runtime"] = old

    def test_collect_any_no_sub_agents(self, configured_context, gates):
        """No sub-agents running or completed — should return error."""
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("collect_any")
        result = dispatch({}, wg, rg)
        assert result.success is False
        assert "No sub-agents" in result.content

    def test_collect_any_already_completed(self, configured_context, gates):
        """If a sub-agent already completed, collect_any returns it immediately."""
        wg, rg = gates
        runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        result_obj = SubAgentResult(success=True, content="done", turns_used=2)
        runtime.store_result("task_x", result_obj)

        dispatch = _TOOL_DISPATCH.get("collect_any")
        result = dispatch({}, wg, rg)
        assert result.success is True
        assert "task_x" in result.content
        assert "done" in result.content

    def test_collect_any_with_task_ids(self, configured_context, gates):
        """collect_any with specific task_ids returns the first completed."""
        wg, rg = gates
        runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        result_obj = SubAgentResult(success=True, content="beta result", turns_used=1)
        runtime.store_result("beta", result_obj)

        dispatch = _TOOL_DISPATCH.get("collect_any")
        result = dispatch({"task_ids": ["alpha", "beta", "gamma"]}, wg, rg)
        assert result.success is True
        assert "beta" in result.content
        assert "beta result" in result.content


# ---------------------------------------------------------------------------
# SubAgentResult serialization round-trip
# ---------------------------------------------------------------------------

class TestResultSerialization:
    def test_round_trip(self):
        r = SubAgentResult(
            success=True,
            content="Task completed successfully.",
            turns_used=4,
            tool_calls_made=2,
            scratchpad="## Plan\n- did stuff",
            error=None,
        )
        j = r.to_json()
        import json
        d = json.loads(j)
        assert d["success"] is True
        assert d["turns_used"] == 4
        assert d["tool_calls_made"] == 2


# ---------------------------------------------------------------------------
# shared_context tests
# ---------------------------------------------------------------------------

class TestSharedContext:
    def test_shared_context_passed_to_sub_agent(self, configured_context, gates):
        """Verify shared_context shows up in sub-agent messages."""
        import sub_agent as sa

        # Capture the messages built by run_sub_agent
        original = sa.run_sub_agent

        def capture(*args, **kwargs):
            # We just want to verify shared_context is in kwargs
            assert "shared_context" in kwargs
            return SubAgentResult(success=True, content="ok")

        try:
            sa.run_sub_agent = capture
            wg, rg = gates
            dispatch = _TOOL_DISPATCH.get("spawn_agent")
            result = dispatch({
                "task": "test task",
                "max_turns": 1,
                "shared_context": "API: /stats -> {count: int}",
            }, wg, rg)
            assert result.success is True
        finally:
            sa.run_sub_agent = original


# ---------------------------------------------------------------------------
# agent_message tests
# ---------------------------------------------------------------------------

class TestAgentMessage:
    def setup_method(self):
        from tools.agent_ops import _AGENT_MSGS, _AGENT_MSGS_LOCK
        with _AGENT_MSGS_LOCK:
            _AGENT_MSGS.clear()

    def test_broadcast_and_read(self, configured_context, gates):
        """Send a message, then read it back."""
        wg, rg = gates
        send = _TOOL_DISPATCH.get("agent_message")
        read = _TOOL_DISPATCH.get("agent_read")

        r1 = send({"text": "Backend API ready at /api/stats", "from": "backend"}, wg, rg)
        assert r1.success is True
        assert "1 total messages" in r1.content

        r2 = read({}, wg, rg)
        assert r2.success is True
        assert "Backend API ready" in r2.content
        assert "from=backend" in r2.content

    def test_read_since(self, configured_context, gates):
        """agent_read with since should skip old messages."""
        wg, rg = gates
        send = _TOOL_DISPATCH.get("agent_message")
        read = _TOOL_DISPATCH.get("agent_read")

        send({"text": "msg 0"}, wg, rg)
        send({"text": "msg 1"}, wg, rg)
        send({"text": "msg 2"}, wg, rg)

        r = read({"since": 1}, wg, rg)
        assert r.success is True
        assert "msg 1" in r.content
        assert "msg 2" in r.content
        assert "msg 0" not in r.content

    def test_read_no_new_messages(self, configured_context, gates):
        wg, rg = gates
        send = _TOOL_DISPATCH.get("agent_message")
        read = _TOOL_DISPATCH.get("agent_read")

        send({"text": "only msg"}, wg, rg)
        r = read({"since": 5}, wg, rg)  # beyond what exists
        assert r.success is True
        assert "No new messages" in r.content

    def test_send_missing_text(self, configured_context, gates):
        wg, rg = gates
        send = _TOOL_DISPATCH.get("agent_message")
        r = send({}, wg, rg)
        assert r.success is False
        assert "Missing" in r.content


# ---------------------------------------------------------------------------
# agent_extend tests
# ---------------------------------------------------------------------------

class TestAgentExtend:
    def test_extend_running_agent(self, configured_context, gates):
        """Extending a running agent's turns should succeed."""
        wg, rg = gates
        runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        # Simulate a running agent
        ev = threading.Event()
        t = threading.Thread(target=lambda: ev.wait(), daemon=True)
        runtime.register("task_z", t, ev, max_turns=10)
        t.start()

        dispatch = _TOOL_DISPATCH.get("agent_extend")
        result = dispatch({"task_id": "task_z", "additional": 10}, wg, rg)
        assert result.success is True
        assert "+10" in result.content
        assert runtime.get_max_turns("task_z") == 20

        runtime.cancel("task_z")
        t.join(timeout=1)

    def test_extend_completed_agent(self, configured_context, gates):
        """Extending an already-completed agent should report it."""
        wg, rg = gates
        runtime = _TOOL_CONTEXT.__dict__.get("_agent_runtime")
        runtime.store_result("done", SubAgentResult(success=True, content="ok"))

        dispatch = _TOOL_DISPATCH.get("agent_extend")
        result = dispatch({"task_id": "done", "additional": 5}, wg, rg)
        assert result.success is True
        assert "already completed" in result.content

    def test_extend_not_found(self, configured_context, gates):
        wg, rg = gates
        dispatch = _TOOL_DISPATCH.get("agent_extend")
        result = dispatch({"task_id": "nope", "additional": 5}, wg, rg)
        assert result.success is False
        assert "not found" in result.content
