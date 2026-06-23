"""
Golden trace record/replay for agent regression testing.

Records agent tool-call traces as JSON artifacts ("golden traces"),
then replays them on every code/config change to detect behavioral drift.

Key features:
- Record: save a known-good agent run as a golden trace
- Replay: compare current behavior against golden trace
- Drift detection: flag when behavior diverges beyond threshold
- Diff: show exactly which tool calls changed
- Version pinning: attach prompt/config hash to each golden trace

Usage:
    from tools.trace_replay import TraceRecorder, TraceReplayer

    # Recording
    recorder = TraceRecorder("my_golden_trace")
    recorder.record_tool_call("read_file", {"path": "foo.py"}, result="...")
    recorder.record_tool_call("edit_file", {"path": "foo.py", ...})
    recorder.save()

    # Replaying
    replayer = TraceReplayer("my_golden_trace")
    replayer.load()
    replayer.assert_matches(current_trace)  # Raises on drift
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Golden trace data model ──────────────────────────────────────

@dataclass
class GoldenTrace:
    """A recorded agent execution trace used as regression baseline."""
    name: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    recorded_at: float = 0.0
    prompt_hash: str = ""
    config_hash: str = ""
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tool_calls": self.tool_calls,
            "metadata": self.metadata,
            "recorded_at": self.recorded_at,
            "prompt_hash": self.prompt_hash,
            "config_hash": self.config_hash,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GoldenTrace":
        return cls(
            name=d.get("name", "unnamed"),
            tool_calls=d.get("tool_calls", []),
            metadata=d.get("metadata", {}),
            recorded_at=d.get("recorded_at", 0.0),
            prompt_hash=d.get("prompt_hash", ""),
            config_hash=d.get("config_hash", ""),
            version=d.get("version", 1),
        )


# ── Recorder ─────────────────────────────────────────────────────

class TraceRecorder:
    """Records agent tool calls to build a golden trace."""

    def __init__(self, name: str, storage_dir: str | None = None):
        self._trace = GoldenTrace(name=name)
        self._trace.recorded_at = time.time()
        self._storage_dir = Path(storage_dir) if storage_dir else self._default_dir()

    @staticmethod
    def _default_dir() -> Path:
        return Path.home() / ".mini_agent" / "golden_traces"

    def record_tool_call(
        self,
        tool: str,
        args: dict[str, Any],
        result: Any = None,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Record a single tool call in the trace."""
        call: dict[str, Any] = {
            "tool": tool,
            "args": args,
            "index": len(self._trace.tool_calls),
        }
        if result is not None:
            # Sanitize large results for storage
            call["result"] = self._sanitize_result(result)
        if error is not None:
            call["error"] = error
        if duration_ms is not None:
            call["duration_ms"] = duration_ms

        self._trace.tool_calls.append(call)

    @staticmethod
    def _sanitize_result(result: Any, max_len: int = 10_000) -> Any:
        """Truncate large results to keep trace files reasonable."""
        if isinstance(result, str) and len(result) > max_len:
            return result[:max_len] + f"\n... [truncated {len(result) - max_len} chars]"
        if isinstance(result, dict):
            return {k: TraceRecorder._sanitize_result(v, max_len) for k, v in result.items()}
        if isinstance(result, list):
            return [TraceRecorder._sanitize_result(v, max_len) for v in result[:100]]
        return result

    def set_prompt_hash(self, prompt_text: str) -> None:
        """Record hash of the system prompt used during this run."""
        self._trace.prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()[:16]

    def set_config_hash(self, config: dict[str, Any]) -> None:
        """Record hash of the agent config used during this run."""
        config_str = json.dumps(config, sort_keys=True)
        self._trace.config_hash = hashlib.sha256(config_str.encode()).hexdigest()[:16]

    def set_metadata(self, key: str, value: Any) -> None:
        """Attach arbitrary metadata to the trace."""
        self._trace.metadata[key] = value

    def save(self) -> str:
        """Persist the golden trace to disk. Returns the file path."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        filepath = self._storage_dir / f"{self._trace.name}.json"
        with open(filepath, "w") as f:
            json.dump(self._trace.to_dict(), f, indent=2)
        return str(filepath)

    @property
    def tool_names(self) -> list[str]:
        return [c["tool"] for c in self._trace.tool_calls]

    @property
    def call_count(self) -> int:
        return len(self._trace.tool_calls)


# ── Replayer / Comparator ────────────────────────────────────────

class TraceComparisonResult:
    """Result of comparing a current trace against a golden trace."""

    def __init__(self, golden: GoldenTrace, current_tools: list[str]):
        self.golden = golden
        self.current_tools = current_tools
        self.match: bool = False
        self.score: float = 0.0
        self.differences: list[str] = []
        self._compute()

    def _compute(self) -> None:
        golden_tools = [c["tool"] for c in self.golden.tool_calls]

        # Exact match
        if golden_tools == self.current_tools:
            self.match = True
            self.score = 1.0
            return

        # Compute similarity score using sequence alignment
        total = max(len(golden_tools), len(self.current_tools))
        if total == 0:
            self.match = True
            self.score = 1.0
            return

        # Count matching positions
        matches = 0
        for i, (g, c) in enumerate(zip(golden_tools, self.current_tools)):
            if g == c:
                matches += 1

        # Also count out-of-order matches
        from collections import Counter
        golden_counts = Counter(golden_tools)
        current_counts = Counter(self.current_tools)
        count_matches = sum((golden_counts & current_counts).values())

        # Score is weighted: positional match + count match
        positional_score = matches / total if total > 0 else 0
        count_score = count_matches / total if total > 0 else 0
        self.score = 0.5 * positional_score + 0.5 * count_score

        # Threshold for "match"
        self.match = self.score >= 0.85

        # Build differences
        if not self.match:
            for i, (g, c) in enumerate(zip(golden_tools, self.current_tools)):
                if g != c:
                    self.differences.append(
                        f"Step {i}: expected '{g}', got '{c}'"
                    )
            if len(golden_tools) != len(self.current_tools):
                self.differences.append(
                    f"Call count changed: {len(golden_tools)} → {len(self.current_tools)}"
                )

    @property
    def drift_percentage(self) -> float:
        """Percentage of behavioral drift (0 = identical, 100 = completely different)."""
        return round((1.0 - self.score) * 100, 1)

    def __bool__(self) -> bool:
        return self.match

    def __repr__(self) -> str:
        if self.match:
            return f"TraceComparisonResult(MATCH, score={self.score:.3f})"
        return f"TraceComparisonResult(MISMATCH, score={self.score:.3f}, diffs={len(self.differences)})"


class TraceReplayer:
    """Loads golden traces and compares current behavior against them."""

    def __init__(self, name: str, storage_dir: str | None = None):
        self._name = name
        self._storage_dir = Path(storage_dir) if storage_dir else Path.home() / ".mini_agent" / "golden_traces"
        self._trace: GoldenTrace | None = None

    def load(self) -> GoldenTrace:
        """Load a golden trace from disk."""
        filepath = self._storage_dir / f"{self._name}.json"
        if not filepath.exists():
            raise FileNotFoundError(f"Golden trace not found: {filepath}")
        with open(filepath) as f:
            data = json.load(f)
        self._trace = GoldenTrace.from_dict(data)
        return self._trace

    def compare(self, current_tools: list[str]) -> TraceComparisonResult:
        """Compare current tool call sequence against the golden trace."""
        if self._trace is None:
            raise RuntimeError("No golden trace loaded. Call load() first.")
        return TraceComparisonResult(self._trace, current_tools)

    def assert_matches(
        self,
        current_tools: list[str],
        threshold: float = 0.85,
    ) -> TraceComparisonResult:
        """Assert current behavior matches golden trace.

        Returns TraceComparisonResult on match, raises AssertionError on mismatch.
        """
        result = self.compare(current_tools)
        if not result.match or result.score < threshold:
            raise AssertionError(
                f"Golden trace '{self._name}' mismatch:\n" +
                "\n".join(f"  - {d}" for d in result.differences) +
                f"\n  Drift: {result.drift_percentage}%"
            )
        return result

    def list_available(self) -> list[str]:
        """List all available golden trace names."""
        if not self._storage_dir.exists():
            return []
        return [p.stem for p in self._storage_dir.glob("*.json")]


# ── Convenience functions ────────────────────────────────────────

def record_run(
    name: str,
    tool_calls: list[dict[str, Any]],
    prompt_text: str = "",
    config: dict[str, Any] | None = None,
) -> str:
    """Quickly record a complete agent run as a golden trace."""
    recorder = TraceRecorder(name)
    for call in tool_calls:
        recorder.record_tool_call(
            tool=call["tool"],
            args=call.get("args", {}),
            result=call.get("result"),
            error=call.get("error"),
            duration_ms=call.get("duration_ms"),
        )
    if prompt_text:
        recorder.set_prompt_hash(prompt_text)
    if config:
        recorder.set_config_hash(config)
    return recorder.save()


def replay_and_assert(name: str, current_tools: list[str]) -> TraceComparisonResult:
    """Load golden trace and assert current behavior matches."""
    replayer = TraceReplayer(name)
    replayer.load()
    return replayer.assert_matches(current_tools)
