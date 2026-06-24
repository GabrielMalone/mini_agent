"""Tests for tools/agent_todos.py — plan, plan_status, todo_write, todo_read, write_scratchpad."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tools.agent_todos import _plan, _plan_status, _write_scratchpad

from tools.result import ToolResult


class TestPlan(unittest.TestCase):
    """Test plan tool."""

    def setUp(self):
        from tools import context as ctx_mod
        ctx_mod._TOOL_CONTEXT._plan_steps = []
        ctx_mod._TOOL_CONTEXT._plan_done = set()

    def tearDown(self):
        from tools import context as ctx_mod
        ctx_mod._TOOL_CONTEXT._plan_steps = []
        ctx_mod._TOOL_CONTEXT._plan_done = set()

    def test_plan_with_steps(self):
        result = _plan({"steps": ["Step 1", "Step 2", "Step 3"]}, None, None)
        self.assertTrue(result.success)
        self.assertIn("Plan (3 steps)", result.content)
        self.assertIn("[1] Step 1", result.content)
        self.assertIn("[2] Step 2", result.content)
        self.assertIn("[3] Step 3", result.content)

    def test_plan_empty_clears(self):
        from tools import context as ctx_mod
        ctx_mod._TOOL_CONTEXT._plan_steps = ["old step"]
        result = _plan({"steps": []}, None, None)
        self.assertTrue(result.success)
        self.assertIn("Plan cleared", result.content)

    def test_plan_empty_no_plan_was_active(self):
        result = _plan({"steps": []}, None, None)
        self.assertTrue(result.success)
        self.assertIn("No plan was active", result.content)

    def test_plan_not_a_list(self):
        result = _plan({"steps": 42}, None, None)
        self.assertFalse(result.success)
        self.assertIn("must be an array", result.content)

    def test_plan_string_repair_numbered(self):
        """Multi-line string with numbers -> auto-repaired to list."""
        result = _plan({"steps": "1. Do X\n2. Do Y\n3. Do Z"}, None, None)
        self.assertTrue(result.success)
        self.assertIn("Plan (3 steps)", result.content)

    def test_plan_string_repair_newlines(self):
        """Multi-line string without numbers -> split by line."""
        result = _plan({"steps": "Do X\nDo Y\nDo Z"}, None, None)
        self.assertTrue(result.success)
        self.assertIn("Plan (3 steps)", result.content)

    def test_plan_too_many_steps(self):
        steps = [f"Step {i}" for i in range(20)]
        result = _plan({"steps": steps}, None, None)
        self.assertFalse(result.success)
        self.assertIn("too large", result.content.lower())


class TestPlanStatus(unittest.TestCase):
    """Test plan_status tool."""

    def setUp(self):
        from tools import context as ctx_mod
        ctx_mod._TOOL_CONTEXT._plan_steps = []
        ctx_mod._TOOL_CONTEXT._plan_done = set()

    def tearDown(self):
        from tools import context as ctx_mod
        ctx_mod._TOOL_CONTEXT._plan_steps = []
        ctx_mod._TOOL_CONTEXT._plan_done = set()

    def test_no_active_plan(self):
        result = _plan_status({}, None, None)
        self.assertTrue(result.success)
        self.assertIn("No active plan", result.content)

    def test_mark_step_complete(self):
        from tools import context as ctx_mod
        ctx_mod._TOOL_CONTEXT._plan_steps = ["Step 1", "Step 2"]
        ctx_mod._TOOL_CONTEXT._plan_done = set()

        result = _plan_status({"step": 1}, None, None)
        self.assertTrue(result.success)
        self.assertIn("[V] 1. Step 1", result.content)
        self.assertIn("[o] 2. Step 2", result.content)

    def test_mark_step_complete_shows_progress(self):
        from tools import context as ctx_mod
        ctx_mod._TOOL_CONTEXT._plan_steps = ["Step 1", "Step 2", "Step 3"]
        ctx_mod._TOOL_CONTEXT._plan_done = set()

        _plan_status({"step": 1}, None, None)
        result = _plan_status({"step": 2}, None, None)
        self.assertTrue(result.success)
        self.assertIn("(2/3 complete)", result.content)

    def test_invalid_step(self):
        from tools import context as ctx_mod
        ctx_mod._TOOL_CONTEXT._plan_steps = ["Step 1", "Step 2"]
        ctx_mod._TOOL_CONTEXT._plan_done = set()

        result = _plan_status({"step": 5}, None, None)
        self.assertFalse(result.success)
        self.assertIn("Invalid step", result.content)

    def test_all_steps_complete(self):
        from tools import context as ctx_mod
        ctx_mod._TOOL_CONTEXT._plan_steps = ["Step 1"]
        ctx_mod._TOOL_CONTEXT._plan_done = set()

        result = _plan_status({"step": 1}, None, None)
        self.assertIn("All steps complete", result.content)

    def test_status_without_step_arg(self):
        from tools import context as ctx_mod
        ctx_mod._TOOL_CONTEXT._plan_steps = ["Step 1", "Step 2"]
        ctx_mod._TOOL_CONTEXT._plan_done = {0}

        result = _plan_status({}, None, None)
        self.assertTrue(result.success)
        self.assertIn("(1/2 complete)", result.content)


if __name__ == "__main__":
    unittest.main()
