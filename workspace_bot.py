#!/usr/bin/env python3
"""
workspace_bot.py -- A Discord bot that knows everything about *this* workspace
(the mini_agent project itself).  Add it to a different Discord channel for
help with development, debugging, architecture questions, etc.

Usage:
  WORKSPACE_BOT_TOKEN=... python workspace_bot.py

The token can also be placed in mini_agent/.env as WORKSPACE_BOT_TOKEN.
"""

from __future__ import annotations

import os
import sys

# Ensure the mini_agent package is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from discord_bot import MiniAgentDiscordBot
except ModuleNotFoundError as e:
    print(f"[workspace_bot] FATAL: {e}")
    print("[workspace_bot] The 'discord' package is not installed.")
    print("[workspace_bot] Install it with: pip install discord.py")
    sys.exit(1)

from core.config import AgentConfig
from core.bootstrap import init_session
from voice_handler import VoiceHandler


def main() -> None:
    workspace = _HERE  # this project itself

    # --- Boot diagnostic log (written early so it survives even on crash) --
    import datetime as _dt

    _boot_log = os.path.join(workspace, "_bot_boot.log")
    _boot_lines = []

    def _flush_boot_log() -> None:
        try:
            with open(_boot_log, "a", encoding="utf-8") as f:
                f.write("\n".join(_boot_lines) + "\n")
        except Exception:
            pass

    def _blog(msg: str) -> None:
        """Log to both console and the boot log file."""
        print(msg, flush=True)
        _boot_lines.append(f"[{_dt.datetime.now().isoformat()}] {msg}")

    _blog(f"[workspace_bot] Boot start -- workspace={workspace}")
    _blog(
        f"[workspace_bot] Python={sys.version} | platform={sys.platform} | pid={os.getpid()}"
    )

    # Load .env first so WORKSPACE_BOT_TOKEN (and any API keys in the
    # mini_agent .env) are available before we bootstrap the session.
    from core.config import _load_dotenv

    _load_dotenv(workspace)
    _blog("[workspace_bot] .env loaded")

    # Dump relevant env vars for diagnostics
    _env_diag = {
        k: os.environ.get(k, "(unset)")
        for k in [
            "WORKSPACE_BOT_TOKEN",
            "DISCORD_BOT_TOKEN",
            "AGENT_WORKSPACE",
            "MINI_AGENT_UI",
            "DEEPSEEK_API_KEY",
        ]
        if os.environ.get(k)
    }
    _blog(
        f"[workspace_bot] Env: WORKSPACE_BOT_TOKEN={'SET' if os.environ.get('WORKSPACE_BOT_TOKEN') else 'MISSING'}, "
        f"DISCORD_BOT_TOKEN={'SET' if os.environ.get('DISCORD_BOT_TOKEN') else 'MISSING'}"
    )

    # Resolve token (must happen before init_session which may print a
    # confusing "no DISCORD_BOT_TOKEN" warning)
    token = os.environ.get("WORKSPACE_BOT_TOKEN", "")
    if not token:
        _blog("[workspace_bot] FATAL: WORKSPACE_BOT_TOKEN not set.")
        _blog("[workspace_bot] Add it to mini_agent/.env:")
        _blog("[workspace_bot]   echo 'WORKSPACE_BOT_TOKEN=...' >> .env")
        _blog("[workspace_bot] Or export it: export WORKSPACE_BOT_TOKEN=...")
        _flush_boot_log()
        sys.exit(1)
    _blog(f"[workspace_bot] Token found (len={len(token)})")

    # Bootstrap the agent session
    os.environ["MINI_AGENT_UI"] = "discord"
    try:
        session_data = init_session(workspace)
        config: AgentConfig = session_data["config"]
        write_gate = session_data["write_gate"]
        read_gate = session_data["read_gate"]
        memory = session_data["memory"]
        base_messages = session_data["messages"]
        _blog(
            f"[workspace_bot] Agent initialized (model={config.model}, provider={config.api_provider})"
        )
    except Exception as e:
        _blog(f"[workspace_bot] FATAL init_session: {e}")
        import traceback

        _blog(traceback.format_exc())
        _flush_boot_log()
        sys.exit(1)

    # Clear any stale sub-agent callback (system removed in 25d41eb)
    try:
        from tools import _TOOL_CONTEXT

        _TOOL_CONTEXT._subagent_callback = None
    except Exception:
        pass

    # --- Voice handler --------------------------------------------------
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY", "")
    voice = VoiceHandler(elevenlabs_api_key=elevenlabs_key)
    if elevenlabs_key:
        _blog("[workspace_bot] Voice TTS: ElevenLabs")
    else:
        _blog("[workspace_bot] Voice TTS: macOS say (no ELEVENLABS_API_KEY set)")

    bot = MiniAgentDiscordBot(
        workspace=workspace,
        config=config,
        write_gate=write_gate,
        read_gate=read_gate,
        memory=memory,
        base_messages=base_messages,
        voice=voice,
    )

    _blog("[workspace_bot] Bot instance created, calling run()...")
    _flush_boot_log()  # flush before blocking call

    try:
        bot.run(token)
    except Exception as e:
        # Check for privileged intents error -- retry with basic intents
        if "PrivilegedIntentsRequired" in type(e).__name__:
            _blog(
                "[workspace_bot] Privileged intents not enabled in Discord Developer Portal."
            )
            _blog(
                "[workspace_bot] Disabling privileged intents (members, presences, message_content) and retrying..."
            )
            import discord_bot

            discord_bot.INTENTS.members = False
            discord_bot.INTENTS.presences = False
            discord_bot.INTENTS.message_content = False
            # Recreate bot with downgraded intents
            bot = MiniAgentDiscordBot(
                workspace=workspace,
                config=config,
                write_gate=write_gate,
                read_gate=read_gate,
                memory=memory,
                base_messages=base_messages,
                voice=voice,
            )
            _blog("[workspace_bot] Retrying with basic intents only...")
            _flush_boot_log()
            bot.run(token)
            return  # skip the except/finally below
        _blog(f"[workspace_bot] FATAL bot.run: {e}")
        import traceback

        _blog(traceback.format_exc())
        _flush_boot_log()
        sys.exit(1)
    finally:
        try:
            memory.close()
        except Exception:
            pass
        _blog("[workspace_bot] Done.")
        _flush_boot_log()


if __name__ == "__main__":
    main()
