#!/usr/bin/env python3
"""
test_tui.py — tests for the Textual TUI frontend.
"""

import os
import tempfile
import unittest
from queue import Queue
from unittest.mock import MagicMock, patch
from tui import _Done, _Error

from tui import (
    MiniAgentTUI, AgentWorker,
    _TokenMsg, _ToolStart, _ToolEnd, _Done, _Error,
)
from config import AgentConfig, DEFAULT_API_KEY
from safety import ReadSafetyGate, WriteSafetyGate


class TestTUIImports(unittest.TestCase):
    """Verify tui.py imports and basic construction."""

    def test_app_class_exists(self):
        self.assertTrue(hasattr(MiniAgentTUI, "compose"))
        self.assertTrue(hasattr(MiniAgentTUI, "on_mount"))

    def test_app_css_defined(self):
        self.assertIsInstance(MiniAgentTUI.CSS, str)
        self.assertIn("background", MiniAgentTUI.CSS)

    def test_bindings_contain_cancel(self):
        binds = {b.key: b.action for b in MiniAgentTUI.BINDINGS}
        self.assertIn("ctrl+c", binds)
        self.assertEqual(binds["ctrl+c"], "cancel")

    def test_bindings_contain_submit(self):
        binds = {b.key: b.action for b in MiniAgentTUI.BINDINGS}
        self.assertIn("enter", binds)
        self.assertEqual(binds["enter"], "submit")

    def test_bindings_contain_quit(self):
        binds = {b.key: b.action for b in MiniAgentTUI.BINDINGS}
        self.assertIn("ctrl+q", binds)
        self.assertEqual(binds["ctrl+q"], "quit")

    def test_css_dark_gray_palette(self):
        css = MiniAgentTUI.CSS
        self.assertIn("#111111", css)
        self.assertNotIn("16162a", css)


class TestMessageTypes(unittest.TestCase):
    """Verify worker→UI message dataclass-like types."""

    def test_token_msg(self):
        m = _TokenMsg("hello")
        self.assertEqual(m.text, "hello")

    def test_tool_start(self):
        m = _ToolStart("search_files('TODO', .)")
        self.assertIn("search_files", m.summary)

    def test_tool_end_success(self):
        m = _ToolEnd(True, "OK")
        self.assertTrue(m.ok)
        self.assertEqual(m.detail, "OK")

    def test_tool_end_failure(self):
        m = _ToolEnd(False, "blocked")
        self.assertFalse(m.ok)

    def test_done(self):
        self.assertIsInstance(_Done(), _Done)

    def test_error(self):
        m = _Error("something broke")
        self.assertEqual(m.msg, "something broke")


class TestAgentWorker(unittest.TestCase):
    """Verify AgentWorker thread setup and cancel."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.config = AgentConfig.load(self.workspace)
        self.config.api_key = DEFAULT_API_KEY
        self.write_gate = WriteSafetyGate(self.workspace, allow_overwrites=True)
        self.read_gate = ReadSafetyGate(self.workspace)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_worker_creates_and_cancels(self):
        messages = [{"role": "system", "content": "You are a test."}]
        out = Queue()
        w = AgentWorker(messages, self.config, self.write_gate, self.read_gate, out, MagicMock())
        self.assertFalse(w.cancel.is_set())
        w.cancel.set()
        w.start()
        w.join(timeout=5)
        self.assertFalse(w.is_alive())

    def test_worker_stream_config_set(self):
        messages = [{"role": "system", "content": "You are a test."}]
        out = Queue()
        config = self.config
        self.assertFalse(config.stream)
        w = AgentWorker(messages, config, self.write_gate, self.read_gate, out, MagicMock())
        w.cancel.set()
        w.start()
        w.join(timeout=5)
        self.assertTrue(config.stream)


if __name__ == "__main__":
    unittest.main()


    def test_worker_exception_pushes_error_to_queue(self):
        """Worker exception sends _Done with error instead of crashing."""
        import threading, requests
        from unittest.mock import patch
        out = Queue()
        config = self.config
        config.stream = True
        w = AgentWorker(
            [{"role": "user", "content": "test"}],
            config, self.write_gate, self.read_gate,
            out, requests.Session(),
        )
        # Make run_agent_turn raise
        with patch("tui.run_agent_turn", side_effect=RuntimeError("boom")):
            w.run()
        # Should have pushed at least a _Done with error
        items = []
        while not out.empty():
            items.append(out.get_nowait())
        errors = [i for i in items if isinstance(i, _Error)]
        dons = [i for i in items if isinstance(i, _Done)]
        self.assertEqual(len(errors), 1, f'Expected 1 _Error, got: {errors}')
        self.assertIn('boom', errors[0].msg)
