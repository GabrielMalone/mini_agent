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
DEFAULT_API_KEY      = "sk-df0ebdf0572a4485bd4e89996c9aa710"
DEFAULT_MAX_MESSAGES = 500
DEFAULT_MAX_TOKENS   = 800_000
DEFAULT_EXA_API_KEY  = "4346b9ff-217d-4d42-8cee-f0e74117d188"


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
