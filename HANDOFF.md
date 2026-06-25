# HANDOFF — 2026-06-25 (edit_file hang on frontend files fix)

## Root Cause Investigation

The `edit_file` tool hung when editing frontend files (.tsx/.ts) because `_finalize_edit()`
in `tools/_file_utils.py` unconditionally called `invalidate_file()` from
`core/knowledge_graph.py`. For TypeScript/TSX files, this triggers `_extract_ts_graph()`
which uses **tree-sitter-typescript** to parse the entire file. For large files like
App.tsx (~1000 lines), the TypeScript grammar is complex and parsing blocks the edit
pipeline, making it appear as if the backend hung.

The call was wrapped in try/except so it wouldn't crash, but tree-sitter parsing doesn't
throw — it just takes a long time on large frontend files.

## Fix

Moved knowledge graph invalidation inside the existing `if resolved.endswith(".py"):` block
in `_finalize_edit()`. Only Python files need real-time graph updates — the knowledge graph
is used for Python code understanding/navigation within the codebase.

## What changed
- `tools/_file_utils.py`: +1 line (moved 6 lines into existing `.py` guard, added comment)

## Verified
- `python3 -c "compile(...)"`: syntax OK
- `pytest tests/test_file_ops_extended.py`: 82 passed

## Other call sites checked
- `invalidate_file` is only called from `_finalize_edit()` — no other call sites in tools/
- `codebase_map.update_file_in_map` is NOT called in the edit pipeline

## Previous session (Tool Panel UI Audit) — still pending
- `HANDOFF.md` needs updating (overwritten by this session)

## Modified files
- `tools/_file_utils.py` (+1 line net, knowledge graph invalidation gated to .py only)
