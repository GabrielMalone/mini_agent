# mini_agent Module Map
# Auto-generated reference of the codebase structure.
# See .mini_agent.rules for behavioral conventions.

## core/
  constants.py           — shared constants (no project imports, safe for any module)
  prompt.py              — system prompt, personality rules
  config.py              — AgentConfig (TOML + env + CLI), startup context
  llm.py                 — API calls, turn orchestration, tool piping
  safety.py              — workspace read/write gates (SafetyResult dataclass)
  bootstrap.py           — session init, workspace setup
  context_inject.py      — per-turn context injection (handoff, state, git diff, etc.)
  codebase_map.py        — AST-based symbol extraction for startup context
  knowledge_graph.py     — entity-relationship graph (calls, imports, defs)
  tree_sitter_parser.py  — multi-language source parsing (Python/JS/TS)

## memory/
  memory.py              — MemoryStore: conversations, knowledge, scratchpad
  memory_prune.py        — content-aware compression, orphan stripping
  session.py             — session lifecycle

## agents/
  agent_runtime.py       — sub-agent lifecycle, inboxes, file reservations
  sub_agent.py           — sub-agent engine, turn budget, pruning, Pro model

## Top-level modules
  api.py                   — LLM API calls, message cache
  interject.py             — thread-safe user interjection queue
  terminal.py              — ANSI colour helpers
  retry.py                 — HTTP retry with jitter and backoff
  stream.py                — SSE stream parser
  logging_setup.py         — structured logging
  discord_bot.py           — Discord bot (emotion game + workspace assistant)
  voice_handler.py         — TTS voice synthesis (ElevenLabs, macOS say)
  workspace_bot.py         — workspace assistant bot entry point

## tools/
  __init__.py            — dispatch, cache, ToolResult, JSON repair
  schema.py              — TOOLS definitions sent to LLM
  file_ops.py            — read/write/edit/list/info/scratchpad/diff/plan
  shell_ops.py           — run_shell, run_tests, search_files, git, task_status, verify
  search_ops.py          — find_symbol, find_usages, semantic_search, web_search, recall_turn
  agent_ops.py           — extend, cancel, wait, restore, session_stats, recall_turn, remember, read_image
  agent_spawn.py         — sub-agent spawning (spawn_agent, _spawn_one)
  agent_collect.py       — sub-agent status & collection (agent_status, collect_agent, collect_any)
  agent_messages.py      — typed message registry, validation, routing
  agent_patterns.py      — fan_out, fan_in, pipeline, barrier, scatter_gather
  agent_todos.py         — per-agent todo tracking, plan, write_scratchpad
  result.py              — ToolResult dataclass
  context.py             — AgentContext, _ContextProxy, _TOOL_CONTEXT
  reservations.py        — file reservation system for sub-agent write collision prevention
  skills.py              — lazy tool loading via skill gates (11 core tools, 11 skill groups)
  error_hints.py         — error hint generation and failure fingerprinting
  failure_learning.py    — failure pattern store, self-critique, project knowledge
  tool_graph.py          — tool transition graph for sequencing hints
  lsp.py                 — LSP client (pylsp integration)
  mcp_client.py          — MCP client (stdio JSON-RPC tool discovery)
  _json_rpc_shared.py    — shared drain_stderr() for LSP and MCP clients
  desktop_ops.py         — desktop automation (macOS Atomacos, Windows UIA)
  macos_ops.py           — macOS-specific tools (apps, windows, clipboard, keys)
  browser_ops.py         — Playwright headless browser automation
  semantic_cache.py      — semantic response cache (cosine-sim match, 15-25% cost reduction)
  memory_consolidation.py — background knowledge consolidation (extract facts, apply to core)
  memory_core.py         — core memory operations (read, add, replace, remove)

## skills/
  Hermes-style skill definitions (SKILL.md with YAML frontmatter)

## Other files
  conftest.py              — shared test fixtures and mocks
  STATE.txt                — architecture decisions, current state (consult before major changes)
  HANDOFF.md               — session handoff (read at startup, write at session end)
  CHANGELOG.md             — self-modification audit trail (update after significant changes)
  TASKS.md                 — task-to-file mapping index
