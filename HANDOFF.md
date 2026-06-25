# HANDOFF — 2026-06-25 (Tool Panel UI Audit)

## What I changed
Audited the tool panel UI in App.tsx. Found that handleCancel already had full tool state cleanup from a previous session. Added two missing safety valves:

1. **`turn_complete` handler** (+2 lines): Added `toolOutputStack.current.length = 0` after the final card sweep. Previously only tool cards were swept to 'ok', but stale stack entries could persist and incorrectly intercept tool_output in the next turn.

2. **`handleSubmit` for new turns** (+7 lines): Added tool state reset (flushing `toolOutputStack`, `orphanOutputs`, and clearing `orphanTimeoutRef`) before starting a new agent turn. Previously, if a user cancelled a turn and started a new one, stale orphan buffers and stack entries could race with tool_start and route output to the wrong card.

## What was already fixed (previous sessions)
- `handleCancel` already clears all tool state (stack, orphans, timeout, marks running cards as 'err')
- Watchdog (10s poll) auto-resolves cards stuck >30s
- 3-tier tool_end fallback (exact ID → tool_name → any running)
- Card cap at 50 with index cleanup

## Verified
- `tsc --noEmit`: clean (0 errors)
- `npx vitest run`: 2/2 passed

## What's pending
- Orphan timeout (5s) silently drops output — consider creating fallback cards for orphaned output
- No toolOutputStack cleanup in slash-command path of handleSubmit (slash commands create blocks, not turns, so low risk)

## Modified files
- `mini_agent_electron/renderer/src/App.tsx` (+9 lines)
