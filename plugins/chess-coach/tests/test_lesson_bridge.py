"""Integration coverage for the lesson bridge command surface."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import chess

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

import lesson  # noqa: E402
from lesson import cmd_bridge_ai, cmd_bridge_eval, cmd_bridge_move  # noqa: E402


def make_bridge_lesson(**overrides):
    """Return a valid guided-play definition for bridge lifecycle tests."""
    definition = {
        "schema_version": 1,
        "id": "free-play-1",
        "stage": "guided-play",
        "order": 1,
        "title": "Play a Short Game",
        "goal": "Play enough moves against the coach to practice your skills.",
        "narration_seed": "Use this short game to apply the skills you have learned.",
        "start_fen": chess.Board().fen(),
        "player_color": "white",
        "objective": {"type": "free_play", "min_moves": 2},
        "allowed_pieces": ["P", "N", "B", "R", "Q", "K"],
        "max_attempts": 1,
        "hints": ["Consider the safety of every piece before you move it."],
        "solution_text": ["Continue playing until you have practiced enough moves."],
        "solution_san": [],
        "success_text": "You completed the guided practice game.",
        "failure_text": "Continue the guided practice game.",
    }
    definition.update(overrides)
    return definition


def make_lesson_state(definition, moves_uci=()):
    """Build a current_lesson.json-shaped state around a bridge definition."""
    return {
        "color": "white",
        "player_name": "learner",
        "players": {"white": "learner", "black": "ai"},
        "level": "beginner",
        "mode": "lesson",
        "moves_uci": list(moves_uci),
        "moves_san": [],
        "move_records": [],
        "move_count": len(moves_uci),
        "result": None,
        "opening": None,
        "start_fen": definition["start_fen"],
        "lesson": {
            "definition": definition,
            "moves_history": [],
            "moves_used": 0,
            "attempts_used": 0,
            "hints_used": 0,
            "result": None,
        },
    }


def status_args(state_path, tmp_path):
    return SimpleNamespace(
        state=str(state_path),
        bundled_dir=str(tmp_path / "bundled"),
        user_dir=str(tmp_path / "user"),
    )


def test_bridge_eval_move_ai_in_process(tmp_path, monkeypatch):
    """Bridge commands call their upstream handlers in-process with the
    lesson-state path bound in code, even if a caller supplies a game path."""
    home = tmp_path / "home"
    (home / ".chess_coach").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    game_path = home / ".chess_coach" / "current_game.json"
    expected_state = str(home / ".chess_coach" / "current_lesson.json")
    calls = []

    def fake_eval(args):
        calls.append(("eval", vars(args).copy()))
        return {"ok": True, "handler": "eval"}

    def fake_move(args):
        calls.append(("move", vars(args).copy()))
        return {"ok": True, "handler": "move"}

    def fake_ai(args):
        calls.append(("ai", vars(args).copy()))
        return {"ok": True, "handler": "ai"}

    monkeypatch.setattr(lesson, "cmd_evaluate_user", fake_eval)
    monkeypatch.setattr(lesson, "cmd_move", fake_move)
    monkeypatch.setattr(lesson, "cmd_ai_move", fake_ai)

    assert cmd_bridge_eval(SimpleNamespace(move="e2e4", state=str(game_path))) == {
        "ok": True,
        "handler": "eval",
    }
    assert cmd_bridge_move(SimpleNamespace(move="e2e4", state=str(game_path))) == {
        "ok": True,
        "handler": "move",
    }
    assert cmd_bridge_ai(SimpleNamespace(state=str(game_path))) == {
        "ok": True,
        "handler": "ai",
    }

    assert calls == [
        ("eval", {"state": expected_state, "move": "e2e4"}),
        ("move", {"state": expected_state, "move": "e2e4"}),
        ("ai", {"state": expected_state}),
    ]


def test_bridge_cli_refuses_user_state_flag(tmp_path):
    """The bridge CLI accepts a move but exposes no mutable --state flag."""
    result = subprocess.run(
        [
            sys.executable,
            lesson.__file__,
            "bridge_eval",
            "--move",
            "e2e4",
            "--state",
            str(tmp_path / "current_game.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "unrecognized arguments: --state" in result.stderr


def test_status_evaluates_free_play_completion(tmp_path, monkeypatch):
    """Only status completes free play at min_moves; attempt remains a redirect."""
    home = tmp_path / "home"
    lesson_dir = home / ".chess_coach"
    lesson_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    definition = make_bridge_lesson()
    state_path = lesson_dir / "current_lesson.json"
    state_path.write_text(json.dumps(make_lesson_state(definition, ["e2e4"])))

    redirected = lesson.cmd_attempt(SimpleNamespace(move="e2e4", state=str(state_path)))
    assert redirected == {
        "ok": True,
        "accepted": False,
        "reason": (
            "This lesson is a free-play bridge, not a scored drill. "
            "Use the bridge commands to play moves instead."
        ),
    }

    before_threshold = lesson.cmd_status(status_args(state_path, tmp_path))
    assert before_threshold["active"]["lesson_id"] == definition["id"]
    assert not (lesson_dir / "learning.json").exists()

    state = json.loads(state_path.read_text())
    state["moves_uci"].append("e7e5")
    state["move_count"] += 1
    state_path.write_text(json.dumps(state))

    completed = lesson.cmd_status(status_args(state_path, tmp_path))
    assert completed["active"] is None
    assert completed["completed_ids"] == [definition["id"]]
    saved = json.loads(state_path.read_text())
    assert saved["lesson"]["result"] == "solved"
    learning = json.loads((lesson_dir / "learning.json").read_text())
    assert definition["id"] in learning["completed"]


def test_lesson_lifecycle_no_game_file_dependency(tmp_path, monkeypatch):
    """A start, hint, bridge loop, completion, and resume work without a
    current_game.json file anywhere in the lesson home directory."""
    home = tmp_path / "home"
    lesson_dir = home / ".chess_coach"
    bundled_dir = tmp_path / "bundled"
    lesson_dir.mkdir(parents=True)
    bundled_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    definition = make_bridge_lesson()
    (bundled_dir / "free-play-1.json").write_text(json.dumps(definition))
    lesson_state = lesson_dir / "current_lesson.json"
    game_state = lesson_dir / "current_game.json"

    started = lesson.cmd_start(SimpleNamespace(
        id=definition["id"],
        state=str(lesson_state),
        bundled_dir=str(bundled_dir),
        user_dir=str(tmp_path / "user"),
    ))
    assert started["ok"] is True
    assert not game_state.exists()

    hinted = lesson.cmd_hint(SimpleNamespace(state=str(lesson_state)))
    assert hinted["ok"] is True
    resumed = lesson.cmd_status(status_args(lesson_state, tmp_path))
    assert resumed["active"]["lesson_id"] == definition["id"]

    redirected = lesson.cmd_attempt(SimpleNamespace(move="e2e4", state=str(lesson_state)))
    assert redirected["accepted"] is False
    evaluated = cmd_bridge_eval(SimpleNamespace(move="e2e4"))
    assert evaluated["ok"] is True
    moved = cmd_bridge_move(SimpleNamespace(move="e2e4"))
    assert moved["ok"] is True
    ai_moved = cmd_bridge_ai(SimpleNamespace())
    assert ai_moved["ok"] is True

    completed = lesson.cmd_status(status_args(lesson_state, tmp_path))
    assert completed["active"] is None
    assert definition["id"] in completed["completed_ids"]
    assert not game_state.exists()


def test_game_file_byte_identical_after_lesson_lifecycle(tmp_path, monkeypatch):
    """A complete bridge lifecycle leaves an existing active-game file
    byte-for-byte unchanged, including its whitespace and trailing newline."""
    home = tmp_path / "home"
    lesson_dir = home / ".chess_coach"
    bundled_dir = tmp_path / "bundled"
    lesson_dir.mkdir(parents=True)
    bundled_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    definition = make_bridge_lesson()
    (bundled_dir / "free-play-1.json").write_text(json.dumps(definition))
    lesson_state = lesson_dir / "current_lesson.json"
    game_state = lesson_dir / "current_game.json"
    game_bytes = (
        b'{\n  "color": "white",\n  "mode": "play",\n'
        b'  "moves_uci": ["e2e4"],\n  "moves_san": ["e4"]\n}\n'
    )
    game_state.write_bytes(game_bytes)

    started = lesson.cmd_start(SimpleNamespace(
        id=definition["id"],
        state=str(lesson_state),
        bundled_dir=str(bundled_dir),
        user_dir=str(tmp_path / "user"),
    ))
    assert started["ok"] is True
    hinted = lesson.cmd_hint(SimpleNamespace(state=str(lesson_state)))
    assert hinted["ok"] is True
    redirected = lesson.cmd_attempt(SimpleNamespace(move="e2e4", state=str(lesson_state)))
    assert redirected["accepted"] is False
    assert cmd_bridge_eval(SimpleNamespace(move="e2e4"))["ok"] is True
    assert cmd_bridge_move(SimpleNamespace(move="e2e4"))["ok"] is True
    assert cmd_bridge_ai(SimpleNamespace())["ok"] is True
    assert lesson.cmd_status(status_args(lesson_state, tmp_path))["active"] is None

    assert game_state.read_bytes() == game_bytes
