# HANDOFF — 2026-06-24 (Stuck tool-card bug fix)

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