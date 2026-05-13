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
  --workspace PATH       Set workspace root (default: current directory)
  --stream               Stream responses token-by-token (default: off)
  --quiet                Suppress tool execution logs
  --no-color             Disable ANSI colours in output
  --allow-overwrites     Allow overwriting existing files without confirmation
  --approve              Prompt for approval before each write/destructive op
  --help, -h             Show this message and exit

Environment:
  AGENT_WORKSPACE        Workspace root (overridden by --workspace)
  DEEPSEEK_API_KEY       API key for the LLM provider
  EXA_API_KEY            API key for web search (Exa)

Config file (.mini_agent.toml) can set model, stream, and other defaults.

Session commands (type at the prompt):
  quit                Save memory and exit
  clear               Reset conversation memory
  /cancel             Cancel the current agent turn (threaded mode)
  /export             Export conversation to markdown
  /stats              Show session statistics

Configuration:
  Set EXA_API_KEY in your environment or .mini_agent.toml for web search.
  See STATE.txt for architecture overview and tool reference.
"""

import json
import os
import sys
import threading
import time

import requests

from config import AgentConfig, CONFIG_FILENAME, resolve_workspace, init_session, parse_args
from llm import run_agent_turn
from prompt import SYSTEM_PROMPT
from safety import ReadSafetyGate, WriteSafetyGate
from memory import MemoryStore
from terminal import c, _DIM, _CYAN, _YELLOW, _GREEN, _RED
from tools import set_context, build_symbol_index
from interject import push_interjection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _approve(tool_name: str, args: dict) -> bool:
    """Ask the user to approve a write/destructive tool call."""
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
    from memory import export_conversation_markdown
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"conversation_{ts}.md"
    path = os.path.join(workspace, fname)
    md = export_conversation_markdown(messages)
    md = md.replace("mini_agent conversation", f"mini_agent conversation — {ts}", 1)
    with open(path, "w") as f:
        f.write(md)
    return path


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    # Parse with argparse (gives --help for free)
    cli = parse_args()

    workspace = resolve_workspace(override=cli.workspace)
    session_data = init_session(workspace, cli_args=cli)
    config = session_data["config"]
    write_gate = session_data["write_gate"]
    read_gate = session_data["read_gate"]
    memory = session_data["memory"]
    messages = session_data["messages"]

    _log(config.verbose, f"mini_agent — workspace: {write_gate.workspace_root}")

    # Session stats
    stats = {"turns": 0, "tool_calls": 0}

    _log(config.verbose, f"model: {config.model}  stream: {config.stream}")
    if os.path.isfile(os.path.join(config.workspace, CONFIG_FILENAME)):
        _log(config.verbose, f"config: {CONFIG_FILENAME} loaded")
    _log(config.verbose, "Type 'quit' to exit, 'clear' to reset memory, --help for flags.")
    _log(config.verbose, "  /cancel — stop the current turn   /export — save conversation")
    if not config.verbose:
        _log(config.verbose, "(quiet mode — use --quiet to suppress tool logs)")
    _log(config.verbose)

    # Lock to guard stats from concurrent mutation by tool callbacks
    _stats_lock = threading.Lock()

    # Threaded mode: agent runs in a background thread so the REPL is always
    # available for user input.  User messages typed while the agent is busy
    # are pushed to the interjection queue and surfaced at the next tool-call
    # boundary.
    #
    # When --approve is active the approve callback needs stdin, which would
    # conflict with the main thread's input().  In that case we fall back to
    # the original synchronous single-threaded loop (below).
    _use_threaded = not config.approve_write_ops

    session = session_data["session"]

    if not _use_threaded:
        # ---- synchronous fallback (--approve mode) ----
        _log(config.verbose, "(approve mode — synchronous, no interjection)")
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
                    if stats["turns"] > 0:
                        print(f"Session: {stats['turns']} turns, {stats['tool_calls']} tool calls")
                    break

                if user_input.lower() == "clear":
                    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                    memory.clear()
                    stats = {"turns": 0, "tool_calls": 0}
                    _log(config.verbose, "Memory cleared.\n")
                    continue

                if user_input.lower() == "/export":
                    path = _export_conversation(messages, workspace)
                    print(f"Exported to {path}")
                    continue

                if user_input.lower() == "/stats":
                    print(f"Turns: {stats['turns']}  Tool calls: {stats['tool_calls']}  Messages: {len(messages)}")
                    continue

                if not user_input:
                    continue

                # Show scratchpad
                sp = memory.get_scratchpad()
                if sp.strip():
                    _log(config.verbose, f"  {c('📝 scratchpad:', _DIM)}")
                    for line in sp.strip().split("\n"):
                        _log(config.verbose, f"  {c(line, _DIM)}")
                    _log(config.verbose)

                messages.append({"role": "user", "content": user_input})

                _log(config.verbose, f"  {c('⏳', _CYAN)} calling API…", file=sys.stderr)
                t0 = time.monotonic()

                def _tool_start_sync(summary: str, parallel: bool = False) -> None:
                    nonlocal t0
                    stats["tool_calls"] += 1
                    elapsed = time.monotonic() - t0
                    _log(config.verbose,
                         f"  {c('←', _YELLOW)} tool call(s) after {elapsed:.1f}s",
                         file=sys.stderr)
                    _log(config.verbose,
                         f"  {c('🔧', _YELLOW)} {summary}",
                         file=sys.stderr)

                def _tool_end_sync(ok: bool, detail: str) -> None:
                    if ok:
                        _log(config.verbose, f"     {c('✓', _GREEN)}  ok", file=sys.stderr)
                    else:
                        _log(config.verbose, f"     {c('✗', _RED)}  FAILED: {c(detail, _RED)}", file=sys.stderr)

                msg = run_agent_turn(
                    messages, config, write_gate, read_gate,
                    on_tool_start=_tool_start_sync,
                    on_tool_end=_tool_end_sync,
                    session=session,
                    memory_store=memory,
                    approve_callback=_approve,
                )
                elapsed = time.monotonic() - t0

                if msg is not None and not msg.get("tool_calls"):
                    if not config.stream:
                        print(msg.get("content", ""))
                    _log(config.verbose,
                         f"  {c('←', _DIM)} text response ({elapsed:.1f}s)",
                         file=sys.stderr)

                stats["turns"] += 1
                memory.save(messages)
                _log(config.verbose, c("─" * 50, _DIM), file=sys.stderr)
        finally:
            try:
                session.close()
            except Exception:
                pass
        return

    # ---- threaded mode (default) ----
    _log(config.verbose, "(agent runs in background — you can interject at any time)")
    _log(config.verbose)

    _agent_thread: threading.Thread | None = None
    _agent_cancel = threading.Event()
    _agent_msg: dict | None = None
    _agent_error: str | None = None
    _turn_start: float = 0.0

    def _run_turn():
        """Run the agent turn in a background thread."""
        nonlocal _agent_msg, _agent_error, _turn_start

        # Tool callbacks for threaded mode
        def _tool_start(summary: str, parallel: bool = False) -> None:
            with _stats_lock:
                stats["tool_calls"] += 1
            elapsed = time.monotonic() - _turn_start
            _log(config.verbose,
                 f"  {c('←', _YELLOW)} tool call(s) after {elapsed:.1f}s",
                 file=sys.stderr)
            _log(config.verbose,
                 f"  {c('🔧', _YELLOW)} {summary}",
                 file=sys.stderr)

        def _tool_end(ok: bool, detail: str) -> None:
            if ok:
                _log(config.verbose, f"     {c('✓', _GREEN)}  ok", file=sys.stderr)
            else:
                _log(config.verbose, f"     {c('✗', _RED)}  FAILED: {c(detail, _RED)}", file=sys.stderr)

        try:
            _agent_msg = run_agent_turn(
                messages, config, write_gate, read_gate,
                on_tool_start=_tool_start,
                on_tool_end=_tool_end,
                session=session,
                memory_store=memory,
                cancel_event=_agent_cancel,
                approve_callback=None,  # not used in threaded mode
            )
        except Exception as e:
            _agent_error = str(e)

    try:
        while True:
            # --- check if agent finished ---
            if _agent_thread is not None and not _agent_thread.is_alive():
                _agent_thread.join()
                _agent_thread = None
                elapsed = time.monotonic() - _turn_start
                if _agent_error:
                    print(f"\n  {c('Error:', _RED)} {_agent_error}")
                    _agent_error = None
                elif _agent_msg is not None and not _agent_msg.get("tool_calls"):
                    if not config.stream:
                        print(_agent_msg.get("content", ""))
                    _log(config.verbose,
                         f"  {c('←', _DIM)} text response ({elapsed:.1f}s)",
                         file=sys.stderr)
                _agent_msg = None
                stats["turns"] += 1
                memory.save(messages)
                _log(config.verbose, c("─" * 50, _DIM), file=sys.stderr)

            # --- always accept input ---
            try:
                user_input = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                if _agent_thread is not None and _agent_thread.is_alive():
                    _agent_cancel.set()
                    _agent_thread.join(timeout=5)
                memory.save(messages)
                break

            if user_input.lower() == "quit":
                if _agent_thread is not None and _agent_thread.is_alive():
                    _agent_cancel.set()
                    _agent_thread.join(timeout=5)
                memory.save(messages)
                if stats["turns"] > 0:
                    print(f"Session: {stats['turns']} turns, {stats['tool_calls']} tool calls")
                break

            if user_input.lower() == "clear":
                if _agent_thread is not None and _agent_thread.is_alive():
                    _agent_cancel.set()
                    _agent_thread.join(timeout=5)
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                memory.clear()
                stats = {"turns": 0, "tool_calls": 0}
                _log(config.verbose, "Memory cleared.\n")
                continue

            if user_input.lower() == "/export":
                path = _export_conversation(messages, workspace)
                print(f"Exported to {path}")
                continue

            if user_input.lower() == "/stats":
                print(f"Turns: {stats['turns']}  Tool calls: {stats['tool_calls']}  Messages: {len(messages)}")
                continue

            if user_input.lower() == "/cancel":
                if _agent_thread is not None and _agent_thread.is_alive():
                    _agent_cancel.set()
                    _agent_thread.join(timeout=5)
                    _agent_thread = None
                    _agent_msg = None
                    _agent_error = None
                    _agent_cancel.clear()
                    print(f"  {c('Turn cancelled.', _YELLOW)}")
                    memory.save(messages)
                else:
                    print(f"  {c('No active turn to cancel.', _DIM)}")
                continue

            if not user_input:
                continue

            # --- route input ---
            if _agent_thread is not None and _agent_thread.is_alive():
                # Agent is working — queue as interjection
                push_interjection(user_input)
                _log(config.verbose,
                     f"  {c('⏳ queued (agent is working)', _DIM)}",
                     file=sys.stderr)
                continue

            # --- start a new turn ---
            # Show scratchpad
            sp = memory.get_scratchpad()
            if sp.strip():
                _log(config.verbose, f"  {c('📝 scratchpad:', _DIM)}")
                for line in sp.strip().split("\n"):
                    _log(config.verbose, f"  {c(line, _DIM)}")
                _log(config.verbose)

            messages.append({"role": "user", "content": user_input})

            _log(config.verbose,
                 f"  {c('⏳', _CYAN)} calling API…", file=sys.stderr)
            _turn_start = time.monotonic()
            _agent_cancel.clear()
            _agent_msg = None
            _agent_error = None
            _agent_thread = threading.Thread(target=_run_turn, daemon=True)
            _agent_thread.start()

    finally:
        if _agent_thread is not None and _agent_thread.is_alive():
            _agent_cancel.set()
            _agent_thread.join(timeout=5)
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
