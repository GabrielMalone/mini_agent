# HANDOFF — 2026-06-16

## What I changed
- **Hash-anchored editing** (`tools/file_ops.py`): `_line_hash()`, `read_file(hash_lines=True)`, `edit_lines()` tool
  - 3-char SHA-256 hash per line. All hashes validated before any write. Bottom-up application.
  - Pattern from Howard Chen's cwcode / Akay's hashline design.
- **Storm-breaker** (`core/llm.py`): tracks 3+ consecutive identical failed tool calls, synthesizes
  assistant-role message instead of silent continue. "Don't crash, talk" pattern.
- **Schema**: `tools/schema.py` — `hash_lines` param on read_file, new `edit_lines` tool definition
- **Tests**: 17 new tests in `tests/test_file_ops_extended.py` (11 Hashlines + 6 StormBreaker)

## What's pending
- The `edit_lines` tool is available but the system prompt doesn't yet guide the model to prefer it
  over `edit_file`. Consider adding a note in `core/prompt.py` suggesting `read_file(hash_lines=True)`
  followed by `edit_lines` for complex multi-line edits.
- Storm-breaker only triggers on 3 consecutive identical failures. Could be extended to detect
  "edit/retry loop" patterns (edit → read → same edit) even when not 100% identical.
- The `read_file` offset parameter is documented as 0-indexed but behaves as 1-indexed in practice.
  A follow-up could fix this or update the docstring.

## Modified files
- `tools/file_ops.py` (+194 lines)
- `tools/schema.py` (+36 lines)
- `core/llm.py` (+69 lines)
- `tests/test_file_ops_extended.py` (+263 lines)
- `STATE.txt` (updated)
- `CHANGELOG.md` (updated)
