# Session Handoff
# Auto-generated at session end. Read at next session start for continuity.

## Last Session: 2026-06-23 13:18 UTC

### What I Changed

**Fix: Batch edit_file parallel tool card matching bug**

Root cause: When tools run in parallel via ThreadPoolExecutor, `on_tool_end` fires in completion order (not start order). The frontend used a LIFO stack (`toolOutputStack`) to match `tool_start→tool_end`, so parallel tools completing out of start order updated the wrong cards — breaking the UI.

Files changed:
- **core/llm.py** `_append_tool_result`: Extract `tool_name` from `tc["function"]["name"]` and pass to `on_tool_end` callback.
- **mini_agent_electron/backend/server.py** `StreamCallbacks.on_tool_end`: Added `tool_name` parameter, forwarded in IPC msg dict.
- **mini_agent_electron/renderer/src/App.tsx**: `tool_start` stores `toolName` in stack entry; `tool_end` matches by `data.tool_name` via `findIndex+splice`, with LIFO fallback for backward compatibility. Fixed `toolOutputStack` useRef type to include `toolName: string`.
- **CHANGELOG.md**: Documented the fix.
- **STATE.txt**: Added active decision entry.

### Verification
- Python syntax checks: PASS
- llm.py tests: 22/22 PASSED
- file_ops_extended tests: 80/80 PASSED
- Broad test suite: 122 passed, 1 pre-existing failure (test_ast_tools tree-sitter)
- TypeScript: My App.tsx `toolName` errors resolved; remaining errors are pre-existing

### What's Pending
- `tool_output` streaming during parallel execution has the same matching issue (output lines go to the last-started tool). Not urgent since `edit_file` doesn't stream output, and most tools run sequentially. Would require deeper refactoring of the `on_output` callback chain to carry tool identity.

### Modified Files
- CHANGELOG.md
- STATE.txt
- core/llm.py
- mini_agent_electron/backend/server.py
- mini_agent_electron/renderer/src/App.tsx
