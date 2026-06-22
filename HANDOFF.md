# Session Handoff
# Auto-generated at session end. Read at next session start for continuity.

## Last Session: 2026-06-22 17:04 UTC

### What I Changed

**replace_symbol decorator byte-range fix** (main task):
- `tools/ast_tools.py` — `_extract_definitions`: walks `prev_sibling` to find decorator nodes and expands byte range to include them. Previously, decorators were left behind (duplicated if replacement included them, dangling if not).
- `tests/test_ast_tools.py` — added `TestReplaceSymbolWithDecorators` class (3 tests): decorated class, decorated function, decorator stripping.

**Prompt token analysis** (ad-hoc query):
- Analyzed `~/.mini_agent/logs/prompts.log`: 918 turns, avg 36,641 tokens/prompt. DeepSeek V4-Pro at ~$0.013/turn is negligible. No action needed.

### What's Pending
None.

### Modified Files
- tools/ast_tools.py (+12 lines in _extract_definitions)
- tests/test_ast_tools.py (+67 lines: TestReplaceSymbolWithDecorators)
- STATE.txt (timestamp update)
- CHANGELOG.md (+8 lines: new entry)
