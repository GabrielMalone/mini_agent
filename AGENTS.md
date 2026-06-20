# Agent Rules

Behavioral rules the agent MUST follow. Architecture facts go in STATE.txt.
Long-term facts and preferences go in core memory.

## Git Operations
- The `git` and `diff` tools have been **removed**. All git commands go through `run_shell`:
  - `git status --short`
  - `git diff`
  - `git add -A`
  - `git commit -m "..."`
  - `git push origin branch:branch`
  - `git log --oneline`

## Read/Write Guardrails (ACI)
- **Read-before-edit**: MUST `read_file` any `.py` file before editing it. New files exempt.
  Tracked via `_READ_FILES` set. Rejects hallucinated edits to unseen code.
- **Syntax validation**: EVERY `.py` write/edit passes through `compile()` first.
  SyntaxErrors are caught BEFORE disk write. Returns exact line number + pointer.
- **Workspace isolation**: All reads/writes bounded to workspace directory.
- **Plan-before-edit**: Declare a plan (`plan` tool) before multi-step code changes.
  Steps auto-complete on file writes.

## Shell Safety
- **Dangerous command detection**: 9 patterns blocked by default (`rm -rf`, `git push --force`,
  `sudo`, `chmod 777`, `dd`, `mkfs`, raw disk redirect, `format`). Requires `force=True`.
- **Empty output**: Shell commands with exit 0 + no output return `"Command completed successfully
  (no output)."` — never an empty string.
- **Search overflow**: When search_files hits the 200-result cap, guide toward precision:
  "use a more specific pattern, subdirectory, or find_symbol."

## Tool Result Conventions
- **Per-result budget**: Individual tool results truncated at 8000 chars during compression.
  Truncated results include offset guidance.
- **Tool cache TTL**: 30-second TTL on cached `read_file` results. Writes invalidate cache.

## Post-Edit Verification
- After editing files, check callers via knowledge graph (`find_callers_of_file`).
- Post-edit verification injection fires every 6 turns + whenever new files modified since last check.
- Git blame included in edit risk briefing (top authors per file).

## Confidence & Knowledge
- **Confidence nudge**: After 3+ search misses, 2+ tool failures, or 6+ read-only turns,
  agent is nudged to use `web_search`. 4-turn cooldown.
## Unreal Engine MCP

When the user wants to work with Unreal Engine, check if `mcp_discover` shows
`unreal-python-mcp` tools (`search_unreal_api`, `get_class_overview`, `exec_unreal_python`).
If not, they need to add the server to `.mini_agent.toml` (see README → MCP Servers).

Workflow for Unreal tasks:
1. `search_unreal_api("<query>")` — find relevant classes/functions
2. `get_class_overview("<ClassName>")` — list all methods/properties
3. `get_member_info("<ClassName>", "<method>")` — get exact signature + docs
4. `exec_unreal_python("<code>")` — run the correct code

NEVER guess Unreal Python API names — always search first.

Use `unreal.get_editor_subsystem()` for editor subsystems (EditorActorSubsystem,
EditorAssetSubsystem, etc.) rather than deprecated global functions.

## Memory Architecture
- **Core memory**: Long-term persistent facts, preferences, conventions (SQLite).
- **AGENTS.md**: Behavioral rules only (this file).
- **STATE.txt**: Architecture map + task index.
- **CHANGELOG.md**: Historical audit trail.

## Provider Fallback
- On 429/5xx, auto-failover to fallback providers (DeepSeek → Claude).
- Provider-specific params stripped from fallback payloads.
- Streaming disabled during fallback.

## Prompt Cache
- DeepSeek requests set `prompt_cache_key: mini_agent-v1-{tool_count}` for KV-cache stickiness.

## Memory Pruning
- Two-tier: gentle zone (last 7-20 msgs, truncate at 16K chars/20 lines), aggressive zone (21+, per-tool-type compression).
- System prompt (index 0) NEVER compressed/pruned — critical for API prompt caching.
