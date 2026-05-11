#!/usr/bin/env python3
"""
tools package — tool definitions, execution, and structured results for mini_agent.

Every tool execution returns a ToolResult (never a raw exception).
All read and write paths route through the safety gates.
Shell commands and searches run sandboxed inside the workspace root.

Adding a new tool requires:
    1. A ``_<name>`` implementation function decorated with ``@_register("name")``.
    2. A ``_<name>_summary`` function decorated with ``@_summarize("name")``.
    3. An entry in ``TOOLS`` (the API schema sent to the LLM).

Submodules:
    file_ops    — read_file, write_file, edit_file, list_directory, file_info
    shell_ops   — run_shell, search_files, run_tests, git
    search_ops  — semantic_search, web_search
"""

import json

from safety import ReadSafetyGate, WriteSafetyGate


# ---------------------------------------------------------------------------
# Tool definitions (API schema sent to the LLM)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_symbol",
            "description": "Find where a Python symbol (function, class, method name) is defined in the workspace. Returns file path and line number for each match. Much faster than grep/search_files for symbol lookup. Use this to locate definitions before editing code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name to find (e.g. '_request_with_retry', 'ToolResult'). Supports substring matching."
                    }
                },
                "required": [
                    "name"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file at the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read"
                    }
                },
                "required": [
                    "path"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file, overwriting if it exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to write"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write"
                    }
                },
                "required": [
                    "path",
                    "content"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file by replacing a specific string with another. Replaces the first occurrence of old_string with new_string. Returns an error if old_string is not found in the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit"
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact string to find and replace"
                    },
                    "new_string": {
                        "type": "string",
                        "description": "String to replace it with"
                    }
                },
                "required": [
                    "path",
                    "old_string",
                    "new_string"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the contents of a directory at the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the directory to list"
                    }
                },
                "required": [
                    "path"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command inside the workspace directory. Returns exit code, stdout, and stderr. Commands time out after 60 seconds. Use this to run tests, check syntax, invoke build tools, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute (e.g. 'python -m pytest test_safety.py -v')"
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Run in background, return immediately with task ID. Use task_status to check."
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Bypass the destructive-command guard. Default: false. Required for rm, mkfs, etc."
                    }
                },
                "required": [
                    "command"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a text pattern recursively in files within the workspace. Returns matching lines with file path and line number. Skips hidden directories, binary files, and common VCS/venv dirs. Capped at 50 results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text or substring to search for (case-sensitive by default). If regex is true, treated as a Python regex."
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (defaults to workspace root)"
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "If true, treat pattern as a Python regex. Default: false."
                    },
                    "ignore_case": {
                        "type": "boolean",
                        "description": "If true, case-insensitive search. Default: false."
                    }
                },
                "required": [
                    "pattern"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_info",
            "description": "Get metadata about a file or directory at the given path. Returns size, permissions, modification time, and type (file/directory). Also reports whether the path exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file or directory to inspect"
                    }
                },
                "required": [
                    "path"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run tests in the workspace. Returns structured pass/fail counts and failure details. Use after every code change to verify nothing broke. If 'path' is given, runs only those tests; otherwise runs all.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional: specific test file or directory to run (e.g. 'test_tools.py' or 'test_memory.py'). If omitted, runs all tests."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": "Search code by meaning using embeddings. Finds code chunks semantically similar to the query, even if they don't share keywords. Good for finding related functionality, similar patterns, or code that 'feels like' something. Indexes files live — no pre-indexing needed. Returns top 10 matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of what to find (e.g. 'error handling around file writes', 'retry logic')"
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (defaults to workspace root)"
                    }
                },
                "required": [
                    "query"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using Exa. Returns relevant pages with titles, URLs, and highlighted excerpts. Good for documentation lookup, API references, current information, and technical questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query. Be specific and use technical terms for best results."
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5, max 20)."
                    },
                    "search_type": {
                        "type": "string",
                        "description": "Search depth: 'auto' (default, balanced), 'fast', 'deep'. 'auto' works for most queries."
                    }
                },
                "required": [
                    "query"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git",
            "description": "Run a git command in the workspace. Supports: status, diff, log, init, add, commit, show, restore. All operations are local-only (no push/pull). Use 'diff' to see unstaged changes, 'status' to see file states, 'log' for recent commits, 'init' to initialize a repo, 'add' to stage files, 'commit' to commit staged changes, 'show' to read a committed version of a file, 'restore' to recover a file from the last commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subcommand": {
                        "type": "string",
                        "description": "Git subcommand: status, diff, log, init, add, or commit"
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional arguments: file paths for 'add', commit message for 'commit', etc."
                    }
                },
                "required": [
                    "subcommand"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "diff",
            "description": "Show unstaged changes (git diff) in the workspace. If 'path' is given, shows diff for that file only; otherwise shows all unstaged changes. Returns the raw diff output. Works even on files that haven't been staged.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional: specific file path to diff. If omitted, shows all unstaged changes."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_status",
            "description": "Check the status of a background shell task by its ID. background=True in run_shell returns a task_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task ID returned by run_shell with background=True"
                    }
                },
                "required": [
                    "task_id"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_scratchpad",
            "description": "Write content to the agent's scratchpad — a persistent working note that survives across turns. Use this to track your plan, progress, decisions, things you've tried, and open questions. The scratchpad is shown to you at the start of every turn. Overwrites previous content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Content to write to the scratchpad. Use markdown."
                    }
                },
                "required": [
                    "content"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_usages",
            "description": "Find all usages (references) of a Python symbol across the workspace. Returns file path, line number, and surrounding context for each usage. Much faster than grep for symbol references. Use this to find all callers of a function or all places a class/variable is used before refactoring.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name to find usages of (e.g. 'execute_tool', 'ToolResult')."
                    }
                },
                "required": [
                    "name"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verify",
            "description": "Run lint + relevant tests for files modified in the current session. Uses tracked writes/edits to find matching test files. Falls back to running all tests if nothing has been modified yet. Use after code changes to verify nothing broke before moving on.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


# ---------------------------------------------------------------------------
# Structured tool result
# ---------------------------------------------------------------------------

class ToolResult:
    """Structured result from a tool execution — never a raw exception.

    *hint* is an optional short diagnostic shown to the LLM to help it
    self-correct on malformed calls (invalid JSON, unknown parameters,
    wrong types, etc.).  It is included only on failure.
    """

    def __init__(self, success: bool, content: str, hint: str = "") -> None:
        self.success = success
        self.content = content
        self.hint = hint

    def to_dict(self) -> dict:
        d: dict = {"success": self.success, "content": self.content}
        if self.hint:
            d["hint"] = self.hint
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ---------------------------------------------------------------------------
# Tool dispatch registry
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, callable] = {}
_TOOL_SUMMARIES: dict[str, callable] = {}
_TOOL_CONTEXT: dict = {}

# Per-turn cache for read-only tools. Cleared by run_agent_turn each turn.
# Key: (tool_name, sorted_args_json). Cached read_file/file_info/etc.
_TOOL_CACHE: dict[str, "ToolResult"] = {}

# Files modified by write/edit — used by verify
_MODIFIED_FILES: set[str] = set()
_TASK_REGISTRY: dict[str, subprocess.Popen] = {}  # background shell task registry
_CACHEABLE = frozenset({
    "read_file", "file_info", "list_directory",
    "search_files", "semantic_search", "web_search",
})


def set_context(**kwargs) -> None:
    """Set module-level context accessible to tool implementations."""
    _TOOL_CONTEXT.update(kwargs)


def _register(name: str):
    """Decorator: register an implementation function in the dispatch table."""
    def decorator(fn):
        _TOOL_DISPATCH[name] = fn
        return fn
    return decorator


def _summarize(name: str):
    """Decorator: register a summary function for verbose logging."""
    def decorator(fn):
        _TOOL_SUMMARIES[name] = fn
        return fn
    return decorator


def clear_tool_cache() -> None:
    """Clear the per-turn tool cache. Called at the start of each agent turn."""
    _TOOL_CACHE.clear()


def _repair_json(raw: str) -> tuple[object, bool]:
    """Attempt to repair common LLM-generated JSON malformations.

    Returns (parsed_value, was_repaired).  If all repair attempts fail the
    original raw string is re-raised via json.loads so callers see a standard
    JSONDecodeError.

    Repairs attempted (in order, each retried independently, then combinations):
    1. Trailing commas before ``]`` or ``}``
    2. Single-quoted strings → double quotes
    3. Unquoted object keys
    4. 1+2, 1+3, 2+3, 1+2+3 (combinations)
    """
    import re

    # Individual fixes
    fix1 = re.sub(r',\s*([}\]])', r'\1', raw)

    fix2 = raw
    if "'" in raw:
        fix2 = raw.replace("'", '"')

    fix3 = raw
    if not raw.strip().startswith('['):
        fix3 = re.sub(r'(\w+)(\s*:)', r'"\1"\2', raw)

    # Combinations — apply fixes in sequence on copies
    def _apply_combo(base: str, *indices: int) -> str:
        s = base
        for i in indices:
            if i == 1:
                s = re.sub(r',\s*([}\]])', r'\1', s)
            elif i == 2:
                s = s.replace("'", '"')
            elif i == 3:
                if not s.strip().startswith('['):
                    s = re.sub(r'(\w+)(\s*:)', r'"\1"\2', s)
        return s

    attempts: list[str] = [
        fix1,
        fix2,
        fix3,
        _apply_combo(raw, 1, 2),
        _apply_combo(raw, 1, 3),
        _apply_combo(raw, 2, 3),
        _apply_combo(raw, 1, 2, 3),
    ]

    for attempt in attempts:
        if attempt == raw:
            continue
        try:
            return json.loads(attempt), True
        except (json.JSONDecodeError, ValueError):
            continue

    # Last resort: try the original
    return json.loads(raw), False


def _build_error_hint(name: str, exc: Exception) -> str:
    """Build a short self-correction hint for the LLM when a tool call fails.

    Includes the tool name, the parse/execution error, and the valid parameter
    names so the LLM can immediately retry with corrected arguments.
    """
    valid_params: list[str] = []
    for tool_def in TOOLS:
        if tool_def["function"]["name"] == name:
            props = tool_def["function"].get("parameters", {}).get("properties", {})
            required = tool_def["function"].get("parameters", {}).get("required", [])
            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "any")
                marker = " (required)" if pname in required else ""
                valid_params.append(f"{pname}: {ptype}{marker}")
            break

    hint_parts = [f"Tool '{name}' failed: {exc}"]
    if valid_params:
        hint_parts.append(f"Valid parameters: {', '.join(valid_params)}")
    hint_parts.append("Please fix your tool call arguments and retry.")
    return "\n".join(hint_parts)


def execute_tool(
    tool_call: dict,
    write_gate: WriteSafetyGate,
    read_gate: ReadSafetyGate,
    on_output: callable = None,
    approve_callback: callable = None,
) -> ToolResult:
    """Execute a single tool call.  All read/write paths go through safety gates.

    Read-only tools (read_file, file_info, etc.) are cached within a turn
    so repeated reads of the same file hit the cache instead of disk.

    If *on_output* is provided, it is called with (tool_name, line_str) for
    real-time output streaming (currently only run_shell uses this).

    Malformed JSON arguments are repaired automatically (trailing commas,
    single quotes, unquoted keys) before parsing.  On failure a *hint* is
    attached to the ToolResult so the LLM can self-correct.
    """
    fn = tool_call["function"]
    name = fn["name"]
    raw_args = fn["arguments"]
    try:
        args, _repaired = _repair_json(raw_args)
    except json.JSONDecodeError as exc:
        hint = _build_error_hint(name, exc)
        return ToolResult(
            success=False,
            content=f"Malformed JSON in tool arguments: {exc}",
            hint=hint,
        )

    # Check cache for read-only tools (skip if on_output is streaming)
    if on_output is None and name in _CACHEABLE:
        cache_key = json.dumps([name, args], sort_keys=True)
        if cache_key in _TOOL_CACHE:
            return _TOOL_CACHE[cache_key]

    # --- schema validation: check parameter names against tool definition ---
    if isinstance(args, dict):
        tool_schema = None
        for td in TOOLS:
            if td["function"]["name"] == name:
                tool_schema = td["function"].get("parameters", {})
                break
        if tool_schema:
            valid_params = set(tool_schema.get("properties", {}).keys())
            required_params = set(tool_schema.get("required", []))
            provided = set(args.keys())
            unknown = provided - valid_params
            missing = required_params - provided
            if unknown or missing:
                hint_parts = []
                if unknown:
                    hint_parts.append(
                        f"Unknown parameter(s): {', '.join(sorted(unknown))}")
                if missing:
                    hint_parts.append(
                        f"Missing required: {', '.join(sorted(missing))}")
                hint_parts.append(
                    f"Valid parameters: {', '.join(sorted(valid_params))}")
                return ToolResult(
                    success=False,
                    content=f"Invalid arguments: {'; '.join(hint_parts[:2])}",
                    hint="\n".join(hint_parts),
                )

    dispatch = _TOOL_DISPATCH.get(name)
    if dispatch is None:
        known = sorted(td["function"]["name"] for td in TOOLS)
        return ToolResult(
            success=False,
            content=f"Unknown tool: {name}",
            hint=f"Tool '{name}' is not recognized. Available tools: {', '.join(known)}. Please use one of these.",
        )

    # Approval gate for write/destructive tools
    if approve_callback is not None and name in ("write_file", "edit_file", "run_shell"):
        if not approve_callback(name, args):
            return ToolResult(
                success=False,
                content=f"{name} not approved by user.",
                hint=f"Tool '{name}' requires user approval and was denied. Consider an alternative approach or ask the user to approve.",
            )

    # Pass on_output to the tool if it accepts it
    import inspect
    sig = inspect.signature(dispatch)
    if "on_output" in sig.parameters:
        result = dispatch(args, write_gate, read_gate, on_output=on_output)
    else:
        result = dispatch(args, write_gate, read_gate)

    # Cache successful read-only results (only when not streaming)
    if on_output is None and name in _CACHEABLE and result.success:
        cache_key = json.dumps([name, args], sort_keys=True)
        _TOOL_CACHE[cache_key] = result

    return result


def tool_summary(tc: dict) -> str:
    """Return a compact one-line summary of a tool call for display."""
    fn = tc["function"]
    name = fn["name"]
    try:
        args = json.loads(fn["arguments"])
    except Exception:
        args = {}

    summarize = _TOOL_SUMMARIES.get(name)
    if summarize is None:
        return f"{name}(…)"
    return summarize(args)


# ---------------------------------------------------------------------------
# Import submodules to trigger @_register / @_summarize side effects
# ---------------------------------------------------------------------------

from tools import file_ops    # noqa: E402, F401
from tools import shell_ops   # noqa: E402, F401
from tools import search_ops  # noqa: E402, F401
from tools.search_ops import build_symbol_index  # noqa: E402, F401
