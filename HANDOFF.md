# Session Handoff
# Auto-generated at session end. Read at next session start for continuity.

## Last Session: 2026-06-23 ~16:05 UTC

### What I Changed

**Fix: Input disabled after /sh commands — shell_output handler now re-enables**

Root cause: `/sh` commands send `shell_output` IPC messages, not `backend:response`. The previous fix (commit 69eaf05) only added input re-enable logic to the `backend:response` handler. `/sh ls`, `/sh cat`, etc. would leave input frozen until the 120s timeout expired.

Fix: Added `clearTimeout(submitTimeoutRef)`, `setIsLive(false)`, `setInputDisabled(false)`, and `inputRef.current?.focus()` to the `stream:shell_output` handler, guarded by `data.exit_code !== undefined` (only fires on the final message from the backend).

The two fixes together cover all slash commands:
- `backend:response` handler (69eaf05): covers `/clear`, `/stats`, `/session`, `/export`, `/cancel`, `/help`, unknown commands
- `stream:shell_output` handler (61fa8c7): covers `/sh <command>`

### Modified Files
- `mini_agent_electron/renderer/src/App.tsx` — `stream:shell_output` handler: +6 lines (input re-enable)
- `CHANGELOG.md` — entry added

### Tests
- Build + smoke test: PASS
- TypeScript: 0 new errors (pre-existing errors only)

### What's Pending
- None
