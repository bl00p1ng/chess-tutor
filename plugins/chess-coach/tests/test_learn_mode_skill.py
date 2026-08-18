"""Runtime-contract coverage for the Learn Mode skill instructions."""

from pathlib import Path
import re
import subprocess
import sys


PLUGIN_ROOT = Path(__file__).parent.parent
SKILL_PATH = PLUGIN_ROOT / "skills" / "learn-mode" / "SKILL.md"
LESSON_SCRIPT = PLUGIN_ROOT / "scripts" / "lesson.py"
RENDER_SCRIPT = PLUGIN_ROOT / "scripts" / "render.py"


def _skill_text():
    return SKILL_PATH.read_text()


def _command_lines(text):
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("python3 plugins/chess-coach/scripts/")
    ]


def _help(command):
    return subprocess.run(
        [sys.executable, str(LESSON_SCRIPT), command, "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_skill_is_discoverable_and_uses_the_runtime_skill_shape():
    """The installed artifact is a concise, discoverable LLM contract."""
    text = _skill_text()
    frontmatter, body = text.split("---", 2)[1:]

    assert re.search(r"^name: learn-mode$", frontmatter, re.MULTILINE)
    description = re.search(r'^description: "([^"]+)"$', frontmatter, re.MULTILINE)
    assert description
    assert description.group(1).startswith("Trigger:")
    assert "learn" in description.group(1).lower()
    assert "license:" in frontmatter
    assert "author:" in frontmatter
    assert "version:" in frontmatter

    headings = [
        "## Activation Contract",
        "## Hard Rules",
        "## Decision Gates",
        "## Execution Steps",
        "## Output Contract",
        "## References",
    ]
    positions = [body.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_skill_uses_status_to_resume_and_only_bound_bridge_mutations():
    """The documented commands match the live CLI and never expose bridge state selection."""
    text = _skill_text()
    commands = _command_lines(text)

    assert "python3 plugins/chess-coach/scripts/lesson.py status" in commands
    assert "active" in text and "next_lesson" in text
    assert "Treat the script verdict as authoritative" in text
    assert "Do not independently judge" in text
    assert "Bridge objectives are redirected by `attempt`; `attempt` never evaluates them." in text
    assert "Use the bound `bridge_eval`, `bridge_move`, and `bridge_ai` commands for guided play." in text

    expected_bridge_commands = {
        "python3 plugins/chess-coach/scripts/lesson.py bridge_eval --move <uci>",
        "python3 plugins/chess-coach/scripts/lesson.py bridge_move --move <uci>",
        "python3 plugins/chess-coach/scripts/lesson.py bridge_ai",
    }
    assert expected_bridge_commands <= set(commands)
    bridge_commands = [line for line in commands if "lesson.py bridge_" in line]
    assert all("--state" not in line for line in bridge_commands)
    assert "python3 plugins/chess-coach/scripts/render.py --plain --state ~/.chess_coach/current_lesson.json" in commands

    for command in ("bridge_eval", "bridge_move", "bridge_ai"):
        help_text = _help(command)
        assert "--state" not in help_text
    assert "--state" in subprocess.run(
        [sys.executable, str(RENDER_SCRIPT), "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_skill_lists_current_start_and_scored_drill_commands():
    """New lessons, hints, moves, and quizzes use the live lesson CLI argument shapes."""
    text = _skill_text()
    commands = set(_command_lines(text))

    assert {
        "python3 plugins/chess-coach/scripts/lesson.py start --id <lesson-id>",
        "python3 plugins/chess-coach/scripts/lesson.py hint",
        "python3 plugins/chess-coach/scripts/lesson.py attempt --move <uci>",
        "python3 plugins/chess-coach/scripts/lesson.py attempt --squares <square1,square2,...>",
    } <= commands
    assert "--id" in _help("start")
    attempt_help = _help("attempt")
    assert "--move" in attempt_help and "--squares" in attempt_help


def test_skill_requires_explicit_opt_in_before_graduation_handoff():
    """Finishing the final lesson offers Coach or Play before any handoff."""
    text = _skill_text()

    assert "Do not start Coach or Play automatically." in text
    assert "Ask: \"You completed the curriculum. Would you like to start Coach or Play from the standard chess position?\"" in text
    assert "Only after an explicit yes" in text
    assert "Load the `chess-coach` skill" in text


def test_skill_has_no_automatic_engine_start_at_graduation():
    """The pre-acceptance path cannot run the actual engine new-game syntax."""
    text = _skill_text()
    before_acceptance, after_acceptance = text.split("Only after an explicit yes", 1)

    assert "Never run `engine.py new_game` before explicit graduation acceptance." in text
    assert not [
        command
        for command in _command_lines(before_acceptance)
        if "engine.py new_game" in command
    ]
    assert "Load the `chess-coach` skill" in after_acceptance
    assert "Do not start Coach or Play automatically." in text
