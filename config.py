#!/usr/bin/env python3
"""
config.py — project-level configuration for mini_agent.

Looks for ``.mini_agent.toml`` in the workspace root and merges settings
with env vars and CLI flags.  Priority: CLI > env var > config file > default.
"""

import os
import sys
from dataclasses import dataclass, field

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # Python < 3.11


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_FILENAME = ".mini_agent.toml"
MEMORY_FILENAME = ".mini_agent_memory.db"

DEFAULT_MODEL        = "deepseek-v4-pro"
DEFAULT_API_URL      = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_API_KEY      = ""  # set via DEEPSEEK_API_KEY env var or .mini_agent.toml
DEFAULT_MAX_MESSAGES = 500
DEFAULT_MAX_TOKENS   = 800_000
DEFAULT_EXA_API_KEY  = ""  # set via EXA_API_KEY env var or .mini_agent.toml


# ---------------------------------------------------------------------------
# Config object
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    """All configuration for a single agent session.

    Fields are populated in priority order:
    1. CLI flags (--workspace, --stream, --quiet)
    2. Environment variables (DEEPSEEK_API_KEY, AGENT_WORKSPACE)
    3. .mini_agent.toml file in workspace root
    4. Hard-coded defaults
    """

    model: str = DEFAULT_MODEL
    api_key: str = DEFAULT_API_KEY
    api_url: str = DEFAULT_API_URL
    workspace: str = ""
    allow_overwrites: bool = False
    stream: bool = False
    verbose: bool = True
    memory_filename: str = MEMORY_FILENAME
    max_messages: int = DEFAULT_MAX_MESSAGES
    max_tokens: int = DEFAULT_MAX_TOKENS
    exa_api_key: str = DEFAULT_EXA_API_KEY
    approve_write_ops: bool = False

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, workspace: str) -> "AgentConfig":
        """Build an AgentConfig from all sources.

        *workspace* is the already-resolved workspace root (from
        ``--workspace`` flag, ``AGENT_WORKSPACE`` env var, or cwd).
        """
        config = cls()

        # ---- 1.  TOML config file -------------------------------------------
        config_path = os.path.join(workspace, CONFIG_FILENAME)
        if os.path.isfile(config_path):
            try:
                with open(config_path, "rb") as f:
                    data = tomllib.load(f)
                agent_data = data.get("agent", {})
                _apply_toml(config, agent_data)
            except Exception as exc:
                print(f"Warning: failed to parse {config_path}: {exc}",
                      file=sys.stderr)

        # ---- 2.  Environment variables --------------------------------------
        if os.environ.get("DEEPSEEK_API_KEY"):
            config.api_key = os.environ["DEEPSEEK_API_KEY"]
        if os.environ.get("AGENT_WORKSPACE"):
            config.workspace = os.environ["AGENT_WORKSPACE"]
        if os.environ.get("EXA_API_KEY"):
            config.exa_api_key = os.environ["EXA_API_KEY"]

        # ---- 3.  CLI flags --------------------------------------------------
        if "--stream" in sys.argv:
            config.stream = True
        if "--quiet" in sys.argv:
            config.verbose = False
        if "--allow-overwrites" in sys.argv:
            config.allow_overwrites = True
        if "--approve" in sys.argv:
            config.approve_write_ops = True
        # --workspace is resolved before we get here; store it
        config.workspace = workspace

        return config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Keys recognised in TOML and their expected types
_TOML_SCHEMA: dict[str, type] = {
    "model": str,
    "api_key": str,
    "api_url": str,
    "allow_overwrites": bool,
    "stream": bool,
    "verbose": bool,
    "max_messages": int,
    "max_tokens": int,
    "exa_api_key": str,
    "approve_write_ops": bool,
}


def _apply_toml(config: AgentConfig, data: dict) -> None:
    """Apply recognised keys from TOML data onto *config* with type checking.

    Unknown keys are skipped.  Values with wrong types are warned and skipped.
    """
    for key, value in data.items():
        if key not in _TOML_SCHEMA:
            continue
        expected = _TOML_SCHEMA[key]
        if not isinstance(value, expected):
            print(
                f"Warning: .mini_agent.toml key '{key}' expected {expected.__name__}, "
                f"got {type(value).__name__} — skipping",
                file=sys.stderr,
            )
            continue
        setattr(config, key, value)


def build_startup_context(workspace: str) -> str:
    """Generate a one-shot system message describing the workspace at startup.

    Saves the agent discovery turns — no need to list_directory / read STATE.txt
    before getting to work.
    """
    import subprocess as _sp

    parts: list[str] = []
    parts.append("[WORKSPACE CONTEXT — injected once at session start]")

    # 1. File tree (skip hidden dirs, __pycache__, .git, venv, node_modules)
    SKIP = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache",
            ".pytest_cache", ".ruff_cache", "dist", "build", ".tox"}
    tree_lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP and not d.startswith("."))
        depth = dirpath[len(workspace):].count(os.sep)
        indent = "  " * depth
        label = os.path.basename(dirpath) or workspace.rstrip("/").rsplit("/", 1)[-1]
        tree_lines.append(f"{indent}[d] {label}/")
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            tree_lines.append(f"{indent}  [f] {fname}")
        if len(tree_lines) > 60:
            tree_lines.append(f"{indent}  ... (truncated)")
            break
    parts.append("```\n" + "\n".join(tree_lines) + "\n```")

    # 2. STATE.txt content (if it exists)
    state_path = os.path.join(workspace, "STATE.txt")
    if os.path.isfile(state_path):
        try:
            with open(state_path) as f:
                state_content = f.read()
            # Only include last ~50 lines to keep it brief
            state_lines = state_content.split("\n")
            if len(state_lines) > 50:
                state_content = "\n".join(state_lines[-50:])
                parts.append("\n## Latest STATE.txt (last 50 lines)\n" + state_content)
            else:
                parts.append("\n## STATE.txt\n" + state_content)
        except OSError:
            pass

    # 3. Recent git log (last 5 commits, if this is a git repo)
    try:
        r = _sp.run(["git", "-C", workspace, "log", "--oneline", "-5"],
                    capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            parts.append("\n## Recent git log\n```\n" + r.stdout.rstrip() + "\n```")
    except Exception:
        pass

    return "\n".join(parts) + "\n"


def resolve_workspace() -> str:
    """Resolve workspace root from CLI arg, env var, or default to cwd.

    Used by both the terminal REPL (mini_agent.py) and TUI (tui.py).
    """
    import sys as _sys
    args = _sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--workspace" and i + 1 < len(args):
            return args[i + 1]
    return os.environ.get("AGENT_WORKSPACE", os.getcwd())
