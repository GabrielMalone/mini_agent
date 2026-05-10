#!/usr/bin/env python3
"""
mini_agent — a minimal DeepSeek V4 Pro agent that reads and writes files.
All file reads and writes are mediated through the safety layer (safety.py).
Conversation memory is persisted between sessions (memory.py).
Tools are defined and executed in tools.py.
Project configuration is loaded from .mini_agent.toml (config.py).
LLM communication is handled by llm.py.
The system prompt lives in prompt.py.

Usage:
    python mini_agent.py [--workspace /path/to/root] [--quiet] [--stream] [--no-color]
    > read /path/to/file
    > write /path/to/file some content here
    > ls /some/dir
    > run python -m pytest test_safety.py -v
    > clear          (reset memory)
    > quit
"""

import os
import sys
import time

from config import AgentConfig, CONFIG_FILENAME
from llm import call_deepseek
from prompt import SYSTEM_PROMPT
from safety import ReadSafetyGate, WriteSafetyGate
from memory import MemoryStore
from terminal import c, _DIM, _CYAN, _YELLOW, _GREEN, _RED
from tools import execute_tool, tool_summary, set_context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(verbose: bool, *args, **kwargs) -> None:
    """Print diagnostic output, unless verbose is disabled.  Always flushes."""
    if verbose:
        kwargs.setdefault("flush", True)
        print(*args, **kwargs)


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
    memory = MemoryStore(memory_path, max_messages=config.max_messages)
    set_context(exa_api_key=config.exa_api_key)

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
    _log(config.verbose, "Type 'quit' to exit, 'clear' to reset memory.")
    if not config.verbose:
        _log(config.verbose, "(quiet mode — use without --quiet to see tool execution)")
    _log(config.verbose)

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

        if not user_input:
            continue

        # ----- User turn -----
        messages.append({"role": "user", "content": user_input})

        # ----- Tool execution loop (handle multiple rounds) -----
        while True:
            _log(config.verbose,
                 f"  {c('⏳', _CYAN)} calling API…", file=sys.stderr)
            t0 = time.monotonic()
            msg = call_deepseek(messages, config)
            elapsed = time.monotonic() - t0

            if not msg.get("tool_calls"):
                if not config.stream:
                    print(msg.get("content", ""))
                _log(config.verbose,
                     f"  {c('←', _DIM)} text response ({elapsed:.1f}s)",
                     file=sys.stderr)
                messages.append(msg)
                break

            n = len(msg["tool_calls"])
            _log(config.verbose,
                 f"  {c('←', _YELLOW)} {n} tool call(s) after {elapsed:.1f}s",
                 file=sys.stderr)
            messages.append(msg)
            for tc in msg["tool_calls"]:
                _log(config.verbose,
                     f"  {c('🔧', _YELLOW)} {tool_summary(tc)}",
                     file=sys.stderr)
                t0 = time.monotonic()
                result = execute_tool(tc, write_gate, read_gate)
                tool_elapsed = time.monotonic() - t0
                if result.success:
                    _log(config.verbose,
                         f"     {c('✓', _GREEN)}  ok ({tool_elapsed:.2f}s)",
                         file=sys.stderr)
                else:
                    _log(config.verbose,
                         f"     {c('✗', _RED)}  FAILED ({tool_elapsed:.2f}s): "
                         f"{c(result.content, _RED)}",
                         file=sys.stderr)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result.to_json(),
                })

        # Persist after every turn
        memory.save(messages)
        _log(config.verbose, c("─" * 50, _DIM), file=sys.stderr)


if __name__ == "__main__":
    main()
