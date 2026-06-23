"""Tests for tools/shell_ops.py."""
from __future__ import annotations
import os
import tempfile
import unittest
from core.safety import ReadSafetyGate, WriteSafetyGate
from tools.result import ToolResult
from tools.shell_ops import _check_dangerous_command, _diagnose_failures, _parse_pytest_output, _run_shell, _search_files, _task_status, _verify

class TestSearchFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root)
        self.wg = WriteSafetyGate(self.ws_root)
        with open(os.path.join(self.tmp, 'a.py'), 'w') as f:
            f.write('def hello():\n    return 42\n')
        with open(os.path.join(self.tmp, 'b.txt'), 'w') as f:
            f.write('hello world\nfoo bar\n')
    def test_search_literal(self):
        r = _search_files({'pattern': 'hello', 'path': self.tmp}, self.wg, self.rg)
        self.assertTrue(r.success)
        self.assertIn('hello', r.content)
    def test_search_icase(self):
        r = _search_files({'pattern': 'HELLO', 'path': self.tmp, 'ignore_case': True}, self.wg, self.rg)
        self.assertTrue(r.success)
        self.assertIn('hello', r.content.lower())
    def test_search_no_results(self):
        r = _search_files({'pattern': 'xyznonexistent', 'path': self.tmp}, self.wg, self.rg)
        self.assertTrue(r.success, r.content)

class TestDangerousCommand(unittest.TestCase):
    def test_safe(self):
        self.assertIsNone(_check_dangerous_command('echo hello', False))
    def test_blocked(self):
        self.assertIsNotNone(_check_dangerous_command('mkfs.ext4 /dev/sda', False))
    def test_force(self):
        r = _check_dangerous_command('mkfs.ext4 /dev/sda', True)
        self.assertTrue(r is None or isinstance(r, str))

class TestRunShell(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root)
        self.wg = WriteSafetyGate(self.ws_root)
    def test_simple(self):
        r = _run_shell({'command': 'echo hello'}, self.wg, self.rg)
        self.assertTrue(r.success)
        self.assertIn('hello', r.content)
    def test_exit(self):
        r = _run_shell({'command': 'exit 1'}, self.wg, self.rg)
        self.assertFalse(r.success, r.content)
    def test_timeout(self):
        r = _run_shell({'command': 'sleep 10', 'timeout': 1}, self.wg, self.rg)
        self.assertFalse(r.success)

class TestTaskStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root)
        self.wg = WriteSafetyGate(self.ws_root)
    def test_nonexistent(self):
        r = _task_status({'task_id': 'nonexistent'}, self.wg, self.rg)
        self.assertIsInstance(r, ToolResult)

class TestVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root)
        self.wg = WriteSafetyGate(self.ws_root)
    def test_returns_toolresult(self):
        self.assertIsInstance(_verify({}, self.wg, self.rg), ToolResult)

class TestDiagnose(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws_root = self.tmp
        self.rg = ReadSafetyGate(self.ws_root)
        self.wg = WriteSafetyGate(self.ws_root)
    def test_returns_toolresult(self):
        self.assertIsInstance(_diagnose_failures({}, self.wg, self.rg), ToolResult)

class TestPytestOutput(unittest.TestCase):
    def test_passing(self):
        o = 'tests/test.py::test PASSED [100%]\n===== 2 passed ====='
        s, f = _parse_pytest_output(o, exit_code=0)
        self.assertIsInstance(s, str)
        self.assertIn('2 passed', s)
    def test_failing(self):
        o = 'tests/test.py::test FAILED [100%]\n===== 1 failed ====='
        s, f = _parse_pytest_output(o, exit_code=1)
        self.assertIsInstance(s, str)
        self.assertIn('1 failed', s)

if __name__ == '__main__':
    unittest.main()
