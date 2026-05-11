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

    Also builds the reference index (_REF_INDEX) in the same pass — no
    second file walk needed.  Both indices are cached in memory.

    Returns {name: [{"path":..., "line":..., "kind":"def"|"class"}, ...]}.
    The index is cached and reused until rebuild_symbol_index is called.
    """
    global _SYMBOL_INDEX, _REF_INDEX
    import re
    import json as _json

    # --- disk cache: avoid re-scanning on every session ---
    cache_path = os.path.join(root, ".mini_agent_index.json")
    try:
        if os.path.exists(cache_path):
            cache_mtime = os.path.getmtime(cache_path)
            # Check if cache is newer than all .py files
            needs_rebuild = False
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
                for fname in filenames:
                    if fname.endswith(".py"):
                        fpath = os.path.join(dirpath, fname)
                        try:
                            if os.path.getmtime(fpath) > cache_mtime:
                                needs_rebuild = True
                                break
                        except OSError:
                            pass
                if needs_rebuild:
                    break
            if not needs_rebuild:
                cached = _json.loads(open(cache_path).read())
                sym = {k: v for k, v in cached.get("symbols", {}).items()}
                ref = {k: v for k, v in cached.get("references", {}).items()}
                _SYMBOL_INDEX = sym
                _REF_INDEX = ref
                return sym
    except Exception:
        pass  # any failure → fall through to rebuild
    def_pat = re.compile(r"^\s*(def|class)\s+(\w+)")
    word_pat = re.compile(r"\b(\w+)\b")

    # Names we never track as references (builtins, common patterns, etc.)
    _SKIP_REF_NAMES = frozenset({
        "self", "cls", "True", "False", "None", "int", "str", "list", "dict",
        "set", "tuple", "bool", "float", "bytes", "type", "object", "super",
        "range", "len", "print", "isinstance", "hasattr", "getattr", "setattr",
        "enumerate", "zip", "map", "filter", "iter", "next", "any", "all",
        "sorted", "reversed", "min", "max", "sum", "abs", "round", "ord", "chr",
        "open", "Exception", "ValueError", "TypeError", "KeyError", "OSError",
        "RuntimeError", "ImportError", "AttributeError", "StopIteration",
        "__init__", "__name__", "__main__", "__file__", "__doc__",
        "unittest", "TestCase", "json", "os", "sys", "re", "time",
    })

    symbol_idx: dict[str, list[dict]] = {}
    ref_idx: dict[str, list[dict]] = {}

    # 1. First pass: collect all symbol definitions
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, "r") as f:
                    for lineno, line in enumerate(f, 1):
                        m = def_pat.match(line)
                        if m:
                            kind, name = m.group(1), m.group(2)
                            symbol_idx.setdefault(name, []).append({
                                "path": fpath,
                                "line": lineno,
                                "kind": kind,
                            })
            except (OSError, PermissionError):
                continue

    known_names = set(symbol_idx.keys())

    # 2. Second pass: collect references (only for known symbol names)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, "r") as f:
                    for lineno, line in enumerate(f, 1):
                        stripped = line.strip()
                        for match in word_pat.finditer(line):
                            word = match.group(1)
                            if word in _SKIP_REF_NAMES or word not in known_names:
                                continue
                            ref_idx.setdefault(word, []).append({
                                "path": fpath,
                                "line": lineno,
                                "context": stripped[:120],
                            })
            except (OSError, PermissionError):
                continue

    # Deduplicate references per file+line
    for name in ref_idx:
        seen = set()
        unique = []
        for ref in ref_idx[name]:
            key = (ref["path"], ref["line"])
            if key not in seen:
                seen.add(key)
                unique.append(ref)
        ref_idx[name] = unique

    _SYMBOL_INDEX = symbol_idx
    _REF_INDEX = ref_idx

    # Persist to disk cache
    try:
        cache_path = os.path.join(root, ".mini_agent_index.json")
        with open(cache_path, "w") as f:
            _json.dump({"symbols": symbol_idx, "references": ref_idx}, f)
    except Exception:
        pass

    return symbol_idx


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


# ---------------------------------------------------------------------------
# find_usages — cross-reference lookup
# ---------------------------------------------------------------------------

# Reverse index: for each symbol name, all lines where it's referenced
# (as a bare word in Python source).  Built lazily.
_REF_INDEX: dict[str, list[dict]] | None = None


def build_ref_index(root: str) -> dict[str, list[dict]]:
    """Scan .py files for symbol references. Reuses the forward index keys."""
    global _REF_INDEX
    import re

    # Get the forward index to know which symbols to look for
    from tools.search_ops import _get_symbol_index
    fwd = _get_symbol_index(root)
    if not fwd:
        _REF_INDEX = {}
        return {}

    # Build a set of all known symbol names
    known_names = set(fwd.keys())
    # Also include common builtins we don't need to track
    skip_names = {
        "self", "cls", "True", "False", "None", "int", "str", "list", "dict",
        "set", "tuple", "bool", "float", "bytes", "type", "object", "super",
        "range", "len", "print", "isinstance", "hasattr", "getattr", "setattr",
        "enumerate", "zip", "map", "filter", "iter", "next", "any", "all",
        "sorted", "reversed", "min", "max", "sum", "abs", "round", "ord", "chr",
        "open", "Exception", "ValueError", "TypeError", "KeyError", "OSError",
        "RuntimeError", "ImportError", "AttributeError", "StopIteration",
        "__init__", "__name__", "__main__", "__file__", "__doc__",
        "unittest", "TestCase", "json", "os", "sys", "re", "time",
    }
    ref_idx: dict[str, list[dict]] = {}

    word_pat = re.compile(r"\b(\w+)\b")

    for dirpath, dirnames, filenames in os.walk(root):
        from tools.shell_ops import _SKIP_DIRS
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, "r") as f:
                    lines = f.readlines()
            except (OSError, PermissionError):
                continue

            for lineno, line in enumerate(lines, 1):
                # Skip def/class lines (those are definitions, not usages)
                stripped = line.strip()
                if stripped.startswith(("def ", "class ", "import ", "from ")):
                    # Still check for inline usages like: from x import Foo
                    # Simple approach: skip pure def/class declarations
                    pass

                for match in word_pat.finditer(line):
                    word = match.group(1)
                    if word in skip_names:
                        continue
                    if word in known_names:
                        ref_idx.setdefault(word, []).append({
                            "path": fpath,
                            "line": lineno,
                            "context": stripped[:120],
                        })

    # Deduplicate per path+line (a word might appear twice on same line)
    for name in ref_idx:
        seen = set()
        unique = []
        for ref in ref_idx[name]:
            key = (ref["path"], ref["line"])
            if key not in seen:
                seen.add(key)
                unique.append(ref)
        ref_idx[name] = unique

    _REF_INDEX = ref_idx
    return ref_idx


def _get_ref_index(root: str) -> dict[str, list[dict]]:
    """Return the reference index, building it lazily."""
    global _REF_INDEX
    if _REF_INDEX is None:
        return build_ref_index(root)
    return _REF_INDEX


@_register("find_usages")
def _find_usages(args: dict, _wg: WriteSafetyGate, rg: ReadSafetyGate) -> ToolResult:
    """Find all usages (references) of a Python symbol in the workspace."""
    import re
    name = args.get("name", "")
    if not name:
        return ToolResult(success=False, content="Missing required parameter: 'name'.")

    root = rg.workspace_root
    ref_idx = _get_ref_index(root)

    if not ref_idx:
        return ToolResult(
            success=True,
            content=f"Reference index not yet built. Try find_symbol first to populate the forward index.",
        )

    # Exact match first, then substring
    if name in ref_idx:
        matches = ref_idx[name]
    else:
        # Substring search
        matches = []
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        for key, refs in ref_idx.items():
            if pattern.search(key):
                matches.extend(refs)

    if not matches:
        # Fall back to grep-based search
        import subprocess
        from tools.shell_ops import _SKIP_DIRS
        try:
            exclude = " ".join(f"--exclude-dir={d}" for d in _SKIP_DIRS)
            cmd = f"grep -rn --include='*.py' {exclude} -w '{name}' {root}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            if result.stdout.strip():
                lines_out = result.stdout.strip().split("\n")[:30]
                return ToolResult(
                    success=True,
                    content=f"Found usages of '{name}' (grep fallback):\n" + "\n".join(f"  {l}" for l in lines_out),
                )
        except Exception:
            pass
        return ToolResult(
            success=True,
            content=f"No usages found for '{name}' in workspace.",
        )

    # Limit output
    shown = matches[:30]
    lines: list[str] = [f"Found {len(matches)} usage(s) of '{name}':"]
    for ref in shown:
        lines.append(f"  {ref['path']}:{ref['line']}  {ref['context']}")

    if len(matches) > 30:
        lines.append(f"  … and {len(matches) - 30} more")

    return ToolResult(success=True, content="\n".join(lines))


@_summarize("find_usages")
def _find_usages_summary(args: dict) -> str:
    return f"find_usages({args.get('name', '?')})"
