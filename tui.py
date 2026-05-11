#!/usr/bin/env python3
"""
tui.py — Textual TUI frontend for mini_agent.

Provides a dark terminal interface with separate agent/user zones,
streaming typewriter output, cancel support, and multi-line paste.

Usage:
    python tui.py [--workspace PATH] [--quiet]
"""

import os
import sys
import threading
from queue import Queue, Empty

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Header, Footer, RichLog, TextArea
from textual.reactive import var

from config import AgentConfig, CONFIG_FILENAME
from llm import call_deepseek
from prompt import SYSTEM_PROMPT
from safety import ReadSafetyGate, WriteSafetyGate
from memory import MemoryStore
from tools import execute_tool, tool_summary, set_context


# ---------------------------------------------------------------------------
# TUI message types (on worker→UI queue)
# ---------------------------------------------------------------------------

class _TokenMsg:
    """A content token from the streaming LLM response."""
    __slots__ = ("text",)
    def __init__(self, text: str):
        self.text = text


class _ToolStart:
    """A tool call is about to execute."""
    __slots__ = ("summary",)
    def __init__(self, summary: str):
        self.summary = summary


class _ToolEnd:
    """A tool call finished."""
    __slots__ = ("ok", "detail")
    def __init__(self, ok: bool, detail: str):
        self.ok = ok
        self.detail = detail


class _Thinking:
    """Reasoning content arrived."""
    __slots__ = ("text",)
    def __init__(self, text: str):
        self.text = text


class _Done:
    """Agent turn complete."""
    pass


# Sentinel for stopping the worker
_STOP = object()


# ---------------------------------------------------------------------------
# Colour constants (dark theme)
# ---------------------------------------------------------------------------

BG       = "#16162a"
SURFACE  = "#1e1e3a"
BORDER   = "#3a3a5a"
ACCENT   = "#6c6cf0"
TEXT     = "#c8c8e0"
DIM      = "#6a6a8a"
GREEN    = "#5cdb5c"
YELLOW   = "#e0e060"
RED      = "#e05050"

CSS = f"""
Screen {{
    background: {BG};
}}

#header {{
    background: {SURFACE};
    color: {ACCENT};
    text-style: bold;
}}

#footer {{
    background: {SURFACE};
    color: {DIM};
}}

#conversation {{
    background: {BG};
    border: none;
    padding: 1 2;
    scrollbar-background: {BG};
    scrollbar-color: {BORDER};
}}

#input-area {{
    background: {SURFACE};
    border: solid {BORDER};
    height: auto;
    min-height: 3;
    max-height: 12;
}}

#input-area:focus-within {{
    border: solid {ACCENT};
}}
"""


# ---------------------------------------------------------------------------
# Worker: runs the agent loop in a background thread
# ---------------------------------------------------------------------------

class AgentWorker(threading.Thread):
    """Runs the agent's LLM+tool loop, pushing display updates to the UI queue."""

    def __init__(self, messages: list[dict], config: AgentConfig,
                 write_gate: WriteSafetyGate, read_gate: ReadSafetyGate,
                 out: Queue):
        super().__init__(daemon=True)
        self.messages = messages
        self.config = config
        self.write_gate = write_gate
        self.read_gate = read_gate
        self.out = out
        self.cancel = threading.Event()

    def run(self) -> None:
        messages = self.messages
        config = self.config

        # Force streaming so we get tokens
        config.stream = True

        while True:
            if self.cancel.is_set():
                return

            try:
                msg = call_deepseek(messages, config,
                                    on_token=lambda t: self.out.put(_TokenMsg(t)))
            except Exception as e:
                self.out.put(_TokenMsg(f"\n[Error: {e}]\n"))
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

    worker: AgentWorker | None = var(None, init=False)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="conversation", highlight=True, markup=True, wrap=True)
        with Container(id="input-area"):
            yield TextArea("", id="input", language=None)
        yield Footer()

    def on_mount(self) -> None:
        """Start the agent session — restore memory, show banner."""
        # Resolve workspace (same logic as mini_agent.py)
        workspace = os.getcwd()
        for i, arg in enumerate(sys.argv[1:]):
            if arg == "--workspace" and i + 1 < len(sys.argv):
                workspace = sys.argv[i + 2]
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

        # Banner
        log.write(f"[bold {ACCENT}]mini_agent[/] — {self.config.model}")
        log.write(f"Workspace: {workspace}")
        if saved:
            log.write(f"Restored {len(saved)} messages from previous session")
        log.write(f"Type [bold]Ctrl+Q[/] to quit, [bold]Ctrl+C[/] to cancel, [bold]Shift+Enter[/] for newline")
        log.write("—" * 50)

        self.query_one("#input", TextArea).focus()

        # Queue for worker→UI communication
        self.queue: Queue = Queue()

    def action_cancel(self) -> None:
        """Cancel the currently running agent turn."""
        if self.worker is not None and self.worker.is_alive():
            self.worker.cancel.set()
            log = self.query_one("#conversation", RichLog)
            log.write(f"[{YELLOW}]╼ Cancelled.[/]")
            self.worker = None

    def on_text_area_changed(self, event) -> None:
        """No-op, handled via key intercept."""
        pass

    def on_key(self, event) -> None:
        """Intercept Enter in the TextArea to submit."""
        focused = self.focused
        if not isinstance(focused, TextArea) or focused.id != "input":
            return

        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self._submit()
        elif event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            focused.insert("\n")

    def _submit(self) -> None:
        """Send user message to the agent."""
        input_widget = self.query_one("#input", TextArea)
        text = input_widget.text.strip()
        if not text:
            return
        input_widget.clear()

        log = self.query_one("#conversation", RichLog)
        # Echo user message
        log.write(f"\n[{GREEN}]▸ {text}[/]\n")

        # Append to conversation
        self.messages.append({"role": "user", "content": text})

        # Start worker
        self.queue = Queue()
        self.worker = AgentWorker(
            self.messages, self.config,
            self.write_gate, self.read_gate,
            self.queue,
        )
        self.worker.start()
        self.set_interval(0.05, self._poll_worker)

    def _poll_worker(self) -> None:
        """Drain the worker queue and update the UI."""
        log = self.query_one("#conversation", RichLog)
        first_token = True

        try:
            while True:
                msg = self.queue.get_nowait()

                if isinstance(msg, _TokenMsg):
                    if first_token:
                        log.write(f"[{ACCENT}]", scroll_end=False)
                        first_token = False
                    log.write(f"{msg.text}", scroll_end=False)

                elif isinstance(msg, _Thinking):
                    log.write(f"[{DIM} italic]{msg.text}[/]", scroll_end=False)

                elif isinstance(msg, _ToolStart):
                    if not first_token:
                        log.write("\n", scroll_end=False)
                        first_token = True
                    log.write(f"  [{YELLOW}]⚙ {msg.summary}[/]", scroll_end=False)

                elif isinstance(msg, _ToolEnd):
                    symbol = "✓" if msg.ok else "✗"
                    color = GREEN if msg.ok else RED
                    log.write(f" [{color}]{symbol}[/]", scroll_end=False)

                elif isinstance(msg, _Done):
                    if not first_token:
                        log.write(f"[/{ACCENT}]", scroll_end=False)
                    log.write("")
                    self.memory.save(self.messages)
                    self.worker = None
                    return

        except Empty:
            pass

        if self.worker is None or not self.worker.is_alive():
            self.memory.save(self.messages)
            self.worker = None


if __name__ == "__main__":
    app = MiniAgentTUI()
    app.run()
