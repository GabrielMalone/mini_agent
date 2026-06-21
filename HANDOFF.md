# HANDOFF — 2026-06-21

## What I Changed

### replace_symbol byte-offset corruption fix
- **Root cause**: tree-sitter returns byte offsets into UTF-8 bytes, but `_collect_python_definitions` and `replace_symbol` applied them directly to Python strings. For ASCII files byte offset == char index (works by accident), but any non-ASCII character before the target symbol causes offsets to diverge.
- **Fix**: `_collect_python_definitions` now accepts `str | bytes` with an `_extract()` helper that slices correctly. `replace_symbol` works entirely in bytes (parse bytes → collect from bytes → splice bytes → decode).
- **New tests**: `tests/test_replace_symbol_nonascii.py` — 4 tests (ASCII regression, single multibyte, multiple multibyte+emoji, method in class)

### Prompt audit bug fixes (4 bugs squashed)
- **Turn counter stuck at 0**: `api.py` was reading a stale module-level import (`from llm import _turn_counter`). Fixed to read `_TOOL_CONTEXT._turn_count` directly (with hasattr guard), matching how other injectors access it.
- **Nudge spam**: `_inject_plan_status` and `_inject_tool_graph_context` fired every single turn, injecting massive transient messages (~70 turns). Added cooldowns: plan_status every 5 turns, tool_graph first + every 20 turns.
- **_transient missing from prompt log**: `_clean_message()` strips all `_`-prefixed keys including `_transient` before log entry is written. Added `transient_count` field computed from raw messages before cleaning.
- **Scratchpad leak**: Unlimited scratchpad content injected into context window. Added 3,000-character truncation with notice.

### replace_symbol end-to-end fix (tree-sitter v0.23+)
- **`tools/ast_tools.py`**: 
  - Added `isinstance(captures, dict)` check for tree-sitter v0.23+ `Query.captures()` API change
  - Added missing imports: `_FILE_CACHE`, `get_tracker`
- **`tests/test_replace_symbol_e2e.py`**: New e2e test (83 lines)

### Edit System Consolidation
- **`tools/_file_utils.py`**: Added `_finalize_edit()` shared helper (65 lines)
- **`tools/_edit_ops.py`**: Refactored three edit paths to call `_finalize_edit` (-75 lines)

## Files Modified
- `api.py` — +1 line (turn counter read fix), +2 lines (transient_count in prompt log)
- `core/context_inject.py` — +68/-20 lines (nudge cooldowns, scratchpad truncation)
- `tools/ast_tools.py` — +13 lines (v0.23+ captures fix + missing imports)
- `tools/_edit_ops.py` — -75 lines (consolidated into _finalize_edit)
- `tools/_file_utils.py` — +65 lines (_finalize_edit helper)
- `tests/test_replace_symbol_e2e.py` — new file (+83 lines)

## Test Results
- 1348 passed, 0 failures (full suite)
- 3 new e2e tests pass (test_replace_symbol_e2e.py)

## Commits
- `dc42187` — fix: turn counter, nudge spam, transient logging, scratchpad leak
- `4fa7971` — fix: clean code audit - ruff format, lint fixes
- `f1c3306` — tree-sitter v0.23+ compatibility
- `a042f83` — fix: add missing imports to replace_symbol

## Pending
- None
