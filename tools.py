#!/usr/bin/env python3
"""
tools.py — tool definitions, execution, and structured results for mini_agent.

Every tool execution returns a ToolResult (never a raw exception).
All read and write paths route through the safety gates.
Shell commands and searches run sandboxed inside the workspace root.

Adding a new tool requires:
    1. A ``_<name>`` implementation function decorated with ``@_register("name")``.
    2. A ``_<name>_summary`` function decorated with ``@_summarize("name")``.
    3. An entry in ``TOOLS`` (the API schema sent to the LLM).
"""

import json
import os
import stat as stat_module
import subprocess
import time

from safety import ReadSafetyGate, WriteSafetyGate


# ---------------------------------------------------------------------------
# Tool definitions (API schema sent to the LLM)
# ---------------------------------------------------------------------------

TOOLS = [
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
                        "description": "Path to the file to read",
                    }
                },
                "required": ["path"],
            },
        },
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
                        "description": "Path to the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Edit a file by replacing a specific string with another. "
                "Replaces the first occurrence of old_string with new_string. "
                "Returns an error if old_string is not found in the file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact string to find and replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "String to replace it with",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
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
                        "description": "Path to the directory to list",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Run a shell command inside the workspace directory. "
                "Returns exit code, stdout, and stderr. "
                "Commands time out after 60 seconds. "
                "Use this to run tests, check syntax, invoke build tools, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute (e.g. 'python -m pytest test_safety.py -v')",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Bypass the destructive-command guard. Default: false. Required for rm, mkfs, etc.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Search for a text pattern recursively in files within the workspace. "
                "Returns matching lines with file path and line number. "
                "Skips hidden directories, binary files, and common VCS/venv dirs. "
                "Capped at 50 results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text or substring to search for (case-sensitive by default). If regex is true, treated as a Python regex.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (defaults to workspace root)",
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "If true, treat pattern as a Python regex. Default: false.",
                    },
                    "ignore_case": {
                        "type": "boolean",
                        "description": "If true, case-insensitive search. Default: false.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_info",
            "description": (
                "Get metadata about a file or directory at the given path. "
                "Returns size, permissions, modification time, and type (file/directory). "
                "Also reports whether the path exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file or directory to inspect",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": (
                "Run tests in the workspace. Returns structured pass/fail counts "
                "and failure details. Use after every code change to verify nothing "
                "broke. If 'path' is given, runs only those tests; otherwise runs all."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional: specific test file or directory to run (e.g. 'test_tools.py' or 'test_memory.py'). If omitted, runs all tests.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": (
                "Search code by meaning using embeddings. Finds code chunks semantically "
                "similar to the query, even if they don't share keywords. Good for finding "
                "related functionality, similar patterns, or code that 'feels like' something. "
                "Indexes files live — no pre-indexing needed. Returns top 10 matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of what to find (e.g. 'error handling around file writes', 'retry logic')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (defaults to workspace root)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web using Exa. Returns relevant pages with titles, URLs, "
                "and highlighted excerpts. Good for documentation lookup, API references, "
                "current information, and technical questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query. Be specific and use technical terms for best results.",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5, max 20).",
                    },
                    "search_type": {
                        "type": "string",
                        "description": "Search depth: 'auto' (default, balanced), 'fast', 'deep'. 'auto' works for most queries.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git",
            "description": (
                "Run a git command in the workspace. "
                "Supports: status, diff, log, init, add, commit, show, restore. "
                "All operations are local-only (no push/pull). "
                "Use 'diff' to see unstaged changes, 'status' to see file states, "
                "'log' for recent commits, 'init' to initialize a repo, "
                "'add' to stage files, 'commit' to commit staged changes, "
                "'show' to read a committed version of a file, "
                "'restore' to recover a file from the last commit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subcommand": {
                        "type": "string",
                        "description": "Git subcommand: status, diff, log, init, add, or commit",
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional arguments: file paths for 'add', commit message for 'commit', etc.",
                    },
                },
                "required": ["subcommand"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Structured tool result
# ---------------------------------------------------------------------------

class ToolResult:
    """Structured result from a tool execution — never a raw exception."""

    def __init__(self, success: bool, content: str) -> None:
        self.success = success
        self.content = content

    def to_dict(self) -> dict:
        return {"success": self.success, "content": self.content}

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ---------------------------------------------------------------------------
# Tool dispatch registry
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, callable] = {}
_TOOL_SUMMARIES: dict[str, callable] = {}
_TOOL_CONTEXT: dict = {}


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


def execute_tool(tool_call: dict, write_gate: WriteSafetyGate, read_gate: ReadSafetyGate) -> ToolResult:
    """Execute a single tool call.  All read/write paths go through safety gates."""
    fn = tool_call["function"]
    name = fn["name"]
    args = json.loads(fn["arguments"])

    dispatch = _TOOL_DISPATCH.get(name)
    if dispatch is None:
        return ToolResult(success=False, content=f"Unknown tool: {name}")

    return dispatch(args, write_gate, read_gate)


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
# Tool implementations  (@_register decorator only)
# ---------------------------------------------------------------------------

@_register("read_file")
def _read_file(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Read blocked by safety layer: {safety_result.reason}",
        )
    try:
        with open(safety_result.resolved_path, "r") as f:
            content = f.read()
        return ToolResult(success=True, content=content)
    except Exception as e:
        return ToolResult(success=False, content=f"Error reading '{safety_result.resolved_path}': {e}")


@_register("write_file")
def _write_file(args: dict, wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    content = args["content"]
    safety_result = wg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Write blocked by safety layer: {safety_result.reason}",
        )
    try:
        parent = os.path.dirname(safety_result.resolved_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(safety_result.resolved_path, "w") as f:
            f.write(content)
        return ToolResult(
            success=True,
            content=f"OK: wrote {len(content)} bytes to {safety_result.resolved_path}",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f"Error writing '{safety_result.resolved_path}': {e}",
        )


@_register("edit_file")
def _edit_file(args: dict, wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    old = args["old_string"]
    new = args["new_string"]
    safety_result = wg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Edit blocked by safety layer: {safety_result.reason}",
        )
    try:
        with open(safety_result.resolved_path, "r") as f:
            original = f.read()
        if old not in original:
            return ToolResult(
                success=False,
                content=f"Edit failed: old_string not found in '{safety_result.resolved_path}'",
            )
        updated = original.replace(old, new, 1)
        with open(safety_result.resolved_path, "w") as f:
            f.write(updated)
        return ToolResult(
            success=True,
            content=f"OK: replaced 1 occurrence in {safety_result.resolved_path}",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f"Error editing '{safety_result.resolved_path}': {e}",
        )


@_register("list_directory")
def _list_directory(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"List blocked by safety layer: {safety_result.reason}",
        )
    try:
        entries = os.listdir(safety_result.resolved_path)
        rows: list[str] = []
        for name in sorted(entries):
            full = os.path.join(safety_result.resolved_path, name)
            prefix = "d" if os.path.isdir(full) else "f"
            rows.append(f"  [{prefix}] {name}")
        if not rows:
            content = f"{safety_result.resolved_path}  (empty)"
        else:
            content = f"{safety_result.resolved_path}\n" + "\n".join(rows)
        return ToolResult(success=True, content=content)
    except Exception as e:
        return ToolResult(success=False, content=f"Error listing '{safety_result.resolved_path}': {e}")


# Patterns that indicate destructive commands.  Guarded unless force=True.
_DESTRUCTIVE_PATTERNS = [
    r"\brm\b",             # remove
    r"\brmdir\b",          # remove directory
    r"\bdd\b",             # disk destroyer
    r"\bmkfs\b",           # make filesystem
    r"\bmkswap\b",         # make swap
    r"\bchmod\s+777\b",    # world-writable
    r"\bchown\b",          # change owner
    r">.*/dev/",            # write directly to device
    r"\bformat\b",         # format disk
    r"\bwiped\b",          # wipe
    r"\bwipefs\b",         # wipe filesystem
    r"\bparted\b",         # partition editor
    r"\bfdisk\b",          # partition table
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # fork bomb
    r">/dev/null\s*&&\s*rm\b",  # rm disguised after suppression
]


def _check_destructive(command: str) -> str | None:
    """Return a warning string if the command looks destructive, else None."""
    import re
    for pat in _DESTRUCTIVE_PATTERNS:
        if re.search(pat, command):
            return (
                f"Command blocked by safety guard (matches destructive pattern '{pat}'). "
                f"Use force=True to bypass, or rephrase to use only safe operations."
            )
    return None


@_register("run_shell")
def _run_shell(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    command = args["command"]
    force = args.get("force", False)
    if not force:
        block = _check_destructive(command)
        if block is not None:
            return ToolResult(success=False, content=block)
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=rg.workspace_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        parts = [f"exit_code={result.returncode}"]
        if result.stdout:
            parts.append(f"stdout:\n{result.stdout.rstrip()}")
        if result.stderr:
            parts.append(f"stderr:\n{result.stderr.rstrip()}")
        return ToolResult(
            success=result.returncode == 0,
            content="\n".join(parts),
        )
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, content="Command timed out after 60s")
    except Exception as e:
        return ToolResult(success=False, content=f"Error running command: {e}")


# Directories skipped during recursive search
_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache",
              "venv", ".venv", "node_modules", ".mypy_cache", ".tox",
              "dist", "build", ".eggs"}


@_register("search_files")
def _search_files(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    pattern = args["pattern"]
    path = args.get("path", ".")
    use_regex = args.get("regex", False)
    ignore_case = args.get("ignore_case", False)
    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"Search blocked by safety layer: {safety_result.reason}",
        )

    if use_regex:
        import re
        flags = re.IGNORECASE if ignore_case else 0
        try:
            compiled = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(success=False, content=f"Invalid regex: {e}")
        match_fn = lambda line: compiled.search(line) is not None
    elif ignore_case:
        lower_pattern = pattern.lower()
        match_fn = lambda line: lower_pattern in line.lower()
    else:
        match_fn = lambda line: pattern in line

    results: list[str] = []
    try:
        for root, dirs, files in os.walk(safety_result.resolved_path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            for fname in sorted(files):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if match_fn(line):
                                results.append(f"{fpath}:{lineno}: {line.rstrip()}")
                                if len(results) >= 50:
                                    break
                except (OSError, PermissionError):
                    continue
                if len(results) >= 50:
                    break
            if len(results) >= 50:
                break
    except Exception as e:
        return ToolResult(success=False, content=f"Error searching: {e}")

    if not results:
        return ToolResult(
            success=True,
            content=f"No matches for '{pattern}' in {safety_result.resolved_path}",
        )
    output = "\n".join(results)
    if len(results) >= 50:
        output += "\n… (capped at 50 results)"
    return ToolResult(success=True, content=output)


@_register("file_info")
def _file_info(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    path = args["path"]
    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(
            success=False,
            content=f"File info blocked by safety layer: {safety_result.reason}",
        )
    resolved = safety_result.resolved_path
    try:
        st = os.stat(resolved)
        parts = [
            f"path: {resolved}",
            f"size: {st.st_size} bytes",
            f"mode: {stat_module.filemode(st.st_mode)}",
            f"modified: {time.ctime(st.st_mtime)}",
        ]
        if stat_module.S_ISDIR(st.st_mode):
            parts.append("type: directory")
        else:
            parts.append("type: file")
        return ToolResult(success=True, content="\n".join(parts))
    except FileNotFoundError:
        return ToolResult(success=True, content=f"path: {resolved}\nexists: no")
    except Exception as e:
        return ToolResult(success=False, content=f"Error stating '{resolved}': {e}")


# ---------------------------------------------------------------------------
# semantic_search tool (sentence-transformers, local)
# ---------------------------------------------------------------------------

_SEMANTIC_STORE: dict[str, tuple[float, list[tuple[int, int, str, "numpy.ndarray"]]]] = {}
_SEM_MODEL = None


def _sem_get_model():
    global _SEM_MODEL
    if _SEM_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _SEM_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _SEM_MODEL


def _sem_chunk_py(filepath: str) -> list[tuple[int, int, str]]:
    """Chunk a .py file at def/class boundaries. Returns (start_line, end_line, text)."""
    try:
        with open(filepath, "r") as f:
            lines = f.readlines()
    except (OSError, PermissionError):
        return []

    boundaries = [i for i, ln in enumerate(lines) if ln.strip().startswith(("def ", "class "))]
    if not boundaries:
        text = "".join(lines).strip()
        return [(1, len(lines), text)] if text else []

    chunks: list[tuple[int, int, str]] = []
    for j, start in enumerate(boundaries):
        end = boundaries[j + 1] if j + 1 < len(boundaries) else len(lines)
        text = "".join(lines[start:end]).strip()
        if text:
            chunks.append((start + 1, end, text))
    return chunks


def _sem_index(root: str) -> None:
    """Build/update in-memory index of .py files."""
    current = set()
    import numpy as np

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            current.add(fpath)
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            if fpath in _SEMANTIC_STORE and _SEMANTIC_STORE[fpath][0] == mtime:
                continue
            chunks = _sem_chunk_py(fpath)
            if not chunks:
                _SEMANTIC_STORE[fpath] = (mtime, [])
                continue
            texts = [t for _, _, t in chunks]
            model = _sem_get_model()
            embeddings = model.encode(texts, show_progress_bar=False)
            _SEMANTIC_STORE[fpath] = (mtime, list(zip(
                [s for s, e, _ in chunks],
                [e for s, e, _ in chunks],
                texts,
                list(embeddings),
            )))
    stale = [p for p in _SEMANTIC_STORE if p not in current]
    for p in stale:
        del _SEMANTIC_STORE[p]


@_register("semantic_search")
def _semantic_search(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    query = args.get("query", "")
    if not query:
        return ToolResult(success=False, content="Missing required parameter: 'query'.")
    path = args.get("path", ".")
    safety_result = rg.check(path)
    if not safety_result.allowed:
        return ToolResult(success=False, content=f"Search blocked by safety layer: {safety_result.reason}")

    import numpy as np

    root = safety_result.resolved_path
    _sem_index(root)

    model = _sem_get_model()
    query_emb = model.encode([query], show_progress_bar=False)[0]

    scored: list[tuple[float, str, int, int, str]] = []
    for fpath, (_, chunks) in _SEMANTIC_STORE.items():
        for start, end, text, emb in chunks:
            a = np.asarray(query_emb)
            b = np.asarray(emb)
            cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
            scored.append((cos, fpath, start, end, text))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:10]

    if not top:
        return ToolResult(success=True, content="No matches found.")

    lines: list[str] = []
    for cos, fpath, start, end, text in top:
        lines.append(f"score={cos:.3f}  {fpath}:{start}-{end}")
        snippet = text[:200].replace("\n", "\\n")
        if len(text) > 200:
            snippet += "…"
        lines.append(f"  {snippet}")

    return ToolResult(success=True, content="\n".join(lines))


# ---------------------------------------------------------------------------
# web_search tool (Exa)
# ---------------------------------------------------------------------------

@_register("web_search")
def _web_search(args: dict, _wg: WriteSafetyGate, _rg: ReadSafetyGate) -> ToolResult:
    query = args.get("query", "")
    if not query:
        return ToolResult(success=False, content="Missing required parameter: 'query'.")
    num = min(args.get("num_results", 5), 20)
    stype = args.get("search_type", "auto")
    api_key = _TOOL_CONTEXT.get("exa_api_key") or os.environ.get("EXA_API_KEY", "")

    if not api_key:
        return ToolResult(success=False, content="EXA_API_KEY not configured.")

    try:
        from exa_py import Exa
        exa = Exa(api_key=api_key)
        response = exa.search(
            query,
            type=stype,
            num_results=num,
            contents={"highlights": True},
        )
    except Exception as e:
        return ToolResult(success=False, content=f"Exa search error: {e}")

    if not response.results:
        return ToolResult(success=True, content="No results found.")

    lines: list[str] = []
    for i, r in enumerate(response.results, 1):
        lines.append(f"{i}. {r.title}")
        lines.append(f"   {r.url}")
        if r.highlights:
            for h in r.highlights[:3]:
                lines.append(f"   > {h.strip()}")
        lines.append("")

    return ToolResult(success=True, content="\n".join(lines).rstrip())


# ---------------------------------------------------------------------------
# Git tool
# ---------------------------------------------------------------------------

_GIT_SAFE: set[str] = {"status", "diff", "log", "init", "add", "commit", "show", "restore"}


def _git_run(cwd: str, *args: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


@_register("git")
def _git(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    sub = args["subcommand"]
    extra = args.get("args", "")

    if sub not in _GIT_SAFE:
        return ToolResult(
            success=False,
            content=f"Unknown or unsafe git subcommand: '{sub}'. "
                    f"Allowed: {', '.join(sorted(_GIT_SAFE))}",
        )

    cwd = rg.workspace_root

    if sub == "status":
        rc, out, err = _git_run(cwd, "status", "--short")
        if rc != 0:
            return ToolResult(success=False, content=err or out)
        if not out.strip():
            return ToolResult(success=True, content="Working tree clean.")
        return ToolResult(success=True, content=out.rstrip())

    elif sub == "diff":
        rc, out, err = _git_run(cwd, "diff")
        if rc != 0:
            return ToolResult(success=False, content=err or out)
        if not out.strip():
            return ToolResult(success=True, content="No unstaged changes.")
        return ToolResult(success=True, content=out.rstrip())

    elif sub == "log":
        rc, out, err = _git_run(
            cwd, "log", "--oneline", "-n", "20", "--decorate",
        )
        if rc != 0 and "does not have any commits" not in err:
            return ToolResult(success=False, content=err or out)
        if not out.strip():
            return ToolResult(success=True, content="No commits yet.")
        return ToolResult(success=True, content=out.rstrip())

    elif sub == "init":
        rc, out, err = _git_run(cwd, "init")
        if rc != 0:
            return ToolResult(success=False, content=err or out)
        return ToolResult(success=True, content=out.strip() or "Repository initialized.")

    elif sub == "add":
        paths = extra.strip() if extra.strip() else "."
        rc, out, err = _git_run(cwd, "add", *paths.split())
        if rc != 0:
            return ToolResult(success=False, content=err or out)
        return ToolResult(success=True, content=f"Staged: {paths}")

    elif sub == "commit":
        if not extra.strip():
            return ToolResult(success=False, content="Commit requires a message in 'args'.")
        rc, out, err = _git_run(cwd, "commit", "-m", extra.strip())
        if rc != 0:
            return ToolResult(success=False, content=err or out)
        return ToolResult(success=True, content=out.strip() or "Committed.")

    elif sub == "show":
        if not extra.strip():
            return ToolResult(success=False, content="'show' requires a file path in 'args'.")
        rc, out, err = _git_run(cwd, "show", f"HEAD:{extra.strip()}")
        if rc != 0:
            return ToolResult(success=False, content=err or out)
        return ToolResult(success=True, content=out)

    elif sub == "restore":
        if not extra.strip():
            extra = "."
        rc, changed, _ = _git_run(cwd, "diff", "--name-only", "HEAD")
        if rc != 0:
            return ToolResult(success=False, content=changed or "Unable to list changed files.")
        files = changed.strip()
        rc, out, err = _git_run(cwd, "restore", *extra.strip().split())
        if rc != 0:
            return ToolResult(success=False, content=err or out)
        if files:
            return ToolResult(success=True,
                              content=f"Restored all changes. Files restored:\n{files}")
        return ToolResult(success=True, content="Restored (no changes to revert).")


# ---------------------------------------------------------------------------
# run_tests tool
# ---------------------------------------------------------------------------

@_register("run_tests")
def _run_tests(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    target = args.get("path", "").strip()
    cmd = ["python", "-m", "pytest", "-q"]
    if target:
        cmd.append(target)

    try:
        result = subprocess.run(
            cmd,
            cwd=rg.workspace_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, content="Tests timed out after 120s")
    except Exception as e:
        return ToolResult(success=False, content=f"Error running tests: {e}")

    output = (result.stdout + result.stderr).strip()
    summary_line = ""
    lines = output.split("\n")
    for line in reversed(lines):
        if "passed" in line or "failed" in line or "error" in line:
            summary_line = line.strip()
            break

    if not summary_line:
        summary_line = f"exit_code={result.returncode}"

    success = result.returncode == 0

    if not success and len(output) > 500:
        output = "…\n" + output[-500:]

    return ToolResult(success=success, content=summary_line)


# ---------------------------------------------------------------------------
# Tool summary functions  (@_summarize decorator only)
# ---------------------------------------------------------------------------

@_summarize("read_file")
def _read_file_summary(args: dict) -> str:
    return f"read_file({args.get('path', '?')})"


@_summarize("write_file")
def _write_file_summary(args: dict) -> str:
    path = args.get("path", "?")
    content = args.get("content", "")
    preview = content[:60].replace("\n", "\\n")
    if len(content) > 60:
        preview += "…"
    return f"write_file({path}, {len(content)}B → \"{preview}\")"


@_summarize("edit_file")
def _edit_file_summary(args: dict) -> str:
    path = args.get("path", "?")
    old = args.get("old_string", "")
    preview = old[:40].replace("\n", "\\n")
    if len(old) > 40:
        preview += "…"
    return f"edit_file({path}, \"{preview}\")"


@_summarize("list_directory")
def _list_directory_summary(args: dict) -> str:
    return f"list_directory({args.get('path', '?')})"


@_summarize("run_shell")
def _run_shell_summary(args: dict) -> str:
    cmd = args.get("command", "?")
    preview = cmd[:80]
    if len(cmd) > 80:
        preview += "…"
    force = args.get("force", False)
    if force:
        return f"run_shell[force] ({preview})"
    return f"run_shell({preview})"


@_summarize("search_files")
def _search_files_summary(args: dict) -> str:
    pattern = args.get("pattern", "?")
    p = args.get("path", ".")
    return f"search_files('{pattern}', {p})"


@_summarize("file_info")
def _file_info_summary(args: dict) -> str:
    return f"file_info({args.get('path', '?')})"


@_summarize("git")
def _git_summary(args: dict) -> str:
    sub = args.get("subcommand", "?")
    extra = args.get("args", "")
    if extra:
        return f"git {sub} {extra}"
    return f"git {sub}"


@_summarize("run_tests")
def _run_tests_summary(args: dict) -> str:
    target = args.get("path", "").strip()
    if target:
        return f"run_tests({target})"
    return "run_tests(all)"


@_summarize("web_search")
def _web_search_summary(args: dict) -> str:
    query = args.get("query", "?")
    preview = query[:60]
    if len(query) > 60:
        preview += "…"
    return f"web_search({preview})"


@_summarize("semantic_search")
def _semantic_search_summary(args: dict) -> str:
    query = args.get("query", "?")
    preview = query[:60]
    if len(query) > 60:
        preview += "…"
    return f"semantic_search({preview})"
