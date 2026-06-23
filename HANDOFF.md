# Session Handoff
# Auto-generated at session end. Read at next session start for continuity.

## Last Session: 2026-06-23 14:00 UTC

### What I Changed

**Fix: Parallel tool hang prevention — timeout on future.result()**

Root cause: `_execute_groups` used `as_completed(futures)` with `future.result()` (no timeout). If one parallel tool thread hung (e.g., git checkpoint lock contention), the loop blocked forever, freezing the agent.

Fix: Added `future.result(timeout=150)` with TimeoutError and Exception handling. Timed-out tools return a failure ToolResult; crashed threads are caught gracefully.

File: **core/llm.py** `_execute_groups` (+37 lines, -1 line)

**Fix: Pass 2 orphan stripping leaves tool messages behind — API 400**

Root cause: `_strip_orphaned_tool_messages` Pass 2 stripped orphaned `assistant(tool_calls)` but left their tool messages in the list. Pass 3 then appended those orphaned tool messages via its `else` branch (pending_ids empty), causing 400 errors: "role 'tool' must be a response to a preceding message with 'tool_calls'".

Fix: Pass 2 now collects ALL `tool_call_id`s of stripped assistants into `orphaned_tc_ids` and filters those tool messages from pass2 output.

File: **memory/memory_prune.py** `_strip_orphaned_tool_messages` (+17 lines, -1 line)

### Verification
- Python syntax checks: PASS
- llm tests (22): PASS
- file_ops_extended tests (80): PASS
- memory + API tests (76): PASS
- Broad test suite (123): 122 passed, 1 pre-existing failure (test_ast_tools tree-sitter deprecation)
- Targeted scenario test (Pass 2 orphan tool message survival): PASS — orphaned tool(A) and tool(B) correctly stripped, tool(C) survives

### What's Pending
- The ongoing API 400 errors should be resolved by the Pass 2 fix. Monitor api_error.log for new occurrences.
- `tool_output` streaming during parallel execution still has the LIFO matching issue (cosmetic, not urgent).

### Modified Files
- core/llm.py
- memory/memory_prune.py
- CHANGELOG.md
- STATE.txt
