# mini_agent

A coding agent powered by DeepSeek V4 Pro with 24 tools. Runs as a terminal REPL or a Textual TUI.

## Features

- **24 tools**: file operations, shell commands, search, git, web search, semantic search, symbol lookup, multi-agent delegation, test running, and more
- **Streaming**: token-by-token responses with live tool output
- **Two interfaces**: terminal REPL (`python mini_agent.py`) or rich TUI (`python tui.py`)
- **Safety layer**: workspace isolation, destructive command guard, overwrite protection
- **Multi-agent**: spawn sub-agents for parallel task execution
- **Memory**: SQLite-backed conversation store with token-aware pruning
- **316 tests**, all passing

## Quick Start

```bash
# 1. Clone
git clone https://github.com/GabrielMalone/mini_agent.git
cd mini_agent

# 2. Set up a virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# or: venv\Scripts\activate  # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your API key (choose one)
export DEEPSEEK_API_KEY="sk-your-key-here"
# or copy and edit the config file:
cp .mini_agent.toml.example .mini_agent.toml
# then edit .mini_agent.toml with your keys

# 5. Run
python tui.py          # Textual TUI (recommended)
# or
python mini_agent.py   # Terminal REPL
```

## Configuration

Settings are loaded from (in priority order):
1. CLI flags (e.g. `--stream`, `--quiet`)
2. Environment variables (`DEEPSEEK_API_KEY`, `AGENT_WORKSPACE`, `EXA_API_KEY`)
3. `.mini_agent.toml` in the workspace root

Copy `.mini_agent.toml.example` to `.mini_agent.toml` for local configuration.

### CLI Flags

| Flag | Description |
|------|-------------|
| `--workspace PATH` | Set workspace root (default: cwd) |
| `--stream` | Stream responses token-by-token |
| `--quiet` | Suppress tool execution logs |
| `--no-color` | Disable ANSI colours |
| `--approve` | Ask confirmation before write/destructive tools |
| `--allow-overwrites` | Allow overwriting existing files |
| `--unrestricted` | Remove workspace boundary checks |
| `--help, -h` | Show help |

## Running Tests

```bash
python -m pytest
# 316 tests in ~6 seconds
```

## Architecture

```
mini_agent/
  mini_agent.py     Terminal REPL entry point
  tui.py            Textual TUI interface
  config.py         Configuration loading (TOML, env, CLI)
  llm.py            API calls, agent loop, circuit breaker
  safety.py         File read/write safety gates
  memory.py         SQLite-backed conversation store
  prompt.py         System prompt template
  stream.py         SSE stream parser
  retry.py          API retry with exponential backoff
  sub_agent.py      Sub-agent spawning
  agent_runtime.py  Sub-agent registry
  terminal.py       ANSI colour helpers
  tools/
    __init__.py     Tool dispatch, registration
    file_ops.py     read/write/edit/list/diff/restore
    shell_ops.py    run_shell, search_files, run_tests, git
    search_ops.py   find_symbol, find_usages, semantic_search, web_search
    agent_ops.py    spawn_agent, agent_status, collect_agent
    schema.py       Tool JSON schemas
```

## License

MIT
