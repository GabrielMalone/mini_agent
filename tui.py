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
from textual.widgets import Header, Footer, RichLog, Input

from config import AgentConfig
from llm import call_deepseek
from prompt import SYSTEM_PROMPT
from safety import ReadSafetyGate, WriteSafetyGate
from memory import MemoryStore
from tools import execute_tool, tool_summary, set_context


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
        messages = self.messages
        config = self.config
        config.stream = True

        while True:
            if self.cancel.is_set():
                return

            try:
                msg = call_deepseek(
                    messages, config,
                    on_token=lambda t: self.out.put(_TokenMsg(t)),
                )
            except Exception as e:
                self.out.put(_Error(str(e)))
                self.out.put(_Done())
                return

            if self.cancel.is_set():
                return

            if not msg.get("tool_calls"):
                self.out.put(_Done())
                messages.append(msg)
                return

            messages.append(msg)
            for tc in msg["tool_calls"]:
                if self.cancel.is_set():
                    return
                self.out.put(_ToolStart(tool_summary(tc)))
                result = execute_tool(tc, self.write_gate, self.read_gate)
                detail = result.content[:300]
                if len(result.content) > 300:
                    detail += "…"
                self.out.put(_ToolEnd(result.success, detail))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result.to_json(),
                })


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class MiniAgentTUI(App):
    """Textual TUI for mini_agent."""

    CSS = CSS

    BINDINGS = [
        ("ctrl+c", "cancel", "Cancel"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="conversation", highlight=True, markup=True, wrap=True)
        with Container(id="input-area"):
            yield Input(
                placeholder="Type a message… (Enter to send, paste for multi-line)",
                id="input",
            )
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

        self.query_one("#input", Input).focus()
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
            self.query_one("#input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "input":
            return
        text = event.value.strip()
        if not text:
            return

        event.input.value = ""

        self.messages.append({"role": "user", "content": text})
        log = self.query_one("#conversation", RichLog)
        log.write(f"\n[{GREEN}]▸ {text}[/]")

        self._buf = ""
        self._thinking_buf = ""
        self._in_thinking = False

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

        try:
            while True:
                msg = self.queue.get_nowait()

                if isinstance(msg, _TokenMsg):
                    text = msg.text
                    # Handle thinking delimiters
                    if text.startswith("\n[thinking] "):
                        self._in_thinking = True
                        self._thinking_buf = ""
                        log.write(f"[{DIM} italic]  thinking…[/]")
                        continue
                    if text == "\n[/thinking]":
                        if self._thinking_buf.strip():
                            log.write(f"[{DIM} italic]  {self._thinking_buf.rstrip()}[/]")
                        self._in_thinking = False
                        self._thinking_buf = ""
                        continue
                    if self._in_thinking:
                        self._thinking_buf += text
                        continue
                    # Content
                    self._buf += text
                    while "\n" in self._buf:
                        idx = self._buf.index("\n")
                        line = self._buf[:idx].rstrip()
                        self._buf = self._buf[idx + 1:]
                        if line:
                            log.write(line)

                elif isinstance(msg, _ToolStart):
                    self._flush_buf()
                    self._in_thinking = False
                    log.write(f"  [{YELLOW}]⚙ {msg.summary}[/]")

                elif isinstance(msg, _ToolEnd):
                    symbol = "✓" if msg.ok else "✗"
                    color = GREEN if msg.ok else RED
                    log.write(f"    [{color}]{symbol}  {msg.detail}[/]")

                elif isinstance(msg, _Error):
                    log.write(f"[{RED}]Error: {msg.msg}[/]")

                elif isinstance(msg, _Done):
                    self._flush_buf()
                    self._in_thinking = False
                    if self._thinking_buf.strip():
                        log.write(f"[{DIM} italic]  {self._thinking_buf.rstrip()}[/]")
                    self._thinking_buf = ""
                    self.memory.save(self.messages)
                    self.worker = None
                    return

        except Empty:
            pass

        if self.worker is None or not self.worker.is_alive():
            self._flush_buf()
            if self._thinking_buf.strip():
                log.write(f"[{DIM} italic]  {self._thinking_buf.rstrip()}[/]")
            self._thinking_buf = ""
            self._buf = ""
            self.memory.save(self.messages)
            self.worker = None
            self.query_one("#input", Input).focus()

    def _flush_buf(self) -> None:
        if self._buf.strip():
            log = self.query_one("#conversation", RichLog)
            log.write(self._buf.rstrip())
        self._buf = ""


if __name__ == "__main__":
    app = MiniAgentTUI()
    app.run()
