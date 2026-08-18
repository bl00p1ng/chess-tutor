"""Bundled Learn Mode curriculum contracts."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace


SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lesson  # noqa: E402

from lesson import (  # noqa: E402
    PREDICATES,
    SQUARE_COORD_RE,
    load_lesson_file,
    validate_lesson,
)


BUNDLED_DIR = Path(__file__).parent.parent / "lessons"
STAGES_1_TO_3 = {
    "board-and-pieces": [
        "pawn-advance-1",
        "knight-jump-1",
        "bishop-diagonal-1",
        "rook-file-1",
    ],
    "special-rules": ["pawn-capture-1", "castle-options-1"],
    "material-and-mate": ["queen-capture-1", "mate-in-one-1"],
}

NARRATION_TEXT_FIELDS = (
    "title",
    "goal",
    "narration_seed",
    "success_text",
    "failure_text",
)
NARRATION_LIST_FIELDS = ("hints", "solution_text")

# A field may use a chess term only when it explains that term itself. The
# groups below list plain-language words that make the cited term understandable
# without requiring a learner to read another lesson field first.
BEGINNER_JARGON_RULES = {
    "rank": {
        "terms": ("rank", "ranks"),
        "required_groups": (("row", "rows"),),
    },
    "file": {
        "terms": ("file", "files"),
        "required_groups": (("column", "columns"),),
    },
    "castling": {
        "terms": ("castling",),
        "required_groups": (("special move",), ("king",), ("rook",)),
    },
    "check or checkmate": {
        "terms": ("checkmate", "in check", "to check", "checks the", "check along"),
        "required_groups": (("attack",), ("king",), ("escape",)),
    },
    "opening": {
        "terms": ("opening",),
        "required_groups": (("first move", "first moves"),),
    },
    "develop": {
        "terms": ("develop", "develops", "developing"),
        "required_groups": (("bring",), ("into play",)),
    },
    "central space": {
        "terms": ("central space",),
        "required_groups": (("middle",), ("board",)),
    },
}


def _bundled_lesson_paths():
    return sorted(BUNDLED_DIR.glob("*.json"))


def _raw_bundled_lessons():
    return [json.loads(path.read_text()) for path in _bundled_lesson_paths()]


def test_bundled_lessons_exist_and_validate():
    """Bundled stages 1–3 are complete, ordered, and loadable without
    special test-only handling."""
    paths = _bundled_lesson_paths()
    assert paths, "Expected bundled lesson JSON files."

    lessons = []
    for path in paths:
        raw_lesson = json.loads(path.read_text())
        assert validate_lesson(raw_lesson) == [], path.name
        assert load_lesson_file(str(path)) == raw_lesson
        lessons.append(raw_lesson)

    actual_inventory = {
        stage: [lesson["id"] for lesson in sorted(
            (lesson for lesson in lessons if lesson["stage"] == stage),
            key=lambda lesson: lesson["order"],
        )]
        for stage in STAGES_1_TO_3
    }
    assert actual_inventory == STAGES_1_TO_3


def test_bundled_stages_one_through_three_cover_each_predicate():
    """The first three stages exercise every supported scored-drill type."""
    objective_types = {
        lesson["objective"]["type"]
        for lesson in _raw_bundled_lessons()
        if lesson["stage"] != "guided-play"
    }
    assert objective_types == set(PREDICATES)


def test_bundled_stage_four_free_play_completes_through_bridge(tmp_path, monkeypatch):
    """A real bundled guided-play lesson completes only through the bound
    bridge flow, rather than merely existing as valid JSON."""
    lessons = _raw_bundled_lessons()
    guided_lessons = sorted(
        (lesson_data for lesson_data in lessons if lesson_data["stage"] == "guided-play"),
        key=lambda lesson_data: lesson_data["order"],
    )
    assert [lesson_data["id"] for lesson_data in guided_lessons] == [
        "guided-opening-1",
        "guided-opening-2",
    ]
    assert [lesson_data["objective"] for lesson_data in guided_lessons] == [
        {"type": "free_play", "min_moves": 2},
        {"type": "free_play", "min_moves": 4},
    ]

    home = tmp_path / "home"
    lesson_dir = home / ".chess_coach"
    lesson_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    completed = {
        lesson_data["id"]: {
            "completed_at": "2026-08-18T00:00:00+00:00",
            "attempts": 1,
            "hints_used": 0,
        }
        for lesson_data in lessons
        if lesson_data["stage"] != "guided-play"
    }
    (lesson_dir / "learning.json").write_text(json.dumps({
        "schema_version": 1,
        "completed": completed,
        "last_lesson_id": None,
    }))

    definition = guided_lessons[0]
    state_path = lesson_dir / "current_lesson.json"
    start = lesson.cmd_start(SimpleNamespace(
        id=definition["id"],
        state=str(state_path),
        bundled_dir=str(BUNDLED_DIR),
        user_dir=str(tmp_path / "user"),
    ))
    assert start["ok"] is True
    assert start["stage"] == "guided-play"
    assert start["objective"] == {"type": "free_play", "min_moves": 2}

    redirected = lesson.cmd_attempt(SimpleNamespace(move="e2e4", state=str(state_path)))
    assert redirected["accepted"] is False
    assert "bridge" in redirected["reason"]
    assert lesson.cmd_bridge_eval(SimpleNamespace(move="e2e4"))["ok"] is True
    assert lesson.cmd_bridge_move(SimpleNamespace(move="e2e4"))["ok"] is True
    assert lesson.cmd_bridge_ai(SimpleNamespace())["ok"] is True

    status = lesson.cmd_status(SimpleNamespace(
        state=str(state_path),
        bundled_dir=str(BUNDLED_DIR),
        user_dir=str(tmp_path / "user"),
    ))
    assert status["active"] is None
    assert definition["id"] in status["completed_ids"]
    saved_state = json.loads(state_path.read_text())
    assert saved_state["lesson"]["result"] == "solved"
    assert not (lesson_dir / "current_game.json").exists()


def _lesson_with_isolated_narration_field(lesson_data, field, text):
    candidate = dict(lesson_data)
    candidate.update({
        "goal": "Choose a legal chess move to complete this lesson.",
        "narration_seed": "This lesson explains one useful chess idea.",
        "success_text": "You completed this chess lesson successfully.",
        "failure_text": "Try the chess lesson again with a new move.",
        "hints": ["Review the board before choosing a legal move."],
        "solution_text": ["Use the lesson objective to choose the correct move."],
    })
    if field in NARRATION_TEXT_FIELDS:
        candidate[field] = text
    else:
        candidate[field] = [text]
    return candidate


def test_bundled_narration_self_contained():
    """Every bundled user-facing field is complete prose and passes the
    square-phrasing rule when validated separately from every other field."""
    for lesson_data in _raw_bundled_lessons():
        for field in NARRATION_TEXT_FIELDS + NARRATION_LIST_FIELDS:
            texts = lesson_data[field] if field in NARRATION_LIST_FIELDS else [lesson_data[field]]
            assert texts, f"{lesson_data['id']} has no {field} text"
            for text in texts:
                assert text[0].isupper(), f"{lesson_data['id']} {field}: {text}"
                assert text.endswith((".", "!", "?")), f"{lesson_data['id']} {field}: {text}"
                assert SQUARE_COORD_RE.search(text) is None, f"{lesson_data['id']} {field}: {text}"
                assert validate_lesson(
                    _lesson_with_isolated_narration_field(lesson_data, field, text)
                ) == [], f"{lesson_data['id']} {field}: {text}"


def _bare_beginner_jargon(text):
    lower_text = text.lower()
    return [
        name
        for name, rule in BEGINNER_JARGON_RULES.items()
        if any(term in lower_text for term in rule["terms"])
        and not all(
            any(plain_term in lower_text for plain_term in alternatives)
            for alternatives in rule["required_groups"]
        )
    ]


def test_bundled_narration_defines_or_replaces_beginner_jargon():
    """A learner reading one field alone never has to infer the cited chess
    jargon from another field or from prior chess experience."""
    violations = []
    for lesson_data in _raw_bundled_lessons():
        for field in NARRATION_TEXT_FIELDS + NARRATION_LIST_FIELDS:
            texts = lesson_data[field] if field in NARRATION_LIST_FIELDS else [lesson_data[field]]
            for text in texts:
                for term in _bare_beginner_jargon(text):
                    violations.append(f"{lesson_data['id']} {field}: bare {term}")
    assert not violations, "\n".join(violations)
