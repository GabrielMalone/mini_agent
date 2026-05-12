#!/usr/bin/env python3
"""
tui.py — Textual TUI frontend for mini_agent.

Usage: python tui.py [--workspace PATH] [--quiet] [--stream] [--allow-overwrites] [--approve]
"""

import os
import sys
import threading
from queue import Queue, Empty
from dataclasses import dataclass


class _NotifyQueue(Queue):
    """Queue that fires an event on every put."""
    def __init__(self, event, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._event = event
    def put(self, item, *args, **kwargs):
        super().put(item, *args, **kwargs)
        self._event.set()


# Escape user content for Rich markup.  rich.markup.escape() skips
# '[' that looks like a valid tag opener (e.g. '[/') — we can't
# trust it, so do our own simple escaping.
def _safe(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", r"\[")

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Header, Footer, RichLog, TextArea
from textual.binding import Binding

import requests

from config import AgentConfig, resolve_workspace, build_startup_context
from llm import run_agent_turn, THINKING_START, THINKING_END
from prompt import SYSTEM_PROMPT
from safety import ReadSafetyGate, WriteSafetyGate
from memory import MemoryStore
from tools import set_context, build_symbol_index


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

BG      = "#111111"
SURFACE = "#1b1b1b"
BORDER  = "#2a2a2a"

ACCENT  = "#8f8f8f"

TEXT    = "#b8b8b8"
DIM     = "#5a5a5a"

GREEN   = "#4f9f6f"
YELLOW  = "#b89a4a"
RED     = "#a85a5a"

CSS = f"""
Screen {{
    background: {BG};
}}

Header {{
    background: {SURFACE};
    color: {ACCENT};
    text-style: bold;
}}

Footer {{
    background: {SURFACE};
    color: {DIM};
}}

#conversation {{
    background: {BG};
    color: {TEXT};
    border: none;
    padding: 0 1;
    height: 1fr;
    scrollbar-background: {BG};
    scrollbar-color: {BORDER};
}}

#input-area {{
    background: {SURFACE};
    padding: 1 2;
    height: auto;
    min-height: 3;
    max-height: 12;
}}

#input {{
    background: {SURFACE};
    color: {TEXT};
    border: none;
    width: 100%;
    height: auto;
}}
"""


# ---------------------------------------------------------------------------
# Queue messages
# ---------------------------------------------------------------------------

@dataclass
class _TokenMsg:
    text: str

@dataclass
class _ToolStart:
    summary: str
    parallel: bool = False

@dataclass
class _ToolEnd:
    ok: bool
    detail: str

@dataclass
class _ToolOutput:
    text: str

@dataclass
class _Done:
    usage: dict | None = None
    turn_count: int = 0

@dataclass
class _Error:
    msg: str


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class AgentWorker(threading.Thread):
    """Runs the agent loop in a background thread, pushing messages to a queue."""

    def __init__(self, messages, config, write_gate, read_gate, out: Queue, session, approve_callback=None):
        super().__init__(daemon=True)
        self.messages = messages
        self.config = config
        self.write_gate = write_gate
        self.read_gate = read_gate
        self.out = out
        self.cancel = threading.Event()
        self.session = session
        self.approve_callback = approve_callback

    def run(self):
        config = self.config
        config.stream = True

        try:
            msg = run_agent_turn(
                self.messages, config,
                self.write_gate, self.read_gate,
                on_token=lambda t: self.out.put(_TokenMsg(t)),
                on_tool_start=lambda s, parallel=False: self.out.put(_ToolStart(s, parallel)),
                on_tool_end=lambda ok, d: self.out.put(_ToolEnd(ok, d)),
                on_tool_output=lambda line: self.out.put(_ToolOutput(line)),
                cancel_event=self.cancel,
                session=self.session,
                approve_callback=self.approve_callback,
            )
        except Exception as e:
            self.out.put(_Error(str(e)))
            self.out.put(_Done())
            return

        if msg is not None:
            self.out.put(_Done(
                usage=msg.get("_total_usage"),
                turn_count=msg.get("_turn_count", 0),
            ))
        # If msg is None, turn was cancelled — app's cancel handler cleans up


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class MiniAgentTUI(App):
    """Textual TUI for mini_agent."""

    CSS = CSS

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("enter", "submit", "Submit", priority=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="conversation", highlight=True, markup=True, wrap=True)
        with Container(id="input-area"):
            yield TextArea("", id="input")
        yield Footer()

    def on_mount(self) -> None:
        workspace = resolve_workspace()
        from config import init_session
        data = init_session(workspace)
        self.config = data["config"]
        self.config.verbose = "--quiet" not in sys.argv
        self.write_gate = data["write_gate"]
        self.read_gate = data["read_gate"]
        self.memory = data["memory"]
        self.messages = data["messages"]
        self.session = requests.Session()


        log = self.query_one("#conversation", RichLog)
        log.write(f"[bold {ACCENT}]mini_agent[/]  —  {self.config.model}")
        log.write(f"Workspace: {_safe(workspace)}")
        if saved := len(self.messages) - 2:  # minus 2 system messages
            log.write(f"Restored {saved} messages from previous session")
        log.write("—" * 50)

        self.query_one("#input", TextArea).focus()
        self._drain_event = threading.Event()
        self.queue: Queue = _NotifyQueue(self._drain_event)
        self.worker: AgentWorker | None = None
        self._buf = ""
        self._thinking_buf = ""
        self._thinking_flush_pos = 0
        self._in_thinking = False
        self._turn_finished = True
        self._history: list[str] = []
        self._history_pos: int = 0
        self.set_interval(0.05, self._drain)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_cancel(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.worker.cancel.set()
            self.worker = None
            self._turn_finished = True
            self._flush_buf()
            self._thinking_buf = ""
            self._thinking_flush_pos = 0
            self._in_thinking = False
            self.memory.save(self.messages)
            log = self.query_one("#conversation", RichLog)
            log.write(f"[{YELLOW}]  ╼ Cancelled.[/]")
            self.query_one("#input", TextArea).focus()

    def action_submit(self) -> None:
        """Submit: Enter key — send TextArea content to agent."""
        focused = self.focused
        if isinstance(focused, TextArea) and focused.id == "input":
            self._submit()

    def on_key(self, event) -> None:
        """Handle Shift+Enter (newline) and Up/Down (history) in TextArea."""
        focused = self.focused
        if not isinstance(focused, TextArea) or focused.id != "input":
            return

        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            focused.insert("\n")

        elif event.key == "up" and not focused.text:
            event.stop()
            event.prevent_default()
            if self._history and self._history_pos > 0:
                self._history_pos -= 1
                focused.text = self._history[self._history_pos]

        elif event.key == "down" and not focused.text:
            event.stop()
            event.prevent_default()
            if self._history_pos < len(self._history) - 1:
                self._history_pos += 1
                focused.text = self._history[self._history_pos]
            else:
                self._history_pos = len(self._history)
                focused.text = ""

    def _approve(self, tool_name: str, args: dict) -> bool:
        """Auto-approve in TUI. User sees tool calls and can cancel with Ctrl+C."""
        log = self.query_one("#conversation", RichLog)
        brief = str(args)
        if len(brief) > 80:
            brief = brief[:80] + "..."
        log.write(f"[{YELLOW} italic]  ⏳ approved {tool_name}({_safe(brief)})[/]")
        return True

    def _export_to_file(self, path: str) -> None:
        """Write current conversation to a markdown file."""
        from memory import export_conversation_markdown
        md = export_conversation_markdown(self.messages)
        with open(path, "w") as f:
            f.write(md)

    def _submit(self) -> None:
        """Send user message to the agent."""
        # Guard against double-submit while agent is working
        if self.worker is not None and self.worker.is_alive():
            return

        # Defensive: clear any stale table buffer from a previous turn
        if hasattr(self, "_table_buf"):
            self._table_buf = []

        input_widget = self.query_one("#input", TextArea)
        text = input_widget.text.strip()
        if not text:
            return
        input_widget.clear()

        # Special commands
        if text.startswith("/"):
            self._handle_command(text)
            return

        self.messages.append({"role": "user", "content": text})
        self._history.append(text)
        self._history_pos = len(self._history)
        log = self.query_one("#conversation", RichLog)
        log.write(f"\n[{GREEN}]▸ {_safe(text)}[/]")

        self._buf = ""
        self._thinking_buf = ""
        self._thinking_flush_pos = 0
        self._in_thinking = False
        self._turn_finished = False

        self.worker = AgentWorker(
            self.messages, self.config,
            self.write_gate, self.read_gate,
            self.queue,
            self.session,
            approve_callback=self._approve if self.config.approve_write_ops else None,
        )
        self.worker.start()

    def _handle_command(self, text: str) -> None:
        """Handle slash-commands typed in the input area."""
        cmd = text.lower().strip()
        log = self.query_one("#conversation", RichLog)

        if cmd == "/clear":
            self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            self.memory.clear()
            self._history = []
            self._history_pos = 0
            log.write("")
            log.write(f"[{DIM}]— conversation cleared —[/]")
            return

        if cmd == "/help":
            log.write("")
            log.write(f"[{DIM}]Commands:[/]")
            log.write(f"[{DIM}]  /clear   Reset conversation memory[/]")
            log.write(f"[{DIM}]  /export  Write conversation to a markdown file[/]")
            log.write(f"[{DIM}]  /help    Show this help[/]")
            return

        if cmd == "/export":
            import datetime
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"conversation_{ts}.md"
            path = os.path.join(self.config.workspace, fname)
            self._export_to_file(path)
            log.write(f"[{DIM}]Exported to {fname}[/]")
            self.query_one("#input", TextArea).focus()
            return

        log.write(f"[{YELLOW}]Unknown command: {text}[/]")

    # ------------------------------------------------------------------
    # Drain queue (called by timer every 50ms)
    # ------------------------------------------------------------------

    def _drain(self) -> None:
        """Pull messages off the queue and write to the conversation log.
        Uses drain_event to skip cycles when queue is empty."""
        # If no data was pushed since last drain and queue is empty, skip
        if not self._drain_event.is_set() and self.queue.empty():
            return
        self._drain_event.clear()
        log = self.query_one("#conversation", RichLog)
        try:
            while True:
                msg = self.queue.get_nowait()

                if isinstance(msg, _TokenMsg):
                    self._handle_token(msg, log)
                elif isinstance(msg, _ToolStart):
                    self._flush_buf()
                    self._in_thinking = False
                    if msg.parallel:
                        log.write(f"  [{YELLOW}]⫼ {_safe(msg.summary)}[/]")
                    else:
                        log.write(f"  [{YELLOW}]⚙ {_safe(msg.summary)}[/]")
                elif isinstance(msg, _ToolEnd):
                    symbol = "✓" if msg.ok else "✗"
                    color = GREEN if msg.ok else RED
                    log.write(f"    [{color}]{symbol}  {_safe(msg.detail)}[/]")
                elif isinstance(msg, _ToolOutput):
                    log.write(f"[{DIM}]{_safe(msg.text)}[/]")
                elif isinstance(msg, _Error):
                    log.write(f"[{RED}]Error: {_safe(msg.msg)}[/]")
                elif isinstance(msg, _Done):
                    self._finish_turn(usage=msg.usage, turn_count=msg.turn_count)
                    return

        except Empty:
            pass

        # Worker finished without sending Done (cancelled or crashed)
        if not self._turn_finished and (self.worker is None or not self.worker.is_alive()):
            self._finish_turn()

    def _handle_token(self, msg: _TokenMsg, log) -> None:
        """Process a single token: route to thinking or content buffer.

        Thinking is flushed on sentence boundaries (period-space, newlines)
        or every ~400 chars.  Content is flushed on newlines or every ~300
        chars at word boundaries.  Visual separators mark thinking blocks.
        """
        text = msg.text

        if text.startswith(THINKING_START):
            self._flush_buf()  # safety: flush any pending content first
            self._in_thinking = True
            self._thinking_buf = ""
            self._thinking_flush_pos = 0
            log.write(f"[{DIM}]▔▔▔ thinking ▔▔▔[/]")
            return

        if text == THINKING_END:
            self._in_thinking = False
            # Flush remaining thinking
            remaining = self._thinking_buf[self._thinking_flush_pos:].strip()
            if remaining:
                log.write(f"[{DIM} italic]┃  {_safe(remaining)}[/]")
            self._thinking_buf = ""
            self._thinking_flush_pos = 0
            return

        if self._in_thinking:
            self._thinking_buf += text
            # Flush on sentence boundaries or ~400-char chunks
            while True:
                buf = self._thinking_buf
                pos = self._thinking_flush_pos
                remaining = buf[pos:]

                # Find the best natural break: newline, then sentence end
                best = -1
                best_len = 0

                # Newline within 400 chars
                nl = remaining.find("\n")
                if nl != -1 and nl < 400:
                    best = nl + 1  # include the newline
                    best_len = nl
                else:
                    # Sentence ending within 400 chars: period/question/exclamation + space
                    for sep in (". ", "? ", "! ", ":\n"):
                        idx = remaining.find(sep)
                        if idx != -1 and idx < 400 and (best == -1 or idx < best_len):
                            best = idx + len(sep)
                            best_len = idx

                if best != -1:
                    chunk = remaining[:best].rstrip()
                    self._thinking_flush_pos = pos + best
                    if chunk:
                        log.write(f"[{DIM} italic]┃  {_safe(chunk)}[/]")
                    continue

                # No natural break — flush at ~400 chars on a space
                if len(remaining) >= 400:
                    cut = 400
                    space = remaining.rfind(" ", 250, 400)
                    if space != -1:
                        cut = space + 1
                    chunk = remaining[:cut].rstrip()
                    self._thinking_flush_pos = pos + cut
                    if chunk:
                        log.write(f"[{DIM} italic]┃  {_safe(chunk)}[/]")
                    continue

                break  # not enough to flush
            return

        # Content — accumulate, flush complete lines or ~300-char chunks
        self._buf += text
        # Buffer table rows to flush as monospace code block
        if not hasattr(self, "_table_buf"):
            self._table_buf: list[str] = []
        while True:
            nl = self._buf.find("\n")
            if nl != -1 and nl < 300:
                line = self._buf[:nl].rstrip()
                self._buf = self._buf[nl + 1:]
                if line:
                    stripped = line.strip()
                    is_table = (stripped.startswith("|") and stripped.endswith("|")
                                and "|" in stripped[1:-1])
                    is_sep = (stripped.startswith("|") and all(c in "| -:" for c in stripped))
                    if is_table or is_sep:
                        self._table_buf.append(line)
                    else:
                        # Flush any buffered table first
                        if self._table_buf:
                            log.write("[code]\n" + "\n".join(self._table_buf) + "\n[/code]")
                            self._table_buf = []
                        log.write(_safe(line))
                continue

            # No newline soon — flush at ~300 chars on a space boundary
            if len(self._buf) >= 300:
                cut = 300
                space = self._buf.rfind(" ", 200, 300)
                if space != -1:
                    cut = space + 1
                chunk = self._buf[:cut].rstrip()
                self._buf = self._buf[cut:]
                if chunk:
                    log.write(_safe(chunk))
                continue

            break

    def _finish_turn(self, usage: dict | None = None, turn_count: int = 0) -> None:
        """Commit buffers, save memory, and clean up after a turn."""
        # Flush any remaining table buffer before regular buf
        if hasattr(self, "_table_buf") and self._table_buf:
            log = self.query_one("#conversation", RichLog)
            log.write("[code]\n" + "\n".join(self._table_buf) + "\n[/code]")
            self._table_buf = []
        self._flush_buf()
        self._in_thinking = False
        self._thinking_buf = ""
        self._thinking_flush_pos = 0
        self._buf = ""
        self.memory.save(self.messages)
        self.worker = None
        self._turn_finished = True
        self.query_one("#input", TextArea).focus()
        n = sum(1 for m in self.messages if m["role"] != "system")
        parts = [f"{n} msgs"]
        if usage and usage.get("total_tokens"):
            parts.append(f"{usage['total_tokens']} tok")
        if turn_count > 1:
            parts.append(f"turn {turn_count}")
        parts.append(self.config.model)
        log = self.query_one("#conversation", RichLog)
        log.write(f"[{DIM}]— {' | '.join(parts)}[/]")

    def _flush_buf(self) -> None:
        if self._buf.strip():
            log = self.query_one("#conversation", RichLog)
            log.write(_safe(self._buf.rstrip()))
        self._buf = ""
        # Defensive: clear stale table buffer (shouldn't still have data,
        # but if it does, a partial flush could cause Rich markup errors)
        if hasattr(self, "_table_buf"):
            self._table_buf = []


if __name__ == "__main__":
    app = MiniAgentTUI()
    app.run()
