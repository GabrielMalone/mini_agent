#!/usr/bin/env python3
"""
mini_agent — a coding agent powered by DeepSeek V4 Pro with 11 tools.

All file reads and writes go through the safety layer (safety.py).
Memory persists between sessions via SQLite (memory.py).
Tools are defined and executed in tools.py.
Config lives in .mini_agent.toml (config.py).
LLM communication is handled by llm.py.
The system prompt lives in prompt.py.

Flags:
  --workspace PATH    Set workspace root (default: current directory)
  --stream            Stream responses token-by-token (default: off)
  --quiet             Suppress tool execution logs
  --no-color          Disable ANSI colours in output
  --help, -h          Show this message and exit

Session commands (type at the prompt):
  quit                Save memory and exit
  clear               Reset conversation memory

Configuration:
  Set EXA_API_KEY in your environment or .mini_agent.toml for web search.
  See STATE.txt for architecture overview and tool reference.
"""

import json
import os
import sys
import time

import requests

from config import AgentConfig, CONFIG_FILENAME
from llm import run_agent_turn
from prompt import SYSTEM_PROMPT
from safety import ReadSafetyGate, WriteSafetyGate
from memory import MemoryStore
from terminal import c, _DIM, _CYAN, _YELLOW, _GREEN, _RED
from tools import set_context, build_symbol_index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _approve(tool_name: str, args: dict) -> bool:
    """Ask the user to approve a write/destructive tool call."""
    from terminal import c, _YELLOW, _RED
    brief = json.dumps(args)
    if len(brief) > 100:
        brief = brief[:100] + "..."
    prompt = f"  {c('Allow', _YELLOW)} {tool_name}({brief})? [y/N] "
    try:
        answer = input(prompt).strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _log(verbose: bool, *args, **kwargs) -> None:
    """Print diagnostic output, unless verbose is disabled.  Always flushes."""
    if verbose:
        kwargs.setdefault("flush", True)
        print(*args, **kwargs)


def _export_conversation(messages: list[dict], workspace: str) -> str:
    """Write conversation to a timestamped markdown file."""
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"conversation_{ts}.md"
    path = os.path.join(workspace, fname)

    blocks: list[str] = []
    blocks.append(f"# mini_agent conversation — {ts}\n")
    for m in messages:
        role = m.get("role", "?")
        if role == "system":
            blocks.append(f"### System\n\n{m.get('content', '')}\n")
        elif role == "user":
            blocks.append(f"### User\n\n{m.get('content', '')}\n")
        elif role == "assistant":
            content = m.get("content", "")
            if m.get("reasoning_content"):
                blocks.append("> **Thinking**\n>")
                for line in m["reasoning_content"].split("\n"):
                    blocks.append(f"> {line}")
                blocks.append("")
            if content:
                blocks.append(f"### Assistant\n\n{content}\n")
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    args = fn.get("arguments", "{}")
                    blocks.append(f"```\n{name}({args})\n```\n")
        elif role == "tool":
            blocks.append(f"> Tool result:\n>\n> {m.get('content', '')[:500]}\n")

    with open(path, "w") as f:
        f.write("\n".join(blocks))
    return path


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def resolve_workspace() -> str:
    """Resolve workspace root from CLI arg, env var, or default to cwd."""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--workspace" and i + 1 < len(args):
            return args[i + 1]
    return os.environ.get("AGENT_WORKSPACE", os.getcwd())


def main() -> None:
    # --help
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return

    workspace = resolve_workspace()
    config = AgentConfig.load(workspace)

    write_gate = WriteSafetyGate(config.workspace, allow_overwrites=config.allow_overwrites)
    read_gate = ReadSafetyGate(config.workspace)
    memory_path = os.path.join(config.workspace, config.memory_filename)
    memory = MemoryStore(memory_path, max_messages=config.max_messages, max_tokens=config.max_tokens)
    set_context(exa_api_key=config.exa_api_key, scratchpad_path=memory._db_path)
    _log(config.verbose, "Indexing workspace symbols...")
    build_symbol_index(workspace)
    _log(config.verbose, f"Workspace indexed.")

    # Restore previous session (system prompt is always fresh)
    saved = memory.load()
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if saved:
        messages.extend(saved)
        _log(config.verbose, f"(restored {len(saved)} messages from previous session)")

    _log(config.verbose, f"mini_agent — workspace: {write_gate.workspace_root}")
    _log(config.verbose, f"model: {config.model}  stream: {config.stream}")
    if os.path.isfile(os.path.join(config.workspace, CONFIG_FILENAME)):
        _log(config.verbose, f"config: {CONFIG_FILENAME} loaded")
    _log(config.verbose, "Type 'quit' to exit, 'clear' to reset memory, --help for flags.")
    if not config.verbose:
        _log(config.verbose, "(quiet mode — use without --quiet to see tool execution)")
    _log(config.verbose)

    session = requests.Session()
    try:
        while True:
            try:
                user_input = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                memory.save(messages)
                break

            if user_input.lower() == "quit":
                memory.save(messages)
                break

            if user_input.lower() == "clear":
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                memory.clear()
                _log(config.verbose, "Memory cleared.\n")
                continue

            if user_input.lower() == "/export":
                path = _export_conversation(messages, workspace)
                print(f"Exported to {path}")
                continue

            if not user_input:
                continue

            # ----- User turn -----
            messages.append({"role": "user", "content": user_input})

            # ----- Agent turn -----
            _log(config.verbose,
                 f"  {c('⏳', _CYAN)} calling API…", file=sys.stderr)
            t0 = time.monotonic()

            def _tool_start(summary: str, parallel: bool = False) -> None:
                nonlocal t0
                elapsed = time.monotonic() - t0
                _log(config.verbose,
                     f"  {c('←', _YELLOW)} tool call(s) after {elapsed:.1f}s",
                     file=sys.stderr)
                _log(config.verbose,
                     f"  {c('🔧', _YELLOW)} {summary}",
                     file=sys.stderr)

            def _tool_end(ok: bool, detail: str) -> None:
                if ok:
                    _log(config.verbose,
                         f"     {c('✓', _GREEN)}  ok",
                         file=sys.stderr)
                else:
                    _log(config.verbose,
                         f"     {c('✗', _RED)}  FAILED: {c(detail, _RED)}",
                         file=sys.stderr)

            msg = run_agent_turn(
                messages, config, write_gate, read_gate,
                on_tool_start=_tool_start,
                on_tool_end=_tool_end,
                session=session,
                memory_store=memory,
                approve_callback=_approve if config.approve_write_ops else None,
            )
            elapsed = time.monotonic() - t0

            if msg is not None and not msg.get("tool_calls"):
                if not config.stream:
                    print(msg.get("content", ""))
                _log(config.verbose,
                     f"  {c('←', _DIM)} text response ({elapsed:.1f}s)",
                     file=sys.stderr)

            # Persist after every turn
            memory.save(messages)
            _log(config.verbose, c("─" * 50, _DIM), file=sys.stderr)
    finally:
        session.close()


if __name__ == "__main__":
    main()
