#!/usr/bin/env python3
"""
agent_ops.py -- session utility tools for mini_agent.
Tools: restore_file, session_stats, recall_turn, remember, read_image
restore_file reverts a file to its backup.
session_stats returns cost and runtime statistics.
recall_turn replays a previous turn for debugging.
remember captures a learning to project knowledge.
read_image analyzes an image file via the LLM.
"""
from __future__ import annotations
import base64
import os
from core.safety import ReadSafetyGate, WriteSafetyGate
from tools import _register, _summarize, ToolResult, _TOOL_CONTEXT

# ---------------------------------------------------------------------------
# restore_file -- session undo
# ---------------------------------------------------------------------------
from tools._file_utils import _BACKUPS
import shutil
import os as _os_restore


@_register("restore_file")


def _restore_file(args: dict, wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Restore a file from a git checkpoint or session backup.

    Tries git checkout first (if available and a checkpoint exists),
    then falls back to the session file backup system.
    """
    path = args["path"]
    safety_result = wg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Restore blocked by safety layer: {safety_result.reason}",
        )
    resolved = safety_result.resolved_path

    # --- Try git checkpoint restore first (Dirac-inspired) ---
    try:
        from core.checkpoint import get_checkpoint_manager
        cm = get_checkpoint_manager(wg.workspace_root)
        if cm.is_available() and cm.checkpoint_count() > 0:
            if cm.restore_file(resolved):
                from tools import _MODIFIED_FILES, _MODIFIED_FILES_LOCK
                with _MODIFIED_FILES_LOCK:
                    _MODIFIED_FILES.discard(safety_result.resolved_path)
                # Also clean up backup if it exists
                _BACKUPS.pop(resolved, None)
                return ToolResult(
                    success=True,
                    content=f"Restored '{resolved}' from git checkpoint ({cm.last_checkpoint_sha()}).",
                )
    except Exception:
        pass  # fall through to backup system

    # --- Fall back to session file backup ---
    if resolved not in _BACKUPS:
        return ToolResult(
            success=False,
            content=f"No backup or checkpoint available for '{resolved}'. Only files modified this session can be restored.",
            hint="No backup exists. Either the file hasn't been modified this session, or it was already restored.",
        )
    backup_path = _BACKUPS[resolved]
    try:
        shutil.copy2(backup_path, resolved)
        del _BACKUPS[resolved]
        from tools import _MODIFIED_FILES, _MODIFIED_FILES_LOCK
        with _MODIFIED_FILES_LOCK:
            _MODIFIED_FILES.discard(safety_result.resolved_path)
        return ToolResult(
            success=True,
            content=f"Restored '{resolved}' from backup ({_os_restore.path.basename(backup_path)}).",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f"Error restoring '{resolved}': {e}",
        )


@_summarize("restore_file")


def _restore_file_summary(args: dict) -> str:
    return f"restore_file({args.get('path', '?')})"


@_register("session_stats")


def _session_stats(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Show session statistics: turns, tokens, context usage, plan progress."""
    turn_history = getattr(_TOOL_CONTEXT, "_turn_history", None) or {}
    turns_used = len(turn_history)
    memory_store = getattr(_TOOL_CONTEXT, "_memory_store", None)
    token_count = 0
    if memory_store is not None:
        token_count = memory_store.token_count
    CONTEXT_BUDGET = 800_000
    pct_used = (token_count / CONTEXT_BUDGET * 100) if CONTEXT_BUDGET else 0
    cache_stats = getattr(_TOOL_CONTEXT, "_cache_stats", None) or {}
    cache_hits = cache_stats.get("hits", 0)
    cache_misses = cache_stats.get("misses", 0)
    cache_calls = cache_stats.get("calls", 0)
    input_tokens = cache_stats.get("input_tokens", 0)
    output_tokens = cache_stats.get("output_tokens", 0)
    total_cache_tokens = cache_hits + cache_misses
    hit_rate_pct = (cache_hits / total_cache_tokens * 100) if total_cache_tokens > 0 else 0
    provider = getattr(_TOOL_CONTEXT, "_provider", None) or "deepseek"
    try:
        from core.config import PROVIDER_DEFAULTS
        pd = PROVIDER_DEFAULTS.get(provider)
        if pd and pd.input_price > 0:
            cost_without_cache = input_tokens / 1_000_000 * pd.input_price
            actual_input_cost = (
                cache_hits / 1_000_000 * pd.cache_hit_price +
                cache_misses / 1_000_000 * pd.input_price
            )
            output_cost = output_tokens / 1_000_000 * pd.output_price
            actual_total = actual_input_cost + output_cost
            saved = cost_without_cache - actual_input_cost
        else:
            saved = 0.0
            actual_total = 0.0
    except Exception:
        saved = 0.0
        actual_total = 0.0
    lines = [
        f"Turns used:    {turns_used}",
        f"Context tokens: {token_count} / {CONTEXT_BUDGET} ({pct_used:.1f}% used)",
    ]
    if cache_calls > 0:
        lines.append(
            f"API calls:      {cache_calls} | "
            f"input {input_tokens:} tok | output {output_tokens:} tok"
        )
        lines.append(
            f"Cache hit rate: {hit_rate_pct:.1f}% "
            f"({cache_hits:} cached / {total_cache_tokens:} tokens)"
        )
        if saved > 0:
            lines.append(
                f"Cost:          ${actual_total:.4f} "
                f"(saved ${saved:.4f} via cache)"
            )
        elif actual_total > 0:
            lines.append(f"Cost:          ${actual_total:.4f}")
    plan = getattr(_TOOL_CONTEXT, "_plan_steps", [])
    plan_done = getattr(_TOOL_CONTEXT, "_plan_done", set())
    if plan:
        done_count = len(plan_done)
        lines.append(f"Plan:           {done_count}/{len(plan)} steps done")
    return ToolResult(success=True, content="\n".join(lines))


@_summarize("session_stats")


def _session_stats_summary(args: dict) -> str:
    return "session_stats"


@_register("recall_turn")


def _recall_turn(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Return a summary of what happened on a given turn number."""
    turn = args.get("turn", 0)
    if not isinstance(turn, int) or turn < 1:
        return ToolResult(success=False, content="turn must be a positive integer")
    history = _TOOL_CONTEXT._turn_history
    if turn not in history:
        available = sorted(history.keys()) if history else []
        return ToolResult(
            success=True,
            content=(
                f"No record of turn {turn}. "
                + (f"Available turns: {available}" if available else "No turns recorded yet.")
            ),
        )
    return ToolResult(success=True, content=f"Turn {turn}:\n{history[turn]}")


@_summarize("recall_turn")


def _recall_turn_summary(args: dict) -> str:
    return f"recall_turn({args.get('turn', '?')})"


@_register("remember")


def _remember(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    """Store a project-level learning that persists across sessions.
    Saved to the project_knowledge table in the session SQLite DB.
    Auto-categorizes the learning if no category is provided.
    """
    topic = args.get("topic", "")
    detail = args.get("detail", "")
    category = args.get("category", "")
    if not topic.strip():
        return ToolResult(
            success=False,
            content="Missing required parameter: 'topic' (short topic label for this learning).",
        )
    if not category:
        try:
            from tools.failure_learning import suggest_category, KNOWLEDGE_CATEGORIES
            category = suggest_category(topic, detail)
            if category not in KNOWLEDGE_CATEGORIES:
                category = "general"
        except ImportError:
            category = "general"
    memory_store = getattr(_TOOL_CONTEXT, "_memory_store", None)
    if memory_store is None:
        return ToolResult(
            success=False,
            content="No memory store available. Project knowledge requires an active session.",
        )
    try:
        memory_store.add_knowledge(topic, detail, category or "general")
        return ToolResult(
            success=True,
            content=f"Remembered: [{category or 'general'}] {topic}",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f"Failed to store knowledge: {e}",
        )


@_summarize("remember")


def _remember_summary(args: dict) -> str:
    topic = args.get("topic", "?")
    return f"remember({topic})"


_IMAGE_EXTENSIONS: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}


def _guess_mime_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return _IMAGE_EXTENSIONS.get(ext, "image/png")


@_register("read_image")


def _read_image(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    """Read an image file, send it to GPT-4o, and return a text description."""
    import requests
    path = args.get("path", "")
    if not path:
        return ToolResult(success=False, content="Missing required parameter: 'path'.")
    sr = rg.check(path)
    if not sr.allowed:
        return ToolResult(success=False, content=f"Read blocked: {sr.reason}")
    import os as _os
    resolved = sr.resolved_path
    if not _os.path.isfile(resolved):
        return ToolResult(success=False, content=f"File not found: {path}")
    ext = _os.path.splitext(resolved)[1].lower()
    if ext not in _IMAGE_EXTENSIONS:
        return ToolResult(
            success=False,
            content=f"Unsupported image format: {ext}. Supported: {sorted(_IMAGE_EXTENSIONS)}",
        )
    mime_type = _IMAGE_EXTENSIONS[ext]
    try:
        with open(resolved, "rb") as f:
            image_bytes = f.read()
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
    except Exception as e:
        return ToolResult(success=False, content=f"Failed to read image: {e}")
    openai_api_key = _TOOL_CONTEXT.openai_api_key or ""
    if not openai_api_key:
        return ToolResult(
            success=False,
            content="OpenAI API key not configured. Set OPENAI_API_KEY env var or openai_api_key in .mini_agent.toml.",
        )
    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": args.get("prompt", "Describe this image in detail. What do you see?"),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64_data}",
                            "detail": "auto",
                        },
                    },
                ],
            }
        ],
        "max_tokens": 1000,
    }
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {openai_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        if resp.status_code != 200:
            return ToolResult(
                success=False,
                content=f"OpenAI API error ({resp.status_code}): {resp.text[:500]}",
            )
        data = resp.json()
        description = data["choices"][0]["message"]["content"]
        return ToolResult(success=True, content=description)
    except requests.exceptions.Timeout:
        return ToolResult(success=False, content="OpenAI API request timed out (60s).")
    except Exception as e:
        return ToolResult(success=False, content=f"OpenAI API request failed: {e}")


@_summarize("read_image")


def _read_image_summary(args: dict) -> str:
    path = args.get("path", "?")
    return f"read_image({path})"
