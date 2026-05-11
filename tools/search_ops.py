#!/usr/bin/env python3
"""
search_ops.py — semantic search and web search tools for mini_agent.

Tools: semantic_search, web_search
"""

import os

from safety import ReadSafetyGate, WriteSafetyGate
from tools import _register, _summarize, ToolResult, _TOOL_CONTEXT
from tools.shell_ops import _SKIP_DIRS


# ---------------------------------------------------------------------------
# symbol_index — fast workspace symbol lookup
# ---------------------------------------------------------------------------

_SYMBOL_INDEX: dict[str, list[dict]] | None = None  # name → [{"path","line","kind"}, ...]


def build_symbol_index(root: str) -> dict[str, list[dict]]:
    """Scan workspace .py files for def/class lines.  Fast — no parsing, just regex.

    Returns {name: [{"path":..., "line":..., "kind":"def"|"class"}, ...]}.
    The index is cached in _SYMBOL_INDEX and reused until rebuild_symbol_index is called.
    """
    global _SYMBOL_INDEX
    import re
    pattern = re.compile(r"^\s*(def|class)\s+(\w+)")

    idx: dict[str, list[dict]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, "r") as f:
                    for lineno, line in enumerate(f, 1):
                        m = pattern.match(line)
                        if m:
                            kind, name = m.group(1), m.group(2)
                            idx.setdefault(name, []).append({
                                "path": fpath,
                                "line": lineno,
                                "kind": kind,
                            })
            except (OSError, PermissionError):
                continue

    _SYMBOL_INDEX = idx
    return idx


def _get_symbol_index(root: str) -> dict[str, list[dict]]:
    """Return the symbol index, building it lazily if needed."""
    global _SYMBOL_INDEX
    if _SYMBOL_INDEX is None:
        return build_symbol_index(root)
    return _SYMBOL_INDEX


@_register("find_symbol")
def _find_symbol(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    """Find where a Python symbol (function, class, method) is defined in the workspace."""
    import re
    name = args.get("name", "")
    if not name:
        return ToolResult(success=False, content="Missing required parameter: 'name'.")

    root = rg.workspace_root
    idx = _get_symbol_index(root)

    # Exact match first, then substring
    if name in idx:
        matches = [(name, entries) for name, entries in [(name, idx[name])]]
    else:
        # Substring search — case-insensitive
        matches = []
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        for key, entries in idx.items():
            if pattern.search(key):
                matches.append((key, entries))

    if not matches:
        return ToolResult(
            success=True,
            content=f"No symbols matching '{name}' found in workspace.",
        )

    lines: list[str] = []
    for sym_name, entries in matches[:20]:
        for e in entries[:5]:
            lines.append(f"  {e['kind']:5s}  {sym_name}  →  {e['path']}:{e['line']}")

    prefix = f"Found {sum(len(entries) for _, entries in matches)} location(s) for '{name}':"
    return ToolResult(success=True, content=prefix + "\n" + "\n".join(lines))


@_summarize("find_symbol")
def _find_symbol_summary(args: dict) -> str:
    return f"find_symbol({args.get('name', '?')})"


# ---------------------------------------------------------------------------
# semantic_search (sentence-transformers, local)
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


@_summarize("semantic_search")
def _semantic_search_summary(args: dict) -> str:
    query = args.get("query", "?")
    preview = query[:60]
    if len(query) > 60:
        preview += "…"
    return f"semantic_search({preview})"


# ---------------------------------------------------------------------------
# web_search (Exa)
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


@_summarize("web_search")
def _web_search_summary(args: dict) -> str:
    query = args.get("query", "?")
    preview = query[:60]
    if len(query) > 60:
        preview += "…"
    return f"web_search({preview})"
