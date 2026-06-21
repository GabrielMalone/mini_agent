# HANDOFF — 2026-06-21

## What I Changed

### Tool-result truncation (context bomb fix)
- `core/llm.py`: `_append_tool_result()` now caps tool results at `TURN_END_RESULT_CAP_CHARS` (8000 chars) BEFORE appending to messages. Previously large tool results (e.g. `run_shell` returning 244K chars of log output) were appended verbatim, ballooning context 7x (13K → 94K tokens per turn). Truncation preserves first 200 chars + last ~7400 chars with a `[truncated N chars / ~M tokens]` marker.
- Imported `TURN_END_RESULT_CAP_CHARS` from `core.compaction`
- New test: `tests/test_tool_result_truncation.py` — 4 tests (small, large, boundary, head/tail preservation)

### Self-critique cooldown (spam fix) — earlier this session
- `core/context_inject.py`: Added 5-turn cooldown via `_TOOL_CONTEXT._last_self_critique_turn` tracking.

### Thinking UI doubling fix — earlier this session
- `mini_agent_electron/renderer/src/hooks/useSmoothStream.js`: `flush()` now clears `fullRef` and `setDisplayedText('')`.

## Files Modified
- `core/llm.py` — +18 lines (import + truncation logic in `_append_tool_result`)
- `core/context_inject.py` — +12 lines (self-critique cooldown)
- `mini_agent_electron/renderer/src/hooks/useSmoothStream.js` — +5/-3 lines
- `tests/test_tool_result_truncation.py` — new file (+85 lines, 4 tests)
- `CHANGELOG.md` — entries
- `STATE.txt` — date bump

## Commits
- `0bbeb27` — fix: self-critique cooldown + thinking UI doubling
- (pending) — fix: tool-result truncation in _append_tool_result

## Test Results
- 1352 passed (full suite)
- 4 new truncation tests pass

## Pending
- None
