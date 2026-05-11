#!/usr/bin/env python3
"""
tui.py — Textual TUI frontend for mini_agent.

Usage: python tui.py [--workspace PATH] [--quiet]
"""

import os
import sys
import threading
from queue import Queue, Empty
from dataclasses import dataclass

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Header, Footer, RichLog, TextArea, Static
from textual.binding import Binding

from config import AgentConfig
from llm import run_agent_turn, THINKING_START, THINKING_END
from prompt import SYSTEM_PROMPT
from safety import ReadSafetyGate, WriteSafetyGate
from memory import MemoryStore
from tools import set_context


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

BG      = "#1a1a1a"
SURFACE = "#262626"
BORDER  = "#3a3a3a"
ACCENT  = "#aaaaaa"
TEXT    = "#c8c8c8"
DIM     = "#6a6a6a"
GREEN   = "#5cdb5c"
YELLOW  = "#e0c860"
RED     = "#e05050"

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

#stream {{
    background: {BG};
    color: {TEXT};
    padding: 0 1;
    height: auto;
    min-height: 0;
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

@dataclass
class _ToolEnd:
    ok: bool
    detail: str

@dataclass
class _Done:
    pass

@dataclass
class _Error:
    msg: str


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class AgentWorker(threading.Thread):
    """Runs the agent loop in a background thread, pushing messages to a queue."""

    def __init__(self, messages, config, write_gate, read_gate, out: Queue):
        super().__init__(daemon=True)
        self.messages = messages
        self.config = config
        self.write_gate = write_gate
        self.read_gate = read_gate
        self.out = out
        self.cancel = threading.Event()

    def run(self):
        config = self.config
        config.stream = True

        try:
            msg = run_agent_turn(
                self.messages, config,
                self.write_gate, self.read_gate,
                on_token=lambda t: self.out.put(_TokenMsg(t)),
                on_tool_start=lambda s: self.out.put(_ToolStart(s)),
                on_tool_end=lambda ok, d: self.out.put(_ToolEnd(ok, d)),
                cancel_event=self.cancel,
            )
        except Exception as e:
            self.out.put(_Error(str(e)))
            self.out.put(_Done())
            return

        if msg is not None:
            self.out.put(_Done())
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
        yield Static("", id="stream")
        with Container(id="input-area"):
            yield TextArea("", id="input")
        yield Footer()

    def on_mount(self) -> None:
        workspace = os.getcwd()
        args = sys.argv[1:]
        for i, arg in enumerate(args):
            if arg == "--workspace" and i + 1 < len(args):
                workspace = args[i + 1]
                break
        workspace = os.environ.get("AGENT_WORKSPACE", workspace)

        self.config = AgentConfig.load(workspace)
        self.config.verbose = "--quiet" not in sys.argv
        self.write_gate = WriteSafetyGate(workspace, allow_overwrites=self.config.allow_overwrites)
        self.read_gate = ReadSafetyGate(workspace)
        memory_path = os.path.join(workspace, self.config.memory_filename)
        self.memory = MemoryStore(memory_path, max_messages=self.config.max_messages)
        set_context(exa_api_key=self.config.exa_api_key)

        saved = self.memory.load()
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if saved:
            self.messages.extend(saved)

        log = self.query_one("#conversation", RichLog)
        log.write(f"[bold {ACCENT}]mini_agent[/]  —  {self.config.model}")
        log.write(f"Workspace: {workspace}")
        if saved:
            log.write(f"Restored {len(saved)} messages from previous session")
        log.write("—" * 50)

        self.query_one("#input", TextArea).focus()
        self.queue: Queue = Queue()
        self.worker: AgentWorker | None = None
        self._buf = ""
        self._thinking_buf = ""
        self._in_thinking = False
        self._poll_timer = self.set_interval(0.05, self._drain)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_cancel(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.worker.cancel.set()
            self._flush_buf()
            log = self.query_one("#conversation", RichLog)
            log.write(f"[{YELLOW}]  ╼ Cancelled.[/]")
            self.query_one("#input", TextArea).focus()

    def action_submit(self) -> None:
        """Submit: Enter key — send TextArea content to agent."""
        focused = self.focused
        if isinstance(focused, TextArea) and focused.id == "input":
            self._submit()

    def on_key(self, event) -> None:
        """Handle Shift+Enter to insert newline in TextArea."""
        focused = self.focused
        if isinstance(focused, TextArea) and focused.id == "input":
            if event.key == "shift+enter":
                event.stop()
                event.prevent_default()
                focused.insert("\n")

    def _submit(self) -> None:
        """Send user message to the agent."""
        # Guard against double-submit while agent is working
        if self.worker is not None and self.worker.is_alive():
            return

        input_widget = self.query_one("#input", TextArea)
        text = input_widget.text.strip()
        if not text:
            return
        input_widget.clear()

        self.messages.append({"role": "user", "content": text})
        log = self.query_one("#conversation", RichLog)
        log.write(f"\n[{GREEN}]▸ {text}[/]")

        self._buf = ""
        self._thinking_buf = ""
        self._in_thinking = False
        self.query_one("#stream", Static).update("")

        self.worker = AgentWorker(
            self.messages, self.config,
            self.write_gate, self.read_gate,
            self.queue,
        )
        self.worker.start()

    # ------------------------------------------------------------------
    # Drain queue (called by timer every 50ms)
    # ------------------------------------------------------------------

    def _drain(self) -> None:
        """Pull messages off the queue and write to the conversation log."""
        log = self.query_one("#conversation", RichLog)
        stream = self.query_one("#stream", Static)

        try:
            while True:
                msg = self.queue.get_nowait()

                if isinstance(msg, _TokenMsg):
                    self._handle_token(msg, log, stream)
                elif isinstance(msg, _ToolStart):
                    self._flush_buf()
                    self._in_thinking = False
                    stream.update("")
                    log.write(f"  [{YELLOW}]⚙ {msg.summary}[/]")
                elif isinstance(msg, _ToolEnd):
                    symbol = "✓" if msg.ok else "✗"
                    color = GREEN if msg.ok else RED
                    log.write(f"    [{color}]{symbol}  {msg.detail}[/]")
                elif isinstance(msg, _Error):
                    log.write(f"[{RED}]Error: {msg.msg}[/]")
                elif isinstance(msg, _Done):
                    self._finish_turn()
                    return

        except Empty:
            pass

        # Worker finished without sending Done (cancelled or crashed)
        if self.worker is None or not self.worker.is_alive():
            self._finish_turn()

    def _handle_token(self, msg: _TokenMsg, log, stream) -> None:
        """Process a single token: route to thinking or content buffer."""
        text = msg.text

        if text.startswith(THINKING_START):
            self._in_thinking = True
            self._thinking_buf = ""
            log.write(f"[{DIM} italic]  thinking…[/]")
            return

        if text == THINKING_END:
            if self._thinking_buf.strip():
                log.write(f"[{DIM} italic]  {self._thinking_buf.rstrip()}[/]")
            self._in_thinking = False
            self._thinking_buf = ""
            return

        if self._in_thinking:
            self._thinking_buf += text
            return

        # Content — accumulate, flush complete lines to log, show partial in stream
        self._buf += text
        while "\n" in self._buf:
            idx = self._buf.index("\n")
            line = self._buf[:idx].rstrip()
            self._buf = self._buf[idx + 1:]
            if line:
                log.write(line)
        stream.update(self._buf)

    def _finish_turn(self) -> None:
        """Commit buffers, save memory, and clean up after a turn."""
        self._flush_buf()
        self._in_thinking = False
        if self._thinking_buf.strip():
            log = self.query_one("#conversation", RichLog)
            log.write(f"[{DIM} italic]  {self._thinking_buf.rstrip()}[/]")
        self._thinking_buf = ""
        self._buf = ""
        self.query_one("#stream", Static).update("")
        self.memory.save(self.messages)
        self.worker = None
        self.query_one("#input", TextArea).focus()

    def _flush_buf(self) -> None:
        if self._buf.strip():
            log = self.query_one("#conversation", RichLog)
            log.write(self._buf.rstrip())
        self._buf = ""
        self.query_one("#stream", Static).update("")


if __name__ == "__main__":
    app = MiniAgentTUI()
    app.run()
