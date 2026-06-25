# HANDOFF — 2026-06-25 (Storm breaker auto-recovery audit)

## What I changed
### Storm breaker auto-recovery
- **`core/llm.py`**: `_check_storm_breaker()` now returns a 4-tuple `(triggered, name, error, count)`.
  Added `_STORM_TRIGGER_COUNT` global counter. On first trigger (count=1), the storm breaker
  injects the synthesized message and **continues the loop** instead of returning — letting the
  agent self-correct. On second trigger (count=2), it escalates and returns to the user as before.
  This is a safety valve against infinite loops.
- **`tests/test_file_ops_extended.py`**: Updated all existing `TestStormBreaker` tests for the
  new 4-tuple return signature. Added 2 new tests:
  - `test_storm_breaker_auto_recovery_first_trigger`: verifies count=1 on first trigger,
    count=2 on second trigger
  - `test_storm_breaker_triggers_again_after_reset`: verifies escalation across separate
    failure batches

## What's pending
- None from this session

## Modified files
- `core/llm.py` (+8 lines)
- `tests/test_file_ops_extended.py` (+56 lines)
- `HANDOFF.md` (this file)

## Commits
- `3ab551c` fix(frontend): harden file-result regexes against edge-case paths
- `a474b62` fix(frontend): prevent PlanPanel content overlap with chat area

---

## What I changed
- **`mini_agent_electron/renderer/src/App.tsx`**: Two fixes for stuck tool cards:
  1. **Watchdog useEffect**: polls every 5s, auto-resolves cards stuck 'running' >30s → 'ok'
  2. **Hardened tool_end fallback matching**: old code had `(tCallId && ID match) || (!tCallId && name match)` 
     which silently dropped tool_end when tCallId was present but no ID-matched card existed.
     Now: exact ID → tool_name → any running card (3 tiers). Also added "any running card" 
     last-resort tier.

## What's pending
- None on this bug. Watchdog is defense-in-depth; the fallback fix addresses the root race condition.
- Pre-existing: `tests/test_tools.py::TestSearchFiles::test_outside_workspace_allowed` fails (unrelated).

## Modified files
- `mini_agent_electron/renderer/src/App.tsx` (+39 lines: watchdog + fallback fix)
- `CHANGELOG.md` (entry added)
- `STATE.txt` (entry added)
- `HANDOFF.md` (this file)

---

# HANDOFF — 2026-07-13 (Session 3)

## What I changed
- Committed the stuck tool-card watchdog from Session 2 (commit `11072c7`):
  - `mini_agent_electron/renderer/src/App.tsx`: 10s poll → auto-resolve "running" cards >30s old → "ok"
  - `CHANGELOG.md`: entry added
- No new code changes this session

## What's pending
- Runtime verification: confirm stuck cards no longer appear in agent sessions
- Consider also adding a backend-side fix to ensure tool_end is always sent for every
  tool_start (the current frontend fix is a safety net, not a root-cause fix)

## Modified files (committed)
- `mini_agent_electron/renderer/src/App.tsx`
- `CHANGELOG.md`
- `HANDOFF.md`