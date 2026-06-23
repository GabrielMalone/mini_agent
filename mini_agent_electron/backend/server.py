#!/usr/bin/env python3
"""
server.py -- JSON-lines backend server for the mini_agent Electron app.

Communicates with the Electron main process via stdin/stdout using
JSON-lines protocol. Each line is a complete JSON object.

Protocol (Electron -> Python):
  {"type": "submit",    "text": "user message"}
  {"type": "command",   "command": "/clear | /help | /cancel | /stats | ..."}
  {"type": "cancel"}
  {"type": "get_status"}
  {"type": "shutdown"}

Protocol (Python -> Electron):
  {"type": "ready",     "model": "...", "workspace": "...", ...}
  {"type": "token",     "text": "..."}
  {"type": "thinking_start"}
  {"type": "thinking_end"}
  {"type": "tool_start","summary": "...", "parallel": bool}
  {"type": "tool_end",  "ok": bool, "detail": "..."}
  {"type": "tool_output","line": "..."}
  {"type": "turn_complete","usage": {...}, "turn_count": N}
  {"type": "error",     "message": "..."}
  {"type": "status",    "model": "...", "git_branch": "...", ...}
"""

from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import threading
import time

# Unix-only modules (not available on Windows).  Guarded here so the import
# doesn't crash the backend at module-load time.  They are used only inside
# _run_shell_pty() which has its own Windows fallback.
if sys.platform != "win32":
    import fcntl
    import pty
    import select
    import termios

# ---------------------------------------------------------------------------
# Windows: force UTF-8 for all I/O.  Without this, Python defaults to the
# system codepage (cp1252) and Unicode characters like -> (U+2192) or [MOON] (U+263E)
# raise 'charmap' codec can't encode errors when written to stdout/stderr.
# PYTHONUTF8=1 is the simplest fix (Python 3.7+) and also makes subprocess
# calls inherit UTF-8 encoding when text=True is used.
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # Reconfigure already-opened stdio streams to use UTF-8 + errors='replace'
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

# Ensure the parent mini_agent package is importable.
# main.js spawns us with cwd = mini_agent root, so cwd is the right path.
_cwd = os.getcwd()
if _cwd not in sys.path:
    sys.path.insert(0, _cwd)
# Also try relative to this file (belt-and-suspenders)
_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from core.config import (
    resolve_workspace,
    init_session,
    parse_args,
    _is_remote_workspace,
    _try_with_timeout,
    _switch_to_provider,
)
from core.llm import run_agent_turn
from stream import THINKING_START, THINKING_END
from core.safety import ReadSafetyGate, WriteSafetyGate
from core.prompt import build_system_prompt, build_startup_context, build_session_header
from core.balance import fetch_balance
from core.cost_tracking import SessionCost, format_cost_cny
from api import clear_api_cache
from tools import _TOOL_CONTEXT


# ---------------------------------------------------------------------------
# JSON-lines transport
# ---------------------------------------------------------------------------

_stdout_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Heartbeat -- prevents the Electron watchdog from killing the backend during
# long-running blocking operations (e.g. 5-min run_shell, slow API calls).
# A daemon thread writes {"type":"heartbeat"} to stdout every 30 seconds.
# If ALL threads are deadlocked (including this one), heartbeats stop and
# the watchdog correctly fires.  If the backend is just busy, heartbeats
# keep coming and the watchdog stays quiet.
# ---------------------------------------------------------------------------
_HEARTBEAT_INTERVAL = 30  # seconds


def _start_heartbeat(stop_event: threading.Event) -> threading.Thread:
    """Start a daemon thread that sends heartbeat messages to stdout.

    Args:
        stop_event: Set this event to stop the heartbeat thread cleanly.

    Returns:
        The started thread (daemon=True, so it won't block process exit).
    """

    def _loop() -> None:
        while not stop_event.wait(_HEARTBEAT_INTERVAL):
            try:
                send_msg({"type": "heartbeat"})
            except Exception:
                # If stdout is broken, we can't do anything useful.
                # The watchdog will detect this and restart the backend.
                break

    t = threading.Thread(target=_loop, daemon=True, name="heartbeat")
    t.start()
    return t


def send_msg(msg: dict) -> None:
    """Write a JSON message to stdout followed by newline, then flush.

    Thread-safe: multiple sub-agent threads may call this concurrently
    via the subagent_callback.  The lock prevents interleaved writes.
    """
    line = json.dumps(msg, ensure_ascii=False, default=str)
    with _stdout_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def read_msg() -> dict | None:
    """Read one JSON message from stdin. Returns None on EOF/error."""
    line = None
    try:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            return None
        return json.loads(line)
    except (json.JSONDecodeError, EOFError, IOError) as e:
        # Log parse errors to stderr instead of flooding the UI.
        # Most common cause: concurrent stdin writes from Electron's
        # flushPending() and an IPC handler producing an interleaved line.
        # Show the raw line (truncated) so we can diagnose.
        import sys as _sys

        raw_preview = repr(line)[:120] if line is not None else "<no line>"
        print(
            f"[server] Ignoring stdin parse error ({raw_preview}): {e}",
            file=_sys.stderr,
            flush=True,
        )
        return None


# ---------------------------------------------------------------------------
# PTY shell runner -- gives subprocesses a real terminal so they produce
# colours (ANSI escape codes) and columnar output (e.g. `ls` without -1).
# ---------------------------------------------------------------------------


def _run_shell_pty(cmd: str, cwd: str, timeout: float = 30.0) -> tuple[str, int]:
    """Run *cmd* with a pseudo-terminal and return (stdout_text, exit_code).

    When stdout is a pipe, many CLI tools (``ls``, ``grep``, ``git diff``)
    suppress colour and default to one-entry-per-line.  A PTY makes them
    behave as if attached to a real terminal.

    On Windows (where PTYs are not available via the ``pty`` module) this
    falls back to ``subprocess.run`` with ``CLICOLOR_FORCE`` set.
    """
    # -- Windows fallback: no pty.openpty() available --------------------
    if sys.platform == "win32":
        env = os.environ.copy()
        env.setdefault("CLICOLOR_FORCE", "1")
        env.setdefault("FORCE_COLOR", "1")
        env["PAGER"] = "cat"
        env["GIT_PAGER"] = "cat"
        r = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        text = r.stdout
        if r.stderr:
            if text:
                text += "\n"
            text += r.stderr
        return text, r.returncode

    master_fd, slave_fd = pty.openpty()
    try:
        # Force colour even for tools that check isatty() after fork
        env = os.environ.copy()
        env.setdefault("CLICOLOR_FORCE", "1")
        env.setdefault("FORCE_COLOR", "1")
        # Disable pagers so tools like `git diff` don't hang waiting for
        # input on a PTY whose stdin is /dev/null.
        env["PAGER"] = "cat"
        env["GIT_PAGER"] = "cat"

        # Set terminal window size so tools like `ls` format in columns.
        # Without this, the PTY defaults to 0 rows/cols and `ls` falls back
        # to one-entry-per-line (vertical) output.
        _PTY_COLS = 120
        _PTY_ROWS = 40
        env["COLUMNS"] = str(_PTY_COLS)
        env["LINES"] = str(_PTY_ROWS)
        try:
            winsz = struct.pack("HHHH", _PTY_ROWS, _PTY_COLS, 0, 0)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsz)
        except OSError:
            pass  # best-effort; not all platforms support TIOCSWINSZ

        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=cwd,
            stdout=slave_fd,
            stderr=slave_fd,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            env=env,
            preexec_fn=os.setsid if sys.platform != "win32" else None,
        )
        os.close(slave_fd)  # parent doesn't need the slave end

        output_chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(cmd, timeout)

            r, _, _ = select.select([master_fd], [], [], min(remaining, 1.0))
            if r:
                try:
                    data = os.read(master_fd, 4096)
                    if not data:
                        break
                    output_chunks.append(data)
                except OSError:
                    break

            if proc.poll() is not None:
                # Drain any last bytes buffered in the PTY
                break

        # Non-blocking drain of any remaining output
        os.set_blocking(master_fd, False)
        try:
            while True:
                data = os.read(master_fd, 4096)
                if not data:
                    break
                output_chunks.append(data)
        except (OSError, BlockingIOError):
            pass

        os.close(master_fd)
        proc.wait()

        text = b"".join(output_chunks).decode("utf-8", errors="replace")
        return text, proc.returncode
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Callbacks for run_agent_turn
# ---------------------------------------------------------------------------


class StreamCallbacks:
    """Callbacks that stream agent output to Electron via JSON messages."""

    def __init__(self):
        self._in_thinking = False

    def on_token(self, text: str) -> None:
        if text == THINKING_START:
            self._in_thinking = True
            send_msg({"type": "thinking_start"})
            return
        if text == THINKING_END:
            self._in_thinking = False
            send_msg({"type": "thinking_end"})
            return
        send_msg({"type": "token", "text": text})

    def on_tool_start(
        self, summary: str, parallel: bool = False, tool_name: str = ""
    ) -> None:
        msg: dict = {"type": "tool_start", "summary": summary, "parallel": parallel}
        if tool_name:
            msg["tool_name"] = tool_name
        send_msg(msg)

    def on_tool_end(
        self,
        ok: bool,
        detail: str,
        turn_id: int = 0,
        diff_preview=None,
        content: str = "",
        tool_name: str = "",
    ) -> None:
        msg: dict = {"type": "tool_end", "ok": ok, "detail": detail, "content": content}
        if tool_name:
            msg["tool_name"] = tool_name
        if diff_preview:
            msg["diff_preview"] = diff_preview
        send_msg(msg)

    def on_tool_output(self, line: str, tool_name: str = "", turn_id: int = 0) -> None:
        send_msg({"type": "tool_output", "line": line, "tool_name": tool_name})

    # -- sub-agent events (wired to _TOOL_CONTEXT._subagent_callback) --

    def on_subagent_start(
        self, task_id: str, parent_id: str, name: str, desc: str
    ) -> None:
        send_msg(
            {
                "type": "subagent_start",
                "task_id": task_id,
                "parent_id": parent_id,
                "name": name,
                "desc": desc,
            }
        )

    def on_subagent_output(self, task_id: str, line: str) -> None:
        send_msg({"type": "subagent_output", "task_id": task_id, "line": line})

    def on_subagent_end(self, task_id: str, ok: bool, content: str) -> None:
        send_msg(
            {
                "type": "subagent_end",
                "task_id": task_id,
                "ok": ok,
                "content": content[:500],
            }
        )

    def on_subagent_tool_start(
        self, task_id: str, tool_name: str, tool_args: str
    ) -> None:
        send_msg(
            {
                "type": "subagent_tool_start",
                "task_id": task_id,
                "tool_name": tool_name,
                "tool_args": tool_args,
            }
        )

    def on_subagent_tool_end(
        self, task_id: str, tool_name: str, ok: bool, content: str
    ) -> None:
        send_msg(
            {
                "type": "subagent_tool_end",
                "task_id": task_id,
                "tool_name": tool_name,
                "ok": ok,
                "content": content[:500],
            }
        )

    def on_subagent_thought(self, task_id: str, text: str) -> None:
        send_msg({"type": "subagent_thought", "task_id": task_id, "text": text})


# ---------------------------------------------------------------------------
# Agent runner -- runs in a background thread so the main thread can accept
# cancel messages and new input while a turn is in progress.
# ---------------------------------------------------------------------------


class AgentRunner:
    def __init__(self):
        # Bootstrap the agent session
        workspace = os.environ.get("MINI_AGENT_WORKSPACE") or resolve_workspace()
        os.environ["MINI_AGENT_UI"] = "electron"  # injected into system prompt header

        # If the workspace is on a remote filesystem, skip expensive
        # operations (symbol index, LSP) inside init_session so the
        # backend doesn't hang at startup.
        if _is_remote_workspace(workspace):
            print(
                f"[server] Remote workspace detected: {workspace} -- using local DB and skipping index scan",
                file=sys.stderr,
                flush=True,
            )

        cli = parse_args()
        data = init_session(workspace, cli_args=cli, progress_callback=None)
        self.config = data["config"]
        self.config.stream = True
        self.write_gate: WriteSafetyGate = data["write_gate"]
        self.read_gate: ReadSafetyGate = data["read_gate"]
        self.memory = data["memory"]
        self.messages: list[dict] = data["messages"]
        self.session = data["session"]
        self.workspace = workspace

        # Track shell CWD so /sh cd <dir> changes persist across commands
        self._shell_cwd: str = workspace

        self._cancel_event = threading.Event()
        self._turn_thread: threading.Thread | None = None
        self._total_turns = 0
        self._total_tokens = 0
        self._input_queue: list[str] = []
        self._input_lock = threading.Lock()
        self._callbacks = StreamCallbacks()

        # Sub-agent auto-report tracking:
        #   _pending_subagents  - set of task_ids spawned during the current turn.
        #   _auto_report_flag   - prevents double-queuing a synthesis prompt.
        # Reset at the start of each turn in _run_turn.
        self._pending_subagents: set[str] = set()
        self._auto_report_flag: bool = False

        # Running sub-agent count for status bar display.
        self._running_subagent_count: int = 0
        self._total_subagents: int = 0

        # Session cost tracking (Reasonix-style per-turn + cumulative).
        self._session_cost = SessionCost()

        # Balance — fetched once at startup, refreshed on demand.
        self._balance: dict | None = None
        self._fetch_balance_async()

        # Git status
        self._git_branch = ""
        self._git_dirty = False
        self._refresh_git_status()

    # -- status ---------------------------------------------------------

    def send_status(self) -> None:
        """Send current status to Electron (includes balance, cost, subagent count)."""
        # Derive session name from memory db path
        session_name = "default"
        db_path = getattr(self.memory, "_db_path", "")
        if db_path:
            import re

            m = re.search(r"_session_(.+)\\.db$", db_path)
            if m:
                session_name = m.group(1)
        status = {
            "type": "status",
            "model": self.config.model,
            "provider": getattr(self.config, "api_provider", "deepseek"),
            "workspace": self.workspace,
            "session_name": session_name,
            "git_branch": self._git_branch,
            "git_dirty": self._git_dirty,
        }
        status["restored_count"] = max(0, len(self.messages) - 2)

        # -- Reasonix-style status bar fields --
        # Balance
        if self._balance:
            status["balance"] = self._balance

        # Cost
        sc = self._session_cost
        status["session_cost"] = format_cost_cny(sc.total_cost)
        status["session_turns"] = sc.turn_count
        status["session_tokens"] = sc.total_prompt_tokens + sc.total_completion_tokens
        status["cache_hit_rate"] = (
            round(sc.cache_hit_rate * 100) if sc.cache_hit_rate is not None else None
        )
        if sc.last_turn:
            status["turn_cost"] = format_cost_cny(sc.last_turn.total_cost)
            status["turn_tokens"] = (
                sc.last_turn.prompt_tokens + sc.last_turn.completion_tokens
            )
            last_rate = sc.last_cache_hit_rate
            status["turn_cache_hit_rate"] = (
                round(last_rate * 100) if last_rate is not None else None
            )

        # Subagent count
        status["subagent_running"] = self._running_subagent_count
        status["subagent_total"] = self._total_subagents

        # Plan state — always send so stale plans don't linger in the UI
        from tools import _TOOL_CONTEXT

        plan_steps = getattr(_TOOL_CONTEXT, "_plan_steps", [])
        plan_done = getattr(_TOOL_CONTEXT, "_plan_done", set())
        status["plan_steps"] = list(plan_steps) if plan_steps else []
        status["plan_done"] = sorted(plan_done) if plan_done else []

        send_msg(status)

    def _fetch_balance_async(self) -> None:
        """Fetch DeepSeek wallet balance in a background thread.

        Does not block startup — the balance appears when the HTTP
        call completes.  Results are stored in self._balance for
        inclusion in the next status message.
        """

        def _fetch() -> None:
            try:
                b = fetch_balance(
                    self.config.api_url,
                    self.config.api_key,
                    timeout=10,
                )
            except Exception:
                b = None

            if b is not None:
                self._balance = {
                    "available": b.available,
                    "display": b.display,
                    "currency": b.infos[0].currency if b.infos else "CNY",
                }
            else:
                self._balance = None
            # Push updated status so the UI gets the balance
            self.send_status()

        t = threading.Thread(target=_fetch, daemon=True, name="balance-fetch")
        t.start()

    def _refresh_git_status(self) -> None:
        try:
            r = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.config.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            self._git_branch = r.stdout.strip()
            r2 = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.config.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            self._git_dirty = bool(r2.stdout.strip())
        except Exception:
            self._git_branch = ""
            self._git_dirty = False

    # -- turn execution -------------------------------------------------

    def submit(self, text: str) -> None:
        """Queue user input and start a turn if not already running.

        Slash commands (e.g. /clear, /stats) are intercepted and dispatched
        to the command handler rather than being sent to the LLM.  This is a
        backend defence in case the frontend hasn't been rebuilt with the
        corresponding slash-command routing.
        """
        if text.strip().startswith("/"):
            self.handle_command(text.strip())
            return

        with self._input_lock:
            self._input_queue.append(text)

        if self._turn_thread is None or not self._turn_thread.is_alive():
            self._start_turn()

    def _start_turn(self) -> None:
        """Start the sequential turn-processing loop in a background thread.

        The thread loops until the input queue is drained, running one
        turn at a time.  This avoids the race condition where a second
        turn's thread could call run_agent_turn concurrently with the
        first, corrupting self.messages.
        """
        self._turn_thread = threading.Thread(target=self._turn_loop, daemon=True)
        self._turn_thread.start()

    def _turn_loop(self) -> None:
        """Sequential turn-processing loop: drain queue, run turn, repeat."""
        try:
            while True:
                with self._input_lock:
                    if not self._input_queue:
                        # Check for pending interjections to keep the loop alive.
                        from interject import poll_interjections

                        pending = poll_interjections()
                        if pending:
                            self._input_queue.extend(pending)
                            # Fall through to process them
                        else:
                            return  # all queued messages processed
                    texts = list(self._input_queue)
                    self._input_queue.clear()

                text = "\n\n".join(texts)
                self._cancel_event.clear()
                self._run_turn(text)
        finally:
            # Always send idle when the turn loop exits, so the renderer
            # knows to reset the running indicator / cancel button.
            send_msg({"type": "idle"})

    def _run_turn(self, text: str) -> None:
        """Execute a single agent turn."""
        # Notify the renderer that a turn is starting, so it can show
        # the running indicator / cancel button.
        send_msg({"type": "turn_start"})

        # Belt-and-suspenders: sub-agents may mutate config.stream when they
        # share the same config object.  Force it back to True for the
        # orchestrator so streaming always works.
        self.config.stream = True
        self.messages.append({"role": "user", "content": text})

        # Reset sub-agent auto-report tracking for this turn
        self._pending_subagents.clear()
        self._auto_report_flag = False

        # Wire sub-agent events to Electron via a callback on the tool context.
        # The callback is called from _spawn_one (agent_ops.py) on sub-agent
        # lifecycle events (start, output, end).
        #
        # IMPORTANT: We set this once during init() and NEVER clear it after
        # run_agent_turn returns.  If we clear it, sub-agents spawned by other
        # sub-agents (grandchildren) won't find a callback because the parent
        # turn may have already finished and cleared it.  The callback closure
        # captures `self` (AgentRunner) which lives for the whole session, so
        # it's safe to keep permanently.
        from tools import _TOOL_CONTEXT

        if getattr(_TOOL_CONTEXT, "_subagent_callback", None) is None:

            def _sub_cb(event_type: str, data: dict) -> None:
                if event_type == "start":
                    task_id = data.get("task_id", "")
                    self._pending_subagents.add(task_id)
                    self._running_subagent_count += 1
                    self._total_subagents += 1
                    self._callbacks.on_subagent_start(
                        task_id,
                        data.get("parent_id", ""),
                        data.get("name", ""),
                        data.get("desc", ""),
                    )
                    self.send_status()  # push updated sub-agent count to UI
                elif event_type == "output":
                    self._callbacks.on_subagent_output(
                        data.get("task_id", ""), data.get("line", "")
                    )
                elif event_type == "end":
                    task_id = data.get("task_id", "")
                    self._pending_subagents.discard(task_id)
                    self._running_subagent_count = max(
                        0, self._running_subagent_count - 1
                    )
                    self._callbacks.on_subagent_end(
                        task_id, data.get("ok", False), data.get("content", "")
                    )
                    self.send_status()  # push updated sub-agent count to UI
                    # Auto-report: if all sub-agents from this turn
                    # have finished, queue a synthesis prompt so the
                    # orchestrator processes and reports their results.
                    if not self._pending_subagents and not self._auto_report_flag:
                        self._auto_report_flag = True
                        # Collect actual results from the runtime to include
                        # in the prompt, so the synthesis is concrete.
                        results_summary = ""
                        try:
                            from tools import _TOOL_CONTEXT as _ctx

                            rt = getattr(_ctx, "_agent_runtime", None)
                            if rt is not None:
                                # Gather all completed sub-agent results
                                lines = []
                                for tid, res in sorted(rt.results.items()):
                                    status = "OK" if res.success else "FAIL"
                                    preview = (res.content or "")[:200].replace(
                                        "\n", " "
                                    )
                                    lines.append(f"  [{tid}] {status}: {preview}")
                                if lines:
                                    results_summary = "\n" + "\n".join(lines) + "\n"
                        except Exception:
                            pass  # best-effort
                        self.submit(
                            "[Report: All sub-agents have completed. "
                            "Synthesize their results and report to the user."
                            + results_summary
                            + "]"
                        )
                elif event_type == "tool_start":
                    self._callbacks.on_subagent_tool_start(
                        data.get("task_id", ""),
                        data.get("tool_name", ""),
                        data.get("tool_args", ""),
                    )
                elif event_type == "tool_end":
                    self._callbacks.on_subagent_tool_end(
                        data.get("task_id", ""),
                        data.get("tool_name", ""),
                        data.get("ok", False),
                        data.get("content", ""),
                    )
                elif event_type == "thought":
                    self._callbacks.on_subagent_thought(
                        data.get("task_id", ""), data.get("text", "")
                    )

            _TOOL_CONTEXT._subagent_callback = _sub_cb

        try:
            msg = run_agent_turn(
                self.messages,
                self.config,
                self.write_gate,
                self.read_gate,
                on_token=self._callbacks.on_token,
                on_tool_start=self._callbacks.on_tool_start,
                on_tool_end=self._callbacks.on_tool_end,
                on_tool_output=self._callbacks.on_tool_output,
                cancel_event=self._cancel_event,
                session=self.session,
            )
        except Exception as e:
            # Safety: reset thinking flag and close any open thinking block
            # so the renderer doesn't route subsequent tokens to the wrong panel.
            if self._callbacks._in_thinking:
                send_msg({"type": "thinking_end"})
            self._callbacks._in_thinking = False
            if not self._cancel_event.is_set():
                send_msg({"type": "error", "message": str(e)})
            # Always send turn_complete so the renderer resets its loading state
            send_msg(
                {
                    "type": "turn_complete",
                    "usage": {
                        "total_tokens": self._total_tokens,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                    },
                    "turn_count": self._total_turns,
                }
            )
            return
        # Safety: reset thinking flag so a stuck marker doesn't persist across turns
        self._callbacks._in_thinking = False

        if self._cancel_event.is_set():
            send_msg(
                {
                    "type": "turn_complete",
                    "usage": {
                        "total_tokens": self._total_tokens,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                    },
                    "turn_count": self._total_turns,
                    "cancelled": True,
                }
            )
            return

        if msg is not None:
            self._total_turns += msg.get("_turn_count", 0)
            usage = msg.get("_total_usage") or {}
            self._total_tokens += usage.get("total_tokens", 0)

            # -- Reasonix-style cost tracking --
            self._session_cost.record_turn(
                model=self.config.model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0),
                cache_miss_tokens=usage.get("prompt_cache_miss_tokens", 0),
            )

        # Persist
        self.messages = self.memory.save(self.messages)

        # Consume prune summary (kept for LLM context but hidden from chat).
        summary = self.memory.last_prune_summary
        if summary:
            self.memory.last_prune_summary = ""

        # Notify Electron
        sc = self._session_cost
        turn_usage = {
            "total_tokens": self._total_tokens,
            "prompt_tokens": sc.last_turn.prompt_tokens if sc.last_turn else 0,
            "completion_tokens": sc.last_turn.completion_tokens if sc.last_turn else 0,
            "turn_cost": format_cost_cny(sc.last_turn.total_cost)
            if sc.last_turn
            else "-",
            "session_cost": format_cost_cny(sc.total_cost),
            "session_turns": sc.turn_count,
            "cache_hit_rate": round(sc.cache_hit_rate * 100)
            if sc.cache_hit_rate is not None
            else None,
            "subagent_running": self._running_subagent_count,
            # Include current cached balance so the status bar updates immediately.
            # The async re-fetch below will push the latest balance when it completes.
            "balance": self._balance,
        }
        # Include plan state in turn_complete so the UI updates after each turn
        plan_steps = getattr(_TOOL_CONTEXT, "_plan_steps", [])
        plan_done = getattr(_TOOL_CONTEXT, "_plan_done", set())
        send_msg(
            {
                "type": "turn_complete",
                "usage": turn_usage,
                "turn_count": self._total_turns,
                "plan_steps": list(plan_steps) if plan_steps else [],
                "plan_done": sorted(plan_done) if plan_done else [],
            }
        )

        # Re-fetch balance after every turn so the wallet display stays current.
        self._fetch_balance_async()

    # -- commands -------------------------------------------------------

    def handle_command(self, command: str) -> None:
        """Handle /slash commands."""
        cmd = command.lower().strip()

        if cmd == "/clear":
            self._cancel_event.set()
            knowledge = (
                self.memory.get_top_knowledge(limit=15)
                if hasattr(self, "memory")
                else []
            )
            self.messages = [
                {"role": "system", "content": build_system_prompt(self.config)},
                {"role": "user", "content": build_session_header(self.config)},
                {
                    "role": "user",
                    "content": build_startup_context(
                        self.config.workspace, knowledge=knowledge, memory_store=self.memory
                    ),
                },
            ]
            clear_api_cache()
            self.memory.clear()
            # Reset one-time context-injection flags so HANDOFF, STATE,
            # session summary etc. are re-injected on the next turn.
            for attr in (
                "_handoff_injected",
                "_state_txt_injected",
                "_tasks_injected",
                "_session_summary_injected",
                "_scratchpad_injected",
                "_git_diff_injected",
            ):
                try:
                    delattr(_TOOL_CONTEXT, attr)
                except AttributeError:
                    pass
            # Reset plan state so old plans don't linger
            _TOOL_CONTEXT._plan_steps = []
            _TOOL_CONTEXT._plan_done = set()
            _TOOL_CONTEXT._plan_last_advanced_turn = 0
            # Persist cleared plan to SQLite
            try:
                self.memory.set_plan([], [])
            except Exception:
                pass
            self._total_turns = 0
            self._total_tokens = 0
            self._session_cost = SessionCost()
            self._total_subagents = 0
            self._running_subagent_count = 0
            self.send_status()  # push cleared plan state to UI
            send_msg({"type": "response", "lines": ["--- conversation cleared ---"]})
            return

        if cmd == "/stats":
            sc = self._session_cost
            bal = ""
            if self._balance and self._balance.get("available"):
                bal = f", wallet: {self._balance['display']}"
            send_msg(
                {
                    "type": "response",
                    "lines": [
                        f"Model: {self.config.model}  |  Session: {sc.turn_count} turns, "
                        f"{sc.total_prompt_tokens + sc.total_completion_tokens} tokens, "
                        f"cost: {format_cost_cny(sc.total_cost)}{bal}",
                        f"Cache hit: {round(sc.cache_hit_rate * 100) if sc.cache_hit_rate is not None else 'N/A'}%  |  "
                        f"Sub-agents: {self._total_subagents} total, {self._running_subagent_count} running",
                    ],
                }
            )
            return

        if cmd.startswith("/session"):
            parts = cmd.split(maxsplit=2)
            sub = parts[1] if len(parts) > 1 else ""
            arg = parts[2] if len(parts) > 2 else ""
            if sub == "list":
                from core.config import list_sessions

                sessions = list_sessions(self.workspace)
                send_msg(
                    {
                        "type": "response",
                        "lines": [
                            f"Sessions: {', '.join(sessions) if sessions else 'none'}"
                        ],
                    }
                )
            elif sub == "new" and arg:
                from core.config import switch_session

                sd = switch_session(self.workspace, arg, self.memory, self.config)
                self.messages = self.memory.save(self.messages)
                self.memory.close()
                self.memory = sd["memory"]
                self.messages = sd["messages"]
                self._total_turns = 0
                self._total_tokens = 0
                send_msg({"type": "response", "lines": [f"Created session '{arg}'."]})
            elif sub == "switch" and arg:
                from core.config import switch_session

                self.messages = self.memory.save(self.messages)
                self.memory.close()
                sd = switch_session(self.workspace, arg, self.memory, self.config)
                self.memory = sd["memory"]
                self.messages = sd["messages"]
                self._total_turns = 0
                self._total_tokens = 0
                send_msg({"type": "response", "lines": [f"Switched to '{arg}'."]})
            elif sub == "delete" and arg:
                from core.config import delete_session

                ok, msg = delete_session(self.workspace, arg)
                send_msg({"type": "response", "lines": [msg]})
            else:
                send_msg(
                    {
                        "type": "response",
                        "lines": [
                            "Usage: /session new <name> | switch <name> | delete <name> | list"
                        ],
                    }
                )
            return

        if cmd == "/export":
            import datetime

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"conversation_{ts}.md"
            path = os.path.join(self.config.workspace, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write("# mini_agent Conversation\n\n")
                for msg in self.messages:
                    role = msg["role"].upper()
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        f.write(f"## {role}\n\n{content}\n\n")
            send_msg({"type": "response", "lines": [f"Exported to {fname}"]})
            return

        if cmd == "/test-svg":
            lines = [
                "[OK] SVG icon test -- check-circle",
                "[FAIL] SVG icon test -- x-circle",
                "WARNING: SVG icon test -- warning",
                "[IDEA] SVG icon test -- lightbulb",
                "[DIR] SVG icon test -- folder",
                "[WRENCH] SVG icon test -- wrench",
                "? SVG icon test -- rocket",
                "(*) SVG icon test -- star",
                "? SVG icon test -- bug",
                "? SVG icon test -- fire",
                "? SVG icon test -- burst",
            ]
            send_msg(
                {
                    "type": "response",
                    "lines": [line for line in lines],
                }
            )
            return

        if cmd == "/demo-tree":
            import threading
            import time as _time

            def _send_tree_demo():
                agents = [
                    (
                        "task_alpha",
                        "orchestrator",
                        "ALPHA",
                        "Search all source files for 'TODO' comments",
                    ),
                    (
                        "task_bravo",
                        "orchestrator",
                        "BRAVO",
                        "Count lines of code in renderer/src/",
                    ),
                    (
                        "task_charlie",
                        "orchestrator",
                        "CHARLIE",
                        "Check package.json for outdated deps",
                    ),
                    (
                        "task_delta",
                        "orchestrator",
                        "DELTA",
                        "Generate a dependency graph from imports",
                    ),
                ]
                # Start all 4
                for tid, pid, name, desc in agents:
                    send_msg(
                        {
                            "type": "subagent_start",
                            "task_id": tid,
                            "parent_id": pid,
                            "name": name,
                            "desc": desc,
                        }
                    )

                # Tool calls + thoughts for each
                tool_data = [
                    (
                        "task_alpha",
                        "grep_search",
                        'pattern="TODO" path="."',
                        "Searching renderer/src/ for TODO markers...",
                    ),
                    (
                        "task_bravo",
                        "run_shell",
                        "find renderer/src -name '*.jsx' | xargs wc -l",
                        "Counting JSX files...",
                    ),
                    (
                        "task_charlie",
                        "read_file",
                        "package.json",
                        "Reading dependency manifest...",
                    ),
                    (
                        "task_delta",
                        "run_shell",
                        "pipdeptree --json",
                        "Building import tree...",
                    ),
                ]
                for tid, tool, args, thought in tool_data:
                    send_msg(
                        {"type": "subagent_thought", "task_id": tid, "text": thought}
                    )
                    send_msg(
                        {
                            "type": "subagent_tool_start",
                            "task_id": tid,
                            "tool_name": tool,
                            "tool_args": args,
                        }
                    )
                    _time.sleep(0.1)
                    send_msg(
                        {
                            "type": "subagent_tool_end",
                            "task_id": tid,
                            "tool_name": tool,
                            "ok": True,
                            "content": f"Done ({tid})",
                        }
                    )

                # More thoughts for agents that are still "thinking"
                extra_thoughts = [
                    ("task_alpha", "Found 42 TODO markers in 8 files."),
                    ("task_bravo", "Counted 2,847 lines across 12 JSX files."),
                    ("task_charlie", "3 packages have newer versions available."),
                    ("task_delta", "Generated DOT graph with 23 nodes, 41 edges."),
                ]
                for tid, thought in extra_thoughts:
                    send_msg(
                        {"type": "subagent_thought", "task_id": tid, "text": thought}
                    )

                # End all
                for tid, _, name, _ in agents:
                    send_msg(
                        {
                            "type": "subagent_end",
                            "task_id": tid,
                            "ok": True,
                            "content": f"{name} completed successfully.",
                        }
                    )

                send_msg({"type": "response", "lines": ["--- Demo tree injected ---"]})

            threading.Thread(target=_send_tree_demo, daemon=True).start()
            return

        if cmd == "/init":
            from tools.file_ops import _init_rules

            rg = ReadSafetyGate(self.config.workspace)
            result = _init_rules({}, None, rg)
            lines = str(result.content).split("\n") if result.content else []
            send_msg({"type": "response", "lines": lines})
            return

        if cmd.startswith("/workspace"):
            parts = command.split(maxsplit=1)
            new_path = parts[1].strip() if len(parts) > 1 else ""
            if not new_path:
                send_msg({"type": "response", "lines": ["Usage: /workspace <path>"]})
                return

            # Resolve the path -- os.path.abspath may hang on stale network mounts.
            # Use a short timeout to avoid blocking the entire backend.
            ok_abspath, new_workspace = _try_with_timeout(
                lambda: os.path.abspath(new_path),
                timeout=4.0,
                description="os.path.abspath",
            )
            if not ok_abspath:
                send_msg(
                    {
                        "type": "error",
                        "message": f"Timeout resolving path: {new_path}. "
                        "The remote share may be unavailable.",
                    }
                )
                return

            ok_isdir, is_dir = _try_with_timeout(
                lambda: os.path.isdir(new_workspace),
                timeout=4.0,
                description="os.path.isdir",
            )
            if not ok_isdir or not is_dir:
                send_msg(
                    {
                        "type": "response",
                        "lines": [f"Not a directory or inaccessible: {new_workspace}"],
                    }
                )
                return

            # Persist old session before switching
            self.messages = self.memory.save(self.messages)
            self.memory.close()
            self.workspace = new_workspace
            self._shell_cwd = new_workspace

            # Notify the UI that we're loading the new workspace
            send_msg(
                {
                    "type": "status",
                    "workspace": new_workspace,
                    "session_name": "loading...",
                    "git_branch": "",
                    "git_dirty": False,
                    "restored_count": 0,
                    "model": self.config.model,
                }
            )

            # init_session may be slow on remote workspaces.
            # Use a generous 15s timeout for remote paths; 8s for local.
            init_timeout = 15.0 if _is_remote_workspace(new_workspace) else 8.0
            ok_init, new_data = _try_with_timeout(
                lambda: init_session(new_workspace, progress_callback=None),
                timeout=init_timeout,
                description="init_session",
            )
            if not ok_init:
                send_msg(
                    {
                        "type": "error",
                        "message": f"Timeout initializing workspace: {new_workspace}. "
                        "The remote share may be too slow. "
                        "Try a local workspace instead.",
                    }
                )
                # Roll back -- keep using old config
                self.memory = None  # will be recreated below
                return

            try:
                self.config = new_data["config"]
                self.config.stream = True
                self.write_gate = new_data["write_gate"]
                self.read_gate = new_data["read_gate"]
                self.memory = new_data["memory"]
                self.messages = new_data["messages"]
                self.session.close()
                self.session = new_data["session"]
                self._total_turns = 0
                self._total_tokens = 0
                self._refresh_git_status()
                self.send_status()
                send_msg(
                    {
                        "type": "response",
                        "lines": [f"Workspace set to: {new_workspace}"],
                    }
                )
            except Exception as exc:
                send_msg({"type": "error", "message": str(exc)})
            return

        if cmd == "/cancel":
            self.cancel()
            send_msg({"type": "response", "lines": ["--- cancelled ---"]})
            return

        if cmd.startswith("/sh "):
            shell_cmd = command[4:].strip()
            if not shell_cmd:
                send_msg({"type": "response", "lines": ["Usage: /sh <command>"]})
                return
            try:
                # Wrap the command so we can capture the final CWD and exit
                # code.  The sentinel markers are printed on their own lines
                # after the user command completes.
                wrapped_cmd = (
                    f"{shell_cmd}; "
                    f"_e=$?; "
                    f"printf '\\n__SHELL_CWD__%s\\n__SHELL_EXIT__%d\\n' "
                    f'"$(pwd)" "$_e"'
                )
                stdout_text, _pty_rc = _run_shell_pty(
                    wrapped_cmd, cwd=self._shell_cwd, timeout=30.0
                )

                # Parse the sentinel from the end of the output.
                clean_text = (
                    stdout_text.rstrip().replace("\r\n", "\n").replace("\r", "\n")
                )
                cwd_updated = False
                try:
                    parts = clean_text.rsplit("\n__SHELL_CWD__", 1)
                    if len(parts) == 2:
                        command_output = parts[0].rstrip("\n")
                        tail_parts = parts[1].split("\n", 2)
                        if len(tail_parts) >= 2:
                            new_cwd = tail_parts[0]
                            m = re.match(r"__SHELL_EXIT__(\d+)", tail_parts[1])
                            if m:
                                clean_text = command_output
                                exit_code = int(m.group(1))
                                if new_cwd and os.path.isdir(new_cwd):
                                    cwd_updated = True
                                    if new_cwd != self._shell_cwd:
                                        self._shell_cwd = new_cwd
                                    if new_cwd != self.workspace:
                                        self.workspace = new_cwd
                                        # Update the safety gates so file ops work
                                        # from the new directory.
                                        self.read_gate = ReadSafetyGate(new_cwd)
                                        self.write_gate = WriteSafetyGate(new_cwd)
                                        # Notify the frontend that the workspace
                                        # has changed.
                                        session_name = "default"
                                        db_path = getattr(self.memory, "_db_path", "")
                                        if db_path:
                                            m = re.search(
                                                r"_session_(.+)\\.db$", db_path
                                            )
                                            if m:
                                                session_name = m.group(1)
                                        send_msg(
                                            {
                                                "type": "status",
                                                "workspace": new_cwd,
                                                "session_name": session_name,
                                                "git_branch": "",
                                                "git_dirty": False,
                                                "restored_count": 0,
                                                "model": self.config.model,
                                            }
                                        )
                except (ValueError, re.error):
                    pass  # malformed sentinel; fall through with raw output

                if not cwd_updated:
                    exit_code = _pty_rc
                    clean_text = (
                        stdout_text.rstrip().replace("\r\n", "\n").replace("\r", "\n")
                    )

                lines = clean_text.split("\n") if clean_text else []
                if exit_code != 0 and not any(
                    line.strip().startswith("(exit ") for line in lines
                ):
                    lines.append(f"(exit {exit_code})")
                send_msg(
                    {
                        "type": "shell_output",
                        "lines": lines,
                        "exit_code": exit_code,
                        "command": shell_cmd,
                    }
                )
            except subprocess.TimeoutExpired:
                send_msg(
                    {
                        "type": "shell_output",
                        "lines": ["(timed out after 30s)"],
                        "exit_code": -1,
                        "command": shell_cmd,
                    }
                )
            except Exception as e:
                send_msg(
                    {
                        "type": "shell_output",
                        "lines": [f"(error: {e})"],
                        "exit_code": -1,
                        "command": shell_cmd,
                    }
                )
            return

        if cmd.startswith("/autocomplete "):
            partial = command[len("/autocomplete ") :].strip()
            if not partial:
                send_msg({"type": "autocomplete_result", "completions": []})
                return
            try:
                completions = self._llm_autocomplete(partial)
                send_msg({"type": "autocomplete_result", "completions": completions})
            except Exception:
                send_msg({"type": "autocomplete_result", "completions": []})
            return

        if cmd == "/help":
            lines = [
                "Available commands:",
                "  /help               Show this help",
                "  /cancel             Cancel the current running turn",
                "  /clear              Clear conversation history and reset session",
                "  /sh <command>       Run a shell command (e.g. /sh ls -la)",
                "  /stats              Show session stats (turns, tokens, cost, cache)",
                "  /export             Export conversation to workspace as markdown",
                "  /init               Initialize project rules (.mini_agent.rules)",
                "  /workspace <path>   Switch to a different workspace directory",
                "  /session list       List all saved sessions",
                "  /session new <name> Create and switch to a new session",
                "  /session switch <n> Switch to an existing session",
                "  /session delete <n> Delete a saved session",
                "",
                "Type a message to start a conversation with the agent.",
                "Prefix with /sh to run a shell command directly.",
            ]
            send_msg({"type": "response", "lines": lines})
            return

        send_msg({"type": "response", "lines": [f"Unknown command: {command}"]})

    def _llm_autocomplete(self, partial: str) -> list[dict]:
        """Return a short list of shell command completions via the LLM.

        Uses a fast/flash model (never the thinking model) so completions
        appear within the 350ms debounce window on the frontend.
        """
        import requests

        # ── pick the fastest model for this provider ──────────────────────────
        provider = getattr(self.config, "api_provider", "deepseek")
        _AUTOCOMPLETE_MODEL = {
            "deepseek": "deepseek-chat",  # non-reasoning v3; v4 models burn tokens on thinking
            "moonshot": "kimi-k2.6",
            "claude": "claude-haiku-4-5",
            "xai": "grok-4.1",
            "qwen": "qwen-flash",
            "gemini": "gemini-3.5-flash",
        }
        fast_model = _AUTOCOMPLETE_MODEL.get(provider, self.config.model)

        prompt = (
            "You are a command-line autocomplete system. "
            "Given a partially typed shell command, suggest 3-5 reasonable completions.\n"
            "Return ONLY the completions, one per line. No explanation, no formatting, no numbering.\n"
            "Rules:\n"
            "- If the partial is a path, suggest real-looking subdirectories/files.\n"
            "- If the partial is a flag prefix (e.g. '--'), suggest matching flags.\n"
            "- For 'git', suggest git subcommands.\n"
            "- Be concise; each completion is a single line containing only the completion.\n"
            "- Append a continuation to the partial, not a replacement.\n"
            "\n"
            f"Command so far: {partial}\n"
            f"Completions:"
        )

        payload = {
            "model": fast_model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 100,
            "temperature": 0.2,
            "stream": False,
        }

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        try:
            r = requests.post(
                self.config.api_url,
                headers=headers,
                json=payload,
                timeout=5,
            )
            if r.status_code != 200:
                return []
            text = r.json()["choices"][0]["message"]["content"].strip()
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            # Limit to 5 and remove numbering/bullets if present
            completions = []
            for line in lines[:5]:
                line = line.lstrip("0123456789.-*#) ")
                if line:
                    completions.append({"label": line, "detail": "AI suggestion"})
            return completions
        except Exception:
            return []

    def cancel(self) -> None:
        """Cancel the current turn."""
        self._cancel_event.set()

    def set_model(self, model: str) -> None:
        """Switch the LLM model (and provider if needed) on the fly."""
        if not model:
            return
        provider = getattr(self.config, "api_provider", "")
        old_provider = provider
        prefix = provider + "/"

        # Map of bare model names -> their native provider
        _MODEL_TO_PROVIDER = {
            "deepseek-v4-pro": "deepseek",
            "deepseek-v4-flash": "deepseek",
            "deepseek-chat": "deepseek",
            "kimi-k2.7-code": "moonshot",
            "kimi-k2.6": "moonshot",
            "claude-opus-4-8": "claude",
            "claude-sonnet-4-5": "claude",
            "claude-haiku-4-5": "claude",
            "grok-4.3": "xai",
            "grok-4.1": "xai",
            "qwen-plus": "qwen",
            "qwen-flash": "qwen",
            "qwen3-max": "qwen",
            "qwen3-coder": "qwen",
            "gemini-3.5-flash": "gemini",
            "gemini-3.5-pro": "gemini",
            "qwen3.6:27b": "ollama",
        }

        if model.startswith(prefix):
            # Model matches current provider prefix (e.g. "deepseek/deepseek-v4-flash"
            # when provider is "deepseek"). Strip prefix for native API.
            model = model[len(prefix) :]
        elif "/" in model:
            # Model has a different provider prefix (e.g. "moonshotai/kimi-k2.7-code"
            # when provider is "deepseek"). Switch to OpenRouter and keep the prefix.
            new_provider = "openrouter"
            err = _switch_to_provider(self.config, new_provider)
            if err:
                send_msg({"type": "response", "lines": [f"Error: {err}"]})
                return
            provider = new_provider
        elif model in _MODEL_TO_PROVIDER:
            # Bare model name — switch to its native provider.
            new_provider = _MODEL_TO_PROVIDER[model]
            if new_provider != provider:
                err = _switch_to_provider(self.config, new_provider)
                if err:
                    send_msg({"type": "response", "lines": [f"Error: {err}"]})
                    return
                provider = new_provider
        # else: bare model name, keep current provider

        old = self.config.model
        self.config.model = model
        self.send_status()
        provider_tag = f" (→ {provider})" if provider != old_provider else ""
        send_msg(
            {"type": "response", "lines": [f"Model: {old} -> {model}{provider_tag}"]}
        )


# ---------------------------------------------------------------------------
# Main -- JSON-lines event loop
# ---------------------------------------------------------------------------


def main() -> None:
    # Disable Python buffering on stdout
    sys.stdout.reconfigure(line_buffering=True) if hasattr(
        sys.stdout, "reconfigure"
    ) else None

    runner = AgentRunner()

    # Start heartbeat so the Electron watchdog doesn't kill us during long
    # blocking operations (run_shell, slow API calls, large file reads).
    _heartbeat_stop = threading.Event()
    _start_heartbeat(_heartbeat_stop)

    # Send initial ready + status
    send_msg({"type": "ready", "model": runner.config.model})
    runner.send_status()

    # Event loop: read messages from stdin, dispatch
    while True:
        msg = read_msg()
        if msg is None:
            # EOF -- Electron closed stdin
            break

        msg_type = msg.get("type", "")

        if msg_type == "submit":
            runner.submit(msg.get("text", ""))

        elif msg_type == "command":
            runner.handle_command(msg.get("command", ""))

        elif msg_type == "cancel":
            runner.cancel()

        elif msg_type == "interject":
            # User typed while agent was working — queue the message
            # so it gets injected at the next turn boundary via
            # context_inject._inject_interjections().
            from interject import push_interjection

            push_interjection(msg.get("text", ""))
            # If no turn is currently running, start one now so the
            # interjection gets processed immediately.  Otherwise it
            # will be picked up by _turn_loop after the current turn
            # completes.
            if runner._turn_thread is None or not runner._turn_thread.is_alive():
                runner._start_turn()

        elif msg_type == "set_model":
            runner.set_model(msg.get("model", ""))

        elif msg_type == "get_status":
            runner.send_status()

        elif msg_type == "session_list":
            from core.config import list_sessions

            sessions = list_sessions(runner.workspace)
            current = ""
            db_path = getattr(runner.memory, "_db_path", "")
            if db_path:
                import re

                m = re.search(r"_session_(.+)\.db$", db_path)
                current = m.group(1) if m else "default"
            else:
                current = "default"
            send_msg(
                {
                    "type": "session_list_result",
                    "sessions": sessions,
                    "current": current,
                }
            )

        elif msg_type == "session_switch":
            from core.config import switch_session

            name = msg.get("name", "")
            if not name:
                send_msg(
                    {"type": "session_list_result", "error": "Session name required."}
                )
            else:
                runner.messages = runner.memory.save(runner.messages)
                runner.memory.close()
                sd = switch_session(
                    runner.workspace, name, runner.memory, runner.config
                )
                runner.memory = sd["memory"]
                runner.messages = sd["messages"]
                runner._total_turns = 0
                runner._total_tokens = 0
                runner.send_status()

        elif msg_type == "session_new":
            from core.config import switch_session

            name = msg.get("name", "")
            if not name:
                send_msg(
                    {"type": "session_list_result", "error": "Session name required."}
                )
            else:
                # switch_session creates a new session if it doesn't exist
                sd = switch_session(
                    runner.workspace, name, runner.memory, runner.config
                )
                runner.messages = runner.memory.save(runner.messages)
                runner.memory.close()
                runner.memory = sd["memory"]
                runner.messages = sd["messages"]
                runner._total_turns = 0
                runner._total_tokens = 0
                runner.send_status()

        elif msg_type == "session_delete":
            from core.config import delete_session

            name = msg.get("name", "")
            if not name:
                send_msg(
                    {"type": "session_list_result", "error": "Session name required."}
                )
            else:
                ok, msg_text = delete_session(runner.workspace, name)
                if ok and name == getattr(runner, "_session_name", None):
                    # Deleted the current session -- switch to default
                    from core.config import switch_session

                    sd = switch_session(
                        runner.workspace, "default", runner.memory, runner.config
                    )
                    runner.memory = sd["memory"]
                    runner.messages = sd["messages"]
                    runner._total_turns = 0
                    runner._total_tokens = 0
                send_msg(
                    {"type": "session_delete_result", "ok": ok, "message": msg_text}
                )
                runner.send_status()

        elif msg_type == "shutdown":
            break

        else:
            send_msg({"type": "error", "message": f"Unknown message type: {msg_type}"})

    # Cleanup
    _heartbeat_stop.set()
    try:
        runner.messages = runner.memory.save(runner.messages)
        runner.memory.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
