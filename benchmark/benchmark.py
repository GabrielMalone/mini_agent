#!/usr/bin/env python3
"""
mini_agent benchmark harness — Exercism Polyglot exercises.

Evaluates mini_agent against coding exercises from the Aider polyglot
benchmark (https://github.com/Aider-AI/polyglot-benchmark). Each exercise
provides instructions + stub code + test suite. The agent reads the
instructions, implements the stub, and passes the tests.

Usage:
    python benchmark/benchmark.py --setup          # clone exercises
    python benchmark/benchmark.py --language python  # run all Python exercises
    python benchmark/benchmark.py -e hello-world     # single exercise
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path for imports
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.config import AgentConfig  # noqa: E402
from core.llm import run_agent_turn  # noqa: E402
from core.safety import ReadSafetyGate, WriteSafetyGate  # noqa: E402

BENCHMARK_DIR = _HERE
EXERCISES_DIR = BENCHMARK_DIR / "exercises"
EXERCISES_REPO = "https://github.com/Aider-AI/polyglot-benchmark.git"


# ---------------------------------------------------------------------------
# Exercise management
# ---------------------------------------------------------------------------


def clone_exercises() -> None:
    """Clone the polyglot-benchmark exercises if not already present."""
    if EXERCISES_DIR.exists():
        print(f"Exercises already at {EXERCISES_DIR}, pulling...")
        subprocess.run(
            ["git", "-C", str(EXERCISES_DIR), "pull", "--ff-only"],
            check=False,
        )
        return

    print(f"Cloning {EXERCISES_REPO}...")
    subprocess.run(
        ["git", "clone", "--depth=1", EXERCISES_REPO, str(EXERCISES_DIR)],
        check=True,
    )


def list_exercises(language: Optional[str] = None) -> list[Path]:
    """Return list of exercise directories for the given language.

    Exercises are nested: {lang}/exercises/practice/{exercise_name}/
    """
    if not EXERCISES_DIR.exists():
        clone_exercises()

    exercises: list[Path] = []
    for lang_dir in sorted(EXERCISES_DIR.iterdir()):
        if not lang_dir.is_dir() or lang_dir.name.startswith("."):
            continue
        if language and lang_dir.name.lower() != language.lower():
            continue

        # Navigate into exercises/practice/
        practice_dir = lang_dir / "exercises" / "practice"
        if not practice_dir.is_dir():
            continue

        for ex_dir in sorted(practice_dir.iterdir()):
            if ex_dir.is_dir() and not ex_dir.name.startswith("."):
                exercises.append(ex_dir)

    return exercises


# ---------------------------------------------------------------------------
# Per-language test command detection
# ---------------------------------------------------------------------------


def _find_test_command(exercise_dir: Path) -> Optional[list[str]]:
    """Detect the test command for this exercise."""
    # Exercise path: {lang}/exercises/practice/{exercise_name}
    # So exercise_dir.parent.parent.parent.name = language
    lang = exercise_dir.parent.parent.parent.name.lower()

    if lang == "python":
        test_files = list(exercise_dir.glob("*_test.py"))
        if test_files:
            return [sys.executable, "-m", "pytest", "-q", str(test_files[0])]
        return [sys.executable, "-m", "pytest", "-q", str(exercise_dir)]
    elif lang == "rust":
        return ["cargo", "test"]
    elif lang == "go":
        return ["go", "test", "./..."]
    elif lang == "javascript":
        pkg_json = exercise_dir / "package.json"
        if pkg_json.exists():
            return ["npm", "test"]
        return ["npx", "jest"]
    elif lang == "java":
        return ["./gradlew", "test"]
    elif lang == "cpp":
        return None

    return None


def _find_instructions(exercise_dir: Path) -> Optional[str]:
    """Find the instructions file for this exercise."""
    candidates = [
        exercise_dir / ".docs" / "instructions.md",
        exercise_dir / "README.md",
        exercise_dir / "instructions.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text()
    return None


def _find_stub_files(exercise_dir: Path) -> list[Path]:
    """Find stub/implementation files the agent should edit."""
    ignore_dirs = {".docs", ".meta", ".articles", ".approaches", "build", "__pycache__"}
    stub_files: list[Path] = []

    for f in sorted(exercise_dir.rglob("*")):
        if f.is_dir():
            continue
        parts = set(f.relative_to(exercise_dir).parts)
        if parts & ignore_dirs:
            continue
        if "test" in f.name.lower() or f.name.endswith("_test.py"):
            continue
        if f.suffix in {".md", ".toml", ".json"}:
            continue
        if f.suffix in {".py", ".rs", ".go", ".js", ".ts", ".java", ".cpp", ".hpp", ".h", ".c"}:
            stub_files.append(f)

    return stub_files


# ---------------------------------------------------------------------------
# Single exercise runner
# ---------------------------------------------------------------------------


def run_exercise(
    exercise_dir: Path,
    *,
    attempts: int = 2,
    timeout: int = 300,
    verbose: bool = False,
) -> dict:
    """Run mini_agent on a single exercise and return results."""
    exercise_name = exercise_dir.name
    # Exercise path: {lang}/exercises/practice/{exercise_name}
    language = exercise_dir.parent.parent.parent.name

    instructions = _find_instructions(exercise_dir)
    if not instructions:
        return {
            "name": exercise_name, "language": language,
            "passed": False, "attempts_used": 0,
            "duration_sec": 0, "error": "No instructions found",
        }

    stub_files = _find_stub_files(exercise_dir)
    test_command = _find_test_command(exercise_dir)
    if not test_command:
        return {
            "name": exercise_name, "language": language,
            "passed": False, "attempts_used": 0,
            "duration_sec": 0, "error": "No test command detected",
        }

    start_time = time.time()

    # Build the prompt
    stub_list = "\n".join(f"  - {f.relative_to(exercise_dir)}" for f in stub_files)
    prompt = f"""You are a coding assistant. Implement the exercise described below.

{instructions}

Files you must edit (keep existing function/class stubs — they will be called by unit tests):
{stub_list}

Important:
- Only use standard libraries. Do NOT install any packages.
- After implementing, run the tests to verify your solution.
- If tests fail, read the errors and fix the code.
- Your workspace is {exercise_dir}"""

    # Set up AgentConfig
    config = AgentConfig(workspace=str(exercise_dir))

    # Safety gates — allow all reads/writes within the exercise dir
    write_gate = WriteSafetyGate(workspace_root=str(exercise_dir))
    read_gate = ReadSafetyGate(workspace_root=str(exercise_dir))

    for attempt in range(1, attempts + 1):
        if verbose:
            print(f"  Attempt {attempt}/{attempts}...")

        messages: list[dict] = [{"role": "user", "content": prompt}]

        try:
            run_agent_turn(
                messages,
                config,
                write_gate,
                read_gate,
                max_turns=50,
            )
        except Exception as e:
            if verbose:
                print(f"    Agent error: {e}")

        # Run the tests
        try:
            test_result = subprocess.run(
                test_command,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(exercise_dir),
            )
            if test_result.returncode == 0:
                return {
                    "name": exercise_name, "language": language,
                    "passed": True, "attempts_used": attempt,
                    "duration_sec": time.time() - start_time, "error": None,
                }

            if verbose:
                print(f"    Tests failed ({test_result.returncode})")
                if test_result.stderr:
                    print(f"    stderr: {test_result.stderr[:300]}")
                if test_result.stdout:
                    print(f"    stdout: {test_result.stdout[:300]}")

            # Prepare retry prompt with test errors
            if attempt < attempts:
                error_output = (test_result.stderr + test_result.stdout)[:2000]
                prompt = f"""The tests failed. Fix the code.

Test output:
{error_output}

The tests are correct. Fix the code in the stub files to make all tests pass.
Only use standard libraries. Do NOT install any packages."""

        except subprocess.TimeoutExpired:
            return {
                "name": exercise_name, "language": language,
                "passed": False, "attempts_used": attempt,
                "duration_sec": time.time() - start_time,
                "error": "Test execution timeout",
            }

    return {
        "name": exercise_name, "language": language,
        "passed": False, "attempts_used": attempts,
        "duration_sec": time.time() - start_time,
        "error": f"Failed after {attempts} attempts",
    }


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------


def run_benchmark(
    language: Optional[str] = None,
    exercise: Optional[str] = None,
    attempts: int = 2,
    timeout: int = 300,
    verbose: bool = False,
    output: Optional[str] = None,
) -> dict:
    """Run the benchmark and return results summary."""
    if exercise:
        exercises = [p for p in list_exercises(language) if p.name == exercise]
        if not exercises:
            print(f"Exercise '{exercise}' not found for language '{language}'")
            sys.exit(1)
    else:
        exercises = list_exercises(language)

    if not exercises:
        print(f"No exercises found for language '{language or 'all'}'")
        sys.exit(1)

    print(f"Running benchmark: {len(exercises)} exercises")
    if language:
        print(f"Language: {language}")
    print(f"Attempts per exercise: {attempts}")
    print()

    results = []
    passed = 0

    for i, ex_dir in enumerate(exercises):
        name = f"{ex_dir.parent.parent.parent.name}/{ex_dir.name}"
        print(f"[{i+1}/{len(exercises)}] {name}...", end=" ", flush=True)

        result = run_exercise(
            ex_dir,
            attempts=attempts,
            timeout=timeout,
            verbose=verbose,
        )
        results.append(result)

        if result["passed"]:
            passed += 1
            print(f"PASS (attempt {result['attempts_used']}, {result['duration_sec']:.1f}s)")
        else:
            print(f"FAIL ({result.get('error', 'unknown')})")

    summary = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": passed / len(results) * 100 if results else 0,
        "results": results,
    }

    print(f"\n{'='*50}")
    print(f"Results: {passed}/{len(results)} passed ({summary['pass_rate']:.1f}%)")
    print(f"{'='*50}")

    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Results written to {output}")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="mini_agent benchmark harness (Exercism Polyglot exercises)",
    )
    parser.add_argument("--language", "-l", help="Language: python, rust, go, javascript, java, cpp")
    parser.add_argument("--exercise", "-e", help="Run a single exercise by name")
    parser.add_argument("--attempts", "-a", type=int, default=2, help="Max attempts per exercise (default: 2)")
    parser.add_argument("--timeout", "-t", type=int, default=300, help="Timeout per attempt in seconds")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--output", "-o", help="Write JSON results to file")
    parser.add_argument("--setup", action="store_true", help="Clone exercises and exit")

    args = parser.parse_args()

    if args.setup:
        clone_exercises()
        exercises = list_exercises(args.language)
        print(f"Cloned {len(exercises)} exercises")
        if args.language:
            print(f"Language: {args.language}")
        return

    run_benchmark(
        language=args.language,
        exercise=args.exercise,
        attempts=args.attempts,
        timeout=args.timeout,
        verbose=args.verbose,
        output=args.output,
    )


if __name__ == "__main__":
    main()
