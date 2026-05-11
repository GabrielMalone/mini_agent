#!/usr/bin/env python3
"""
shell_ops.py — shell, search, test, and git tools for mini_agent.

Tools: run_shell, search_files, run_tests, git
"""

import os
import subprocess

from safety import ReadSafetyGate, WriteSafetyGate
from tools import _register, _summarize, ToolResult


# ---------------------------------------------------------------------------
# run_shell
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------

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


@_summarize("search_files")
def _search_files_summary(args: dict) -> str:
    pattern = args.get("pattern", "?")
    p = args.get("path", ".")
    return f"search_files('{pattern}', {p})"


# ---------------------------------------------------------------------------
# run_tests
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


@_summarize("run_tests")
def _run_tests_summary(args: dict) -> str:
    target = args.get("path", "").strip()
    if target:
        return f"run_tests({target})"
    return "run_tests(all)"


# ---------------------------------------------------------------------------
# git
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


@_summarize("git")
def _git_summary(args: dict) -> str:
    sub = args.get("subcommand", "?")
    extra = args.get("args", "")
    if extra:
        return f"git {sub} {extra}"
    return f"git {sub}"
