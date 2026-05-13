# Agent Evaluation Harness — Design Document

## 1. Overview

The eval harness measures mini_agent's ability to complete real coding tasks
in a reproducible, automated way.  It is **not a benchmark platform** — it is a
development tool for the mini_agent project itself.  We run the agent against
curated tasks, measure how well it does, and track regressions as we change
prompts, tools, or the agent loop.

**Inspiration:** SWE-bench (Princeton, Oct 2023), SWE-bench Verified,
SWE-agent's evaluation pipeline.

**Differences from SWE-bench:**
- Single workspace (mini_agent's own repo), no Docker isolation.
- Tasks are small, single-file scoped changes — not multi-file PRs on large repos.
- No external repositories to clone; tasks operate on mini_agent itself or a
  minimal test fixture.
- Scoring is pluggable (file checks, test results, diff comparison) rather than
  solely test-suite-driven.
- Designed to be run as part of CI (`python -m eval.runner` or a pytest plugin).

---

## 2. Module Layout

```
eval/
├── __init__.py              # Public API: run_task(), run_suite(), EvalResult
├── runner.py                # Task runner — spawns mini_agent with a task
├── scorer.py                # Scoring — checks results against expected outcomes
├── metrics.py               # Metric collection — turns, tokens, tools, timing
├── tasks/                   # Task definitions (one YAML file per task)
│   ├── add_hello_world.yaml
│   ├── fix_off_by_one.yaml
│   └── write_tests_for_utils.yaml
├── fixtures/                # Pre-canned workspace fixtures (optional)
│   └── mini_repo/           # Minimal repo snapshot for isolated tasks
├── reports/                 # Output directory for run reports (JSON)
└── test_eval.py             # Tests for the eval harness itself
```

---

## 3. Task Format (YAML)

Each task is a YAML file in `eval/tasks/`.  The schema:

```yaml
# ---- Required ----
id: "add-hello-world"                  # kebab-case unique id
name: "Add hello_world to utils.py"    # Human-readable
description: |                          # Full task prompt given to the agent
  Add a function `hello_world(name: str = "World") -> str` to `utils.py`.
  The function should return the string `"Hello, {name}!"`.
  Add a docstring and type hints.

category: "feature"                    # feature | bugfix | test | refactor
difficulty: "easy"                     # easy | medium | hard

# ---- Environment ----
workspace_fixture: null                # null = use current workspace
                                       # "mini_repo" = copy eval/fixtures/mini_repo/

# ---- Scoring (at least one checker) ----
checks:
  - type: "file_exists"
    path: "utils.py"
  - type: "file_contains"
    path: "utils.py"
    pattern: "def hello_world"
  - type: "test_passes"
    path: "test_utils.py"              # pytest file to run (added by task or pre-existing)
  - type: "diff_contains"             # gold diff fragment must appear in agent's diff
    fragment: |
      +def hello_world(name: str = "World") -> str:

# ---- Optional ----
expected_tools:                        # Informational — tools we expect the agent to use
  - "write_file"
  - "edit_file"
  - "run_tests"
expected_turns_max: 12                 # Fails if agent exceeds this (anti-infinite-loop)
expected_files_touched:                # Informational — files we expect to be modified
  - "utils.py"
tags: ["functions", "basics"]          # Free-form tags for filtering
```

### Checker Types

| Checker | Parameters | Pass Condition |
|---------|-----------|----------------|
| `file_exists` | `path: str` | File exists after agent finishes |
| `file_not_exists` | `path: str` | File does not exist after agent finishes |
| `file_contains` | `path: str`, `pattern: str` (regex) | File content matches regex |
| `file_not_contains` | `path: str`, `pattern: str` (regex) | File content does NOT match regex |
| `test_passes` | `path: str` (pytest file) | Pytest exits 0 on that file |
| `diff_contains` | `fragment: str` | `git diff` output contains the fragment |
| `diff_not_contains` | `fragment: str` | `git diff` output does NOT contain fragment |
| `shell` | `command: str`, `expected_returncode: int = 0`, `expected_stdout: str` (optional regex) | Shell command exits with expected code and stdout matches |

All checks must pass for the task to be scored as **success**.  This is binary
scoring (like SWE-bench) — no partial credit at the task level.

---

## 4. Runner Design

### 4.1 Entry Point

```python
# eval/runner.py

def run_task(
    task: EvalTask,
    *,
    config: AgentConfig | None = None,
    timeout_seconds: int = 300,
    stream: bool = False,
) -> EvalResult:
    """Run a single eval task and return the result."""
```

### 4.2 Flow

```
1. Validate task YAML
2. Set up workspace:
   - If workspace_fixture is set, copy fixture to a temp directory
   - Otherwise, use a snapshot/copy of the current workspace
     (so the agent's changes don't pollute the real repo)
3. Initialize agent session (config.init_session)
4. Inject task prompt as the first user message
5. Run agent loop with instrumentation:
   - Count turns
   - Count tool calls (by name)
   - Estimate token usage (_total_tokens after each turn)
   - Track wall-clock time
   - Enforce timeout; cancel event + time limit
6. When agent finishes (or times out / exceeds expected_turns_max):
   - Run all checkers against the workspace
   - Collect git diff
7. Restore workspace (delete temp copy)
8. Return EvalResult
```

### 4.3 Instrumentation

The runner wraps `run_agent_turn()` with metric collection.  Two approaches:

**Approach A (non-invasive):** Hook into existing callbacks (`on_token`,
`on_tool_start`, `on_tool_end`).  The runner provides custom callbacks that
increment counters.

**Approach B (monkey-patch):** Wrap `execute_tool` and `call_deepseek` to
intercept calls and record metadata.

**Recommendation: Approach A** — it uses the existing callback surface area
and requires zero changes to core modules.  `on_tool_start(tool_name, args)`
already exists; we add a lightweight metrics collector that the callbacks push
into.

### 4.4 Agent Prompt Injection

The task description is injected as the first user message after session init:

```python
messages.append({"role": "user", "content": task.description})
```

The system prompt and startup context are still included normally — the agent
gets the full workspace awareness it normally has.

### 4.5 Timeout & Safety

- A `threading.Event` cancel event is set after `timeout_seconds`.
- If `expected_turns_max` is set and the agent exceeds it, the runner cancels.
- The runner sets `unrestricted=True` and `allow_overwrites=True` on eval
  config so safety gates don't block the agent during eval.

---

## 5. Scoring

### 5.1 Check Execution

```python
# eval/scorer.py

@dataclass
class CheckResult:
    check_type: str
    passed: bool
    detail: str          # Human-readable explanation

def run_checks(checks: list[dict], workspace: str) -> list[CheckResult]:
    """Run all checkers against the workspace.  Order is preserved."""
```

Each checker is a simple function:

```python
def check_file_exists(params: dict, workspace: str) -> CheckResult:
    path = os.path.join(workspace, params["path"])
    exists = os.path.isfile(path)
    return CheckResult("file_exists", exists, ...)

def check_file_contains(params: dict, workspace: str) -> CheckResult:
    path = os.path.join(workspace, params["path"])
    content = open(path).read()
    match = re.search(params["pattern"], content)
    return CheckResult("file_contains", bool(match), ...)

def check_test_passes(params: dict, workspace: str) -> CheckResult:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", params["path"], "-q"],
        cwd=workspace, capture_output=True, text=True, timeout=60,
    )
    return CheckResult("test_passes", result.returncode == 0, result.stdout)
```

### 5.2 Overall Result

```python
@dataclass
class EvalResult:
    task_id: str
    success: bool                    # All checks passed
    checks: list[CheckResult]        # Individual check results
    turns_used: int
    tool_calls: dict[str, int]       # tool_name → count
    tokens_consumed: int             # Estimated total tokens
    wall_time_seconds: float
    error: str | None                # Exception message if agent crashed
    diff: str                        # Full git diff of changes made
```

---

## 6. Metrics & Reporting

### 6.1 Per-Task Metrics (in `EvalResult`)

| Metric | Source | Notes |
|--------|--------|-------|
| `success` | scorer | Binary — all checks passed |
| `turns_used` | runner | Number of agent loop iterations |
| `tool_calls` | runner | Dict of tool_name → call count |
| `tokens_consumed` | runner | `_total_tokens(messages)` at end |
| `wall_time_seconds` | runner | `time.monotonic()` delta |
| `diff` | runner | `git diff` from temp workspace |

### 6.2 Suite-Level Metrics (aggregated across tasks)

```python
@dataclass
class SuiteReport:
    total: int
    passed: int
    failed: int
    errors: int                       # Agent crashed or timed out
    pass_rate: float                  # passed / total
    avg_turns: float
    avg_tokens: float
    avg_wall_time: float
    tool_usage: dict[str, int]        # Aggregated across all tasks
    per_task: list[EvalResult]
```

### 6.3 Report Output

Reports are written to `eval/reports/` as JSON:

```json
{
  "run_id": "2025-01-15T14:30:00Z",
  "suite": "all",
  "total": 5,
  "passed": 4,
  "failed": 1,
  "errors": 0,
  "pass_rate": 0.80,
  "avg_turns": 5.2,
  "avg_tokens": 12500,
  "avg_wall_time": 23.4,
  "tool_usage": {
    "read_file": 15,
    "write_file": 5,
    "edit_file": 3,
    "run_tests": 4
  },
  "tasks": [ ... ]
}
```

---

## 7. CLI & Integration

### 7.1 Command-Line Interface

```bash
# Run all tasks
python -m eval.runner

# Run a specific task
python -m eval.runner --task add-hello-world

# Run tasks matching tags
python -m eval.runner --tags basics,feature

# Run with difficulty filter
python -m eval.runner --difficulty easy

# Output report path
python -m eval.runner --output eval/reports/my_run.json

# Stream agent output during eval
python -m eval.runner --stream

# Timeout per task
python -m eval.runner --timeout 120
```

### 7.2 pytest Integration

```python
# eval/test_eval.py — runs all tasks as parameterized tests

import pytest
from eval.runner import run_task, load_tasks

@pytest.mark.parametrize("task", load_tasks())
def test_eval_task(task):
    result = run_task(task, timeout_seconds=120)
    assert result.success, (
        f"Task '{task.id}' failed.\n"
        + "\n".join(f"  {c.check_type}: {'PASS' if c.passed else 'FAIL'} — {c.detail}"
                    for c in result.checks)
    )
```

This makes eval tasks a first-class part of CI: `pytest eval/test_eval.py`.

### 7.3 `verify` Tool Integration

The existing `verify` tool runs lint + tests for modified files.  We could add
an `--eval` flag: `verify --eval` runs the eval suite on modified tasks.

---

## 8. Example Tasks

### 8.1 Add a Function

```yaml
id: "add-hello-world"
name: "Add hello_world to utils.py"
description: |
  Add a function `hello_world(name: str = "World") -> str` to a new file
  `utils.py` in the workspace root.
  The function should return `"Hello, {name}!"`.
  Include a docstring and type hints.
  After writing, run `python -c "from utils import hello_world; print(hello_world('Eval'))"`
  to verify it works.
category: "feature"
difficulty: "easy"
checks:
  - type: "file_exists"
    path: "utils.py"
  - type: "file_contains"
    path: "utils.py"
    pattern: "def hello_world"
  - type: "file_contains"
    path: "utils.py"
    pattern: "Hello, \\{name\\}"
  - type: "shell"
    command: "python -c 'from utils import hello_world; assert hello_world(\"Eval\") == \"Hello, Eval!\"'"
expected_tools:
  - "write_file"
  - "run_shell"
expected_turns_max: 10
tags: ["functions", "basics"]
```

### 8.2 Fix a Bug

```yaml
id: "fix-off-by-one"
name: "Fix off-by-one error in counter.py"
description: |
  The file `counter.py` contains a bug.  The function `count_to(n: int) -> list[int]`
  should return `[1, 2, ..., n]` but it returns `[1, 2, ..., n-1]`.
  Find the off-by-one error in `range(1, n)` and fix it to `range(1, n + 1)`.
  Run `python counter.py` to verify the fix.
category: "bugfix"
difficulty: "easy"
workspace_fixture: "mini_repo"
checks:
  - type: "file_contains"
    path: "counter.py"
    pattern: "range\\(1, n \\+ 1\\)"
  - type: "shell"
    command: "python counter.py"
    expected_stdout: "\\[1, 2, 3, 4, 5\\]"
expected_tools:
  - "read_file"
  - "edit_file"
  - "run_shell"
expected_turns_max: 8
tags: ["bugfix", "basics"]
```

### 8.3 Write Tests

```yaml
id: "write-tests-for-calculator"
name: "Write tests for calculator.py"
description: |
  The file `calculator.py` defines four functions: `add(a, b)`, `subtract(a, b)`,
  `multiply(a, b)`, `divide(a, b)`.  None have tests.
  Write a `test_calculator.py` file with pytest tests covering:
  - Normal cases for all four functions
  - Edge case: division by zero raises ValueError
  Run `python -m pytest test_calculator.py -v` to verify all tests pass.
category: "test"
difficulty: "medium"
workspace_fixture: "mini_repo"
checks:
  - type: "file_exists"
    path: "test_calculator.py"
  - type: "test_passes"
    path: "test_calculator.py"
expected_tools:
  - "read_file"
  - "write_file"
  - "run_tests"
expected_turns_max: 15
tags: ["testing", "basics"]
```

### 8.4 Refactor with Sub-Agent

```yaml
id: "refactor-split-module"
name: "Split data_utils.py into two files"
description: |
  The file `data_utils.py` contains both data loading functions and data
  transformation functions (prefixes: `load_*` and `transform_*`).
  Use sub-agents to split this into two files:
  - `data_loader.py` containing all `load_*` functions
  - `data_transformer.py` containing all `transform_*` functions
  Update `data_utils.py` to re-export from both new modules so existing
  imports don't break.
  Run `python -m pytest test_data_utils.py` to verify the split is correct.
category: "refactor"
difficulty: "hard"
workspace_fixture: "mini_repo"
checks:
  - type: "file_exists"
    path: "data_loader.py"
  - type: "file_exists"
    path: "data_transformer.py"
  - type: "test_passes"
    path: "test_data_utils.py"
expected_tools:
  - "spawn_agent"
  - "collect_agent"
  - "read_file"
  - "write_file"
  - "run_tests"
expected_turns_max: 25
tags: ["refactor", "multi-agent"]
```

---

## 9. Workspace Fixtures

Fixtures are minimal directory trees in `eval/fixtures/` that serve as the
starting state for tasks that need a specific file layout.  They avoid
polluting the real mini_agent workspace.

**`eval/fixtures/mini_repo/`** — example structure:

```
mini_repo/
├── counter.py              # Contains the off-by-one bug (for fix-off-by-one)
├── calculator.py           # Untested math functions (for write-tests-for-calculator)
├── data_utils.py           # Combined loader + transformer (for refactor-split-module)
├── test_data_utils.py      # Tests that must pass after refactor
└── .mini_agent.toml        # Minimal config for eval sessions
```

Fixtures are copied to a temp directory before each task run, so tasks are
isolated and idempotent.

---

## 10. Runner Implementation Sketch

```python
# eval/runner.py (sketch)

import os, sys, time, shutil, tempfile, threading
from dataclasses import dataclass, field
from eval.scorer import run_checks, CheckResult
from eval.metrics import MetricsCollector

@dataclass
class EvalTask:
    """Parsed from YAML."""
    id: str
    name: str
    description: str
    category: str
    difficulty: str
    checks: list[dict]
    workspace_fixture: str | None = None
    expected_tools: list[str] = field(default_factory=list)
    expected_turns_max: int | None = None
    expected_files_touched: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

@dataclass
class EvalResult:
    task_id: str
    success: bool
    checks: list[CheckResult]
    turns_used: int
    tool_calls: dict[str, int]
    tokens_consumed: int
    wall_time_seconds: float
    error: str | None = None
    diff: str = ""


def run_task(task: EvalTask, *, timeout_seconds: int = 300,
             stream: bool = False) -> EvalResult:
    """Run a single eval task."""

    # 1. Set up isolated workspace
    if task.workspace_fixture:
        fixture_path = os.path.join(
            os.path.dirname(__file__), "fixtures", task.workspace_fixture)
        workspace = tempfile.mkdtemp(prefix=f"eval_{task.id}_")
        shutil.copytree(fixture_path, workspace, dirs_exist_ok=True)
    else:
        # Copy current workspace to temp
        workspace = tempfile.mkdtemp(prefix=f"eval_{task.id}_")
        shutil.copytree(os.getcwd(), workspace, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(
                            '.git', '__pycache__', '.pytest_cache',
                            'venv', '.venv', 'node_modules', 'eval'))

    os.chdir(workspace)

    # 2. Init agent session
    from config import init_session
    session = init_session(workspace)
    config = session["config"]
    # Override for eval: unrestricted, allow overwrites, no TUI
    config.unrestricted = True
    config.allow_overwrites = True
    config.stream = stream
    config.verbose = False

    # 3. Inject task prompt
    session["messages"].append({
        "role": "user",
        "content": (
            f"Your task: {task.name}\n\n{task.description}\n\n"
            "When you are done, report what you changed and why."
        ),
    })

    # 4. Instrument with metrics collector
    metrics = MetricsCollector()
    cancel_event = threading.Event()
    start_time = time.monotonic()

    try:
        from llm import run_agent_turn

        # We wrap the session's run_agent_turn call.  The agent loop runs
        # until it produces a final answer or we cancel it.
        # For eval we call it once and let internal tool-calling loop handle
        # the multi-turn aspect (run_agent_turn already does this).
        result = run_agent_turn(
            messages=session["messages"],
            config=config,
            write_gate=session["write_gate"],
            read_gate=session["read_gate"],
            on_tool_start=metrics.on_tool_start,
            on_tool_end=metrics.on_tool_end,
            cancel_event=cancel_event,
            max_turns=task.expected_turns_max or 100,
            session=session["session"],
            memory_store=session["memory"],
        )
    except Exception as exc:
        elapsed = time.monotonic() - start_time
        shutil.rmtree(workspace, ignore_errors=True)
        return EvalResult(
            task_id=task.id,
            success=False,
            checks=[],
            turns_used=0,
            tool_calls=metrics.tool_counts,
            tokens_consumed=0,
            wall_time_seconds=elapsed,
            error=str(exc),
        )

    elapsed = time.monotonic() - start_time

    # 5. Collect diff
    import subprocess as sp
    diff_result = sp.run(["git", "diff"], capture_output=True, text=True,
                         cwd=workspace)
    diff = diff_result.stdout

    # 6. Run checks
    checks = run_checks(task.checks, workspace)

    # 7. Estimate tokens
    from memory import _total_tokens
    tokens = _total_tokens(session["messages"])

    # 8. Cleanup
    shutil.rmtree(workspace, ignore_errors=True)

    return EvalResult(
        task_id=task.id,
        success=all(c.passed for c in checks),
        checks=checks,
        turns_used=metrics.turn_count,
        tool_calls=dict(metrics.tool_counts),
        tokens_consumed=tokens,
        wall_time_seconds=elapsed,
        diff=diff,
    )
```

---

## 11. Metrics Collector

```python
# eval/metrics.py

from collections import Counter

class MetricsCollector:
    """Collects per-run metrics via agent callbacks."""

    def __init__(self):
        self.turn_count: int = 0
        self.tool_counts: Counter[str] = Counter()
        self._current_turn_tools: int = 0

    def on_tool_start(self, tool_name: str, args: dict) -> None:
        """Called before each tool executes."""
        self.tool_counts[tool_name] += 1
        self._current_turn_tools += 1

    def on_tool_end(self, tool_name: str, result) -> None:
        """Called after each tool completes."""
        # Track turn boundaries: a new turn starts after tool results
        # are fed back and the LLM is called again.  In practice,
        # run_agent_turn calls this after each batch, so we just count
        # each on_tool_start batch as one turn.
        pass

    def mark_turn(self) -> None:
        """Mark the start of a new agent turn (LLM → tools → LLM cycle)."""
        self.turn_count += 1
```

---

## 12. Scorer

```python
# eval/scorer.py

import os, re, subprocess, sys
from dataclasses import dataclass

@dataclass
class CheckResult:
    check_type: str
    passed: bool
    detail: str

_CHECKERS = {}

def _register(name: str):
    def dec(fn):
        _CHECKERS[name] = fn
        return fn
    return dec

def run_checks(checks: list[dict], workspace: str) -> list[CheckResult]:
    results = []
    for check in checks:
        checker = _CHECKERS.get(check["type"])
        if checker is None:
            results.append(CheckResult(
                check["type"], False,
                f"Unknown checker type: {check['type']}"))
            continue
        try:
            results.append(checker(check, workspace))
        except Exception as exc:
            results.append(CheckResult(
                check["type"], False,
                f"Checker error: {exc}"))
    return results


@_register("file_exists")
def _check_file_exists(params, workspace):
    path = os.path.join(workspace, params["path"])
    ok = os.path.isfile(path)
    return CheckResult("file_exists", ok,
                       f"{params['path']} {'exists' if ok else 'missing'}")

@_register("file_contains")
def _check_file_contains(params, workspace):
    path = os.path.join(workspace, params["path"])
    if not os.path.isfile(path):
        return CheckResult("file_contains", False, f"{params['path']} missing")
    content = open(path).read()
    match = re.search(params["pattern"], content, re.MULTILINE)
    return CheckResult("file_contains", bool(match),
                       f"pattern '{params['pattern']}' {'found' if match else 'not found'}")

@_register("test_passes")
def _check_test_passes(params, workspace):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", params["path"], "-q", "--tb=short"],
        cwd=workspace, capture_output=True, text=True, timeout=120,
    )
    ok = result.returncode == 0
    return CheckResult("test_passes", ok, result.stdout[-500:] or result.stderr[-500:])

@_register("diff_contains")
def _check_diff_contains(params, workspace):
    result = subprocess.run(
        ["git", "diff"], capture_output=True, text=True, cwd=workspace)
    ok = params["fragment"] in result.stdout
    return CheckResult("diff_contains", ok, "fragment found" if ok else "fragment not in diff")

@_register("shell")
def _check_shell(params, workspace):
    result = subprocess.run(
        params["command"], shell=True, capture_output=True, text=True,
        cwd=workspace, timeout=60,
    )
    expected_rc = params.get("expected_returncode", 0)
    rc_ok = result.returncode == expected_rc
    stdout_ok = True
    if "expected_stdout" in params:
        stdout_ok = bool(re.search(params["expected_stdout"], result.stdout))
    ok = rc_ok and stdout_ok
    detail = f"rc={result.returncode} (expected {expected_rc})"
    if not stdout_ok:
        detail += f"; stdout did not match '{params['expected_stdout']}'"
    return CheckResult("shell", ok, detail)
```

---

## 13. Design Decisions & Rationale

| Decision | Rationale |
|----------|-----------|
| **YAML not JSON** | More readable for task authors; supports multi-line strings naturally for task descriptions and diff fragments. |
| **Binary scoring** | Follows SWE-bench convention. No partial credit — a task is either fully solved or not. Keeps scoring unambiguous. |
| **Temp workspace copies** | Isolates tasks from each other and from the developer's dirty working tree. Prevents eval runs from corrupting the real repo. |
| **Callback-based instrumentation** | Uses existing `on_tool_start`/`on_tool_end` surface area. Zero changes to `llm.py` or `tools/__init__.py`. |
| **Pluggable checkers** | Different tasks need different validation: some need file checks, some need test runs, some need shell output. A registry pattern (same as tool dispatch) keeps it extensible. |
| **`workspace_fixture` instead of Docker** | mini_agent tasks are small and operate on a handful of Python files. Copying a fixture directory is fast enough. Docker adds complexity we don't need. |
| **pytest integration** | Makes eval tasks a first-class CI citizen. `pytest eval/test_eval.py` runs all tasks and reports failures like any other test. |
| **`expected_turns_max`** | Safety net: prevents an agent that's stuck in a loop from burning API credits forever during eval. |

---

## 14. Open Questions & Future Work

1. **Pass@k support:** Run each task k times and count if any attempt succeeds.
   Requires non-determinism handling (temperature > 0).  Useful for measuring
   model reliability.

2. **Token counting accuracy:** `_total_tokens()` is an estimate (character-based
   heuristic).  For eval we may want actual API-reported `usage` fields.  This
   requires surfacing `usage` from `call_deepseek` responses.

3. **Parallel task execution:** Run multiple eval tasks concurrently to speed up
   suite runs.  Each task uses its own temp workspace, so there's no shared state.

4. **Regression tracking:** Store historical run reports and compare pass rates
   over time.  Could be a simple CSV or a SQLite DB.

5. **Human-validated task set:** Like SWE-bench Verified, have a human review each
   task to confirm it's well-specified, solvable, and fairly scored before adding
   it to the default suite.

6. **Sub-agent eval tasks:** Tasks that explicitly test the multi-agent system
   (spawning sub-agents, collecting results, inter-agent messaging).

---

## 15. Implementation Plan

1. **Create `eval/` module structure** — `__init__.py`, `runner.py`,
   `scorer.py`, `metrics.py`, `tasks/`, `fixtures/`, `reports/`.
2. **Implement `scorer.py`** — all checker types + `run_checks()`.
3. **Implement `metrics.py`** — `MetricsCollector` class.
4. **Implement `runner.py`** — `run_task()`, `run_suite()`, task YAML loading.
5. **Implement CLI** — `python -m eval.runner` with argparse.
6. **Write example tasks** — 3-4 tasks covering feature, bugfix, test, refactor.
7. **Write fixture repos** — `mini_repo/` with pre-built files for tasks.
8. **Write `test_eval.py`** — pytest integration + unit tests for checkers.
9. **Document** — Update `STATE.txt` with eval module info.
10. **CI integration** — Add `pytest eval/test_eval.py` to CI workflow.
