# Session Handoff
# Auto-generated at session end. Read at next session start for continuity.

## Last Session: 2026-06-23 ~15:30 UTC

### What I Changed

**Fix: Single-tool timeout protection — prevents agent freeze when edit_file hangs**

Root cause: `_execute_groups()` in `core/llm.py` had a code path asymmetry:
- **Multi-tool (parallel) path**: `execute_tool()` ran in `ThreadPoolExecutor` with `future.result(timeout=150)` — hung tools timed out gracefully.
- **Single-tool path** (`len(group) == 1`): `execute_tool()` was called directly with NO timeout. If a tool hung (e.g., `edit_file` in batch mode with lock contention, slow ruff lint, or infinite diff), the entire agent loop froze forever.

This is the far more common case — most tool calls are single-tool groups.

Fix applied (+36 lines): Wrapped the single-tool `execute_tool()` call in a `ThreadPoolExecutor` with the same 150s timeout, `TimeoutError` handling, and `Exception` crash handling as the multi-tool path.

### Modified Files
- `core/llm.py` — `_execute_groups`: single-tool path now uses ThreadPoolExecutor + 150s timeout (+36 lines)
- `CHANGELOG.md` — entry added
- `STATE.txt` — date updated, Active Decisions entry added

### Tests
- `tests/test_llm.py`: 22/22 pass
- `tests/test_file_ops_extended.py`: 80/80 pass
- Total: 102/102 pass

### What's Pending
- No pending items
