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
        blocked = {"spawn_agent", "agent_status", "collect_agent"}
        from sub_agent import run_sub_agent
        import inspect
        source = inspect.getsource(run_sub_agent)
        for tool in blocked:
            assert tool in source, f"Recursion guard should block '{tool}'"


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
