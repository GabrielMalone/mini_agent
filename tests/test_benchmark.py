"""Tests for the benchmark harness — exercise discovery and test command detection.

These tests validate the harness infrastructure without making LLM API calls.
"""

from __future__ import annotations

from pathlib import Path

from benchmark.benchmark import (
    list_exercises,
    _find_instructions,
    _find_stub_files,
    _find_test_command,
)


def test_list_python_exercises() -> None:
    """Verify we can discover Python exercises."""
    exercises = list_exercises("python")
    assert len(exercises) > 0, "Should find Python exercises"
    for ex in exercises:
        assert ex.is_dir()
        # Should be nested: {lang}/exercises/practice/{name}
        assert ex.parent.name == "practice"
        assert ex.parent.parent.name == "exercises"
        assert ex.parent.parent.parent.name == "python"


def test_list_invalid_language() -> None:
    """Verify invalid language returns empty list."""
    exercises = list_exercises("zzz_nonexistent_zzz")
    assert exercises == []


def test_all_languages_have_exercises() -> None:
    """Verify each language directory has exercises."""
    exercises = list_exercises()  # all languages
    langs = {e.parent.parent.parent.name for e in exercises}
    assert "python" in langs, "Python exercises should exist"


def test_find_instructions() -> None:
    """Verify instructions are found for Python exercises."""
    exercises = list_exercises("python")
    for ex in exercises[:5]:
        instructions = _find_instructions(ex)
        assert instructions is not None, f"No instructions for {ex.name}"
        assert len(instructions) > 50, f"Instructions too short for {ex.name}"


def test_find_stub_files() -> None:
    """Verify stub files are found and test files are excluded."""
    exercises = list_exercises("python")
    for ex in exercises[:5]:
        stubs = _find_stub_files(ex)
        assert len(stubs) >= 1, f"No stubs for {ex.name}"
        for stub in stubs:
            assert stub.suffix == ".py"
            assert "test" not in stub.name.lower(), f"Test file leaked into stubs: {stub.name}"


def test_find_test_command() -> None:
    """Verify test commands are generated correctly for Python."""
    exercises = list_exercises("python")
    for ex in exercises[:5]:
        cmd = _find_test_command(ex)
        assert cmd is not None, f"No test command for {ex.name}"
        assert "-m" in cmd
        assert "pytest" in cmd
        assert cmd[-1].endswith("_test.py"), f"Test file not ending in _test.py: {cmd[-1]}"


def test_exercise_structure() -> None:
    """Verify exercise directory structure is as expected."""
    exercises = list_exercises("python")
    for ex in exercises:
        # Should have .docs/instructions.md
        assert (ex / ".docs" / "instructions.md").exists(), f"Missing instructions for {ex.name}"
        # Should have at least one .py file
        py_files = list(ex.glob("*.py"))
        assert len(py_files) >= 2, f"Expected >=2 .py files for {ex.name}, got {len(py_files)}"
        # At least one should be a test file
        test_files = list(ex.glob("*_test.py"))
        assert len(test_files) >= 1, f"No test file for {ex.name}"


def test_language_detection() -> None:
    """Verify language is correctly detected from exercise path."""
    exercises = list_exercises("python")
    for ex in exercises:
        lang = ex.parent.parent.parent.name
        assert lang == "python"
        # Verify _find_test_command uses correct language
        cmd = _find_test_command(ex)
        assert cmd is not None
        assert "pytest" in cmd  # Python-specific


def test_exercise_names_unique() -> None:
    """Verify no duplicate exercise names within a language."""
    exercises = list_exercises("python")
    names = [e.name for e in exercises]
    assert len(names) == len(set(names)), f"Duplicate exercise names: {set(n for n in names if names.count(n) > 1)}"
