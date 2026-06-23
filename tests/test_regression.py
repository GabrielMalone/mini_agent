"""
Golden trace regression tests.

Tests the record/replay system:
- Recording and saving golden traces
- Loading and comparing against golden traces
- Drift percentage calculation
- Exact match, partial match, complete mismatch scenarios
- Edge cases: empty traces, large results, missing files
"""

import json
import os
import tempfile

import pytest

from tools.trace_replay import (
    GoldenTrace,
    TraceRecorder,
    TraceReplayer,
    TraceComparisonResult,
    record_run,
    replay_and_assert,
)


# ── Helpers ──────────────────────────────────────────────────────

def _make_call(tool: str, args: dict | None = None, **kwargs):
    call = {"tool": tool, "args": args or {}}
    call.update(kwargs)
    return call


# ── GoldenTrace model ────────────────────────────────────────────

class TestGoldenTraceModel:
    def test_empty_trace(self):
        gt = GoldenTrace(name="test")
        assert gt.name == "test"
        assert gt.tool_calls == []
        assert gt.version == 1

    def test_to_dict_and_back(self):
        gt = GoldenTrace(
            name="roundtrip",
            tool_calls=[
                {"tool": "read_file", "args": {"path": "x.py"}, "index": 0},
                {"tool": "edit_file", "args": {"path": "x.py"}, "index": 1},
            ],
            metadata={"task": "fix bug"},
            recorded_at=1234567890.0,
            prompt_hash="abc123",
            config_hash="def456",
            version=2,
        )
        d = gt.to_dict()
        gt2 = GoldenTrace.from_dict(d)
        assert gt2.name == "roundtrip"
        assert len(gt2.tool_calls) == 2
        assert gt2.tool_calls[0]["tool"] == "read_file"
        assert gt2.metadata["task"] == "fix bug"
        assert gt2.prompt_hash == "abc123"

    def test_from_dict_partial(self):
        """Minimal dict should produce sensible defaults."""
        gt = GoldenTrace.from_dict({"name": "minimal"})
        assert gt.tool_calls == []
        assert gt.metadata == {}
        assert gt.recorded_at == 0.0


# ── TraceRecorder ────────────────────────────────────────────────

class TestTraceRecorder:
    def test_record_and_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("test_run", storage_dir=tmpdir)
            recorder.record_tool_call("read_file", {"path": "foo.py"}, result="content")
            recorder.record_tool_call("edit_file", {"path": "foo.py", "old_string": "x", "new_string": "y"})

            path = recorder.save()
            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)

            assert data["name"] == "test_run"
            assert len(data["tool_calls"]) == 2
            assert data["tool_calls"][0]["tool"] == "read_file"
            assert data["tool_calls"][1]["tool"] == "edit_file"
            assert data["tool_calls"][0]["result"] == "content"

    def test_tool_names_property(self):
        recorder = TraceRecorder("test")
        recorder.record_tool_call("read_file", {})
        recorder.record_tool_call("edit_file", {})
        recorder.record_tool_call("run_shell", {})
        assert recorder.tool_names == ["read_file", "edit_file", "run_shell"]
        assert recorder.call_count == 3

    def test_set_hashes(self):
        recorder = TraceRecorder("test")
        recorder.set_prompt_hash("system: you are an agent")
        recorder.set_config_hash({"model": "claude-3.5-sonnet"})
        assert len(recorder._trace.prompt_hash) == 16
        assert len(recorder._trace.config_hash) == 16

    def test_set_metadata(self):
        recorder = TraceRecorder("test")
        recorder.set_metadata("task", "fix bug #42")
        recorder.set_metadata("duration_s", 12.5)
        assert recorder._trace.metadata["task"] == "fix bug #42"
        assert recorder._trace.metadata["duration_s"] == 12.5

    def test_sanitize_large_result(self):
        recorder = TraceRecorder("test")
        large = "x" * 20_000
        sanitized = recorder._sanitize_result(large)
        assert len(sanitized) < len(large)
        assert "[truncated" in sanitized

    def test_sanitize_dict_result(self):
        recorder = TraceRecorder("test")
        result = {"a": "x" * 20_000, "b": "short"}
        sanitized = recorder._sanitize_result(result)
        assert "[truncated" in sanitized["a"]
        assert sanitized["b"] == "short"

    def test_record_with_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("error_run", storage_dir=tmpdir)
            recorder.record_tool_call(
                "run_shell", {"command": "bad"}, error="Command not found"
            )
            path = recorder.save()
            with open(path) as f:
                data = json.load(f)
            assert data["tool_calls"][0]["error"] == "Command not found"

    def test_record_with_duration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("timed_run", storage_dir=tmpdir)
            recorder.record_tool_call(
                "run_shell", {"command": "sleep 1"}, duration_ms=1234.5
            )
            path = recorder.save()
            with open(path) as f:
                data = json.load(f)
            assert data["tool_calls"][0]["duration_ms"] == 1234.5


# ── TraceReplayer ────────────────────────────────────────────────

class TestTraceReplayer:
    def test_load_and_compare_exact_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Record
            recorder = TraceRecorder("exact", storage_dir=tmpdir)
            recorder.record_tool_call("read_file", {"path": "a.py"})
            recorder.record_tool_call("edit_file", {"path": "a.py"})
            recorder.save()

            # Replay
            replayer = TraceReplayer("exact", storage_dir=tmpdir)
            replayer.load()
            result = replayer.compare(["read_file", "edit_file"])
            assert result.match
            assert result.score == 1.0
            assert result.drift_percentage == 0.0
            assert result.differences == []

    def test_compare_complete_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("mismatch", storage_dir=tmpdir)
            recorder.record_tool_call("read_file", {"path": "a.py"})
            recorder.record_tool_call("edit_file", {"path": "a.py"})
            recorder.save()

            replayer = TraceReplayer("mismatch", storage_dir=tmpdir)
            replayer.load()
            result = replayer.compare(["web_search", "browser"])
            assert not result.match
            assert result.score < 0.5
            assert result.drift_percentage > 50.0

    def test_compare_partial_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("partial", storage_dir=tmpdir)
            recorder.record_tool_call("read_file", {"path": "a.py"})
            recorder.record_tool_call("edit_file", {"path": "a.py"})
            recorder.record_tool_call("run_shell", {"command": "pytest"})
            recorder.save()

            replayer = TraceReplayer("partial", storage_dir=tmpdir)
            replayer.load()
            # 2 out of 3 match positionally
            result = replayer.compare(["read_file", "edit_file", "search_files"])
            assert not result.match  # Below 0.85 threshold
            assert result.drift_percentage > 0

    def test_compare_different_length(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("diff_len", storage_dir=tmpdir)
            recorder.record_tool_call("read_file", {"path": "a.py"})
            recorder.record_tool_call("edit_file", {"path": "a.py"})
            recorder.save()

            replayer = TraceReplayer("diff_len", storage_dir=tmpdir)
            replayer.load()
            result = replayer.compare(["read_file"])
            assert not result.match
            assert "Call count changed" in " ".join(result.differences)

    def test_assert_matches_raises_on_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("assert_test", storage_dir=tmpdir)
            recorder.record_tool_call("read_file", {"path": "a.py"})
            recorder.save()

            replayer = TraceReplayer("assert_test", storage_dir=tmpdir)
            replayer.load()
            with pytest.raises(AssertionError, match="mismatch"):
                replayer.assert_matches(["edit_file"])

    def test_assert_matches_passes_on_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("assert_pass", storage_dir=tmpdir)
            recorder.record_tool_call("read_file", {"path": "a.py"})
            recorder.save()

            replayer = TraceReplayer("assert_pass", storage_dir=tmpdir)
            replayer.load()
            result = replayer.assert_matches(["read_file"])
            assert result.match

    def test_load_missing_file(self):
        replayer = TraceReplayer("nonexistent", storage_dir="/tmp/nonexistent_dir_xyz")
        with pytest.raises(FileNotFoundError):
            replayer.load()

    def test_compare_without_load(self):
        replayer = TraceReplayer("noload")
        with pytest.raises(RuntimeError, match="No golden trace loaded"):
            replayer.compare(["read_file"])

    def test_list_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder1 = TraceRecorder("trace_a", storage_dir=tmpdir)
            recorder1.record_tool_call("read_file", {})
            recorder1.save()

            recorder2 = TraceRecorder("trace_b", storage_dir=tmpdir)
            recorder2.record_tool_call("edit_file", {})
            recorder2.save()

            replayer = TraceReplayer("any", storage_dir=tmpdir)
            available = replayer.list_available()
            assert "trace_a" in available
            assert "trace_b" in available

    def test_list_available_empty(self):
        replayer = TraceReplayer("any", storage_dir="/tmp/nonexistent_dir_abc")
        assert replayer.list_available() == []


# ── TraceComparisonResult ────────────────────────────────────────

class TestTraceComparisonResult:
    def test_empty_both(self):
        gt = GoldenTrace(name="empty")
        result = TraceComparisonResult(gt, [])
        assert result.match
        assert result.score == 1.0
        assert result.drift_percentage == 0.0

    def test_exact_match_bool(self):
        gt = GoldenTrace(name="test", tool_calls=[
            {"tool": "a", "args": {}, "index": 0},
            {"tool": "b", "args": {}, "index": 1},
        ])
        result = TraceComparisonResult(gt, ["a", "b"])
        assert result.match
        assert bool(result) is True

    def test_mismatch_bool(self):
        gt = GoldenTrace(name="test", tool_calls=[
            {"tool": "a", "args": {}, "index": 0},
        ])
        result = TraceComparisonResult(gt, ["b"])
        assert not result.match
        assert bool(result) is False

    def test_repr(self):
        gt = GoldenTrace(name="test", tool_calls=[{"tool": "a", "args": {}, "index": 0}])
        result = TraceComparisonResult(gt, ["a"])
        assert "MATCH" in repr(result)

        result2 = TraceComparisonResult(gt, ["b"])
        assert "MISMATCH" in repr(result2)


# ── Convenience functions ────────────────────────────────────────

class TestConvenienceFunctions:
    def test_record_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Override default dir with patch — just test the function
            path = record_run(
                "quick_test",
                [
                    _make_call("read_file", {"path": "x.py"}, result="hello"),
                    _make_call("edit_file", {"path": "x.py"}),
                ],
                prompt_text="system: you are helpful",
                config={"model": "claude"},
            )
            # The record_run uses default dir; verify file was created
            # Just verify it doesn't crash
            assert path.endswith(".json")

    def test_replay_and_assert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("replay_test", storage_dir=tmpdir)
            recorder.record_tool_call("read_file", {"path": "a.py"})
            recorder.record_tool_call("edit_file", {"path": "a.py"})
            recorder.save()

            replayer = TraceReplayer("replay_test", storage_dir=tmpdir)
            replayer.load()
            result = replayer.assert_matches(["read_file", "edit_file"])
            assert result.match

    def test_replay_and_assert_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("failing_test", storage_dir=tmpdir)
            recorder.record_tool_call("read_file", {"path": "a.py"})
            recorder.save()

            replayer = TraceReplayer("failing_test", storage_dir=tmpdir)
            replayer.load()
            with pytest.raises(AssertionError):
                replayer.assert_matches(["edit_file"])


# ── Edge cases ───────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_call_trace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("single", storage_dir=tmpdir)
            recorder.record_tool_call("read_file", {"path": "a.py"})
            recorder.save()

            replayer = TraceReplayer("single", storage_dir=tmpdir)
            replayer.load()
            assert replayer.compare(["read_file"]).match
            assert not replayer.compare(["edit_file"]).match

    def test_unicode_in_args(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("unicode", storage_dir=tmpdir)
            recorder.record_tool_call("edit_file", {
                "path": "файл.py",
                "old_string": "привет мир",
            })
            path = recorder.save()
            with open(path) as f:
                data = json.load(f)
            assert data["tool_calls"][0]["args"]["path"] == "файл.py"

    def test_none_result_not_stored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder("none_result", storage_dir=tmpdir)
            recorder.record_tool_call("run_shell", {"command": "ls"}, result=None)
            path = recorder.save()
            with open(path) as f:
                data = json.load(f)
            assert "result" not in data["tool_calls"][0]
