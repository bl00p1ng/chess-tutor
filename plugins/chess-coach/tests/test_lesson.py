"""
test_lesson.py — Lesson library: registries, validate_lesson, and the four
verification predicate checks (plugins/chess-coach/scripts/lesson.py).

lesson.py ships as an inert library in this slice: no CLI, no argparse, no
command dispatch. Every test below imports and calls library functions
directly (pure-function style, matching test_render_width.py) — there is
nothing to shell out to yet.

Covers:
  - PREDICATES / BRIDGE_OBJECTIVES / DEFERRED_TYPES: two disjoint registries
    plus a deferred allowlist (adjudication #1 — never merge these).
  - validate_lesson: required fields, stage-constrained objective-type
    dispatch, narration self-containment (F7), cross-field FEN/objective
    occupancy checks.
  - The four PREDICATES checker functions (reach_square, capture_square,
    checkmate_in_1, legal_moves_from_square).
  - load_lesson_file: single-file read + validate, no merge/list logic
    (that is a later slice's CLI concern).
"""

import os
import sys

import chess
import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

from lesson import (  # noqa: E402
    PREDICATES,
    BRIDGE_OBJECTIVES,
    DEFERRED_TYPES,
    validate_lesson,
)

KNIGHT_D5_FEN = "4k3/8/8/3N4/8/8/8/4K3 w - - 0 1"


def make_lesson(**overrides):
    """A well-formed board-and-pieces lesson (reach_square). Base fixture for
    validate_lesson tests — mutate one field per test to trigger one violation."""
    lesson = {
        "schema_version":  1,
        "id":              "knight-tour-1",
        "stage":           "board-and-pieces",
        "order":           1,
        "title":           "How the Knight Moves",
        "goal":            "Move your knight to the square f6 in at most three moves.",
        "narration_seed":  "The knight is the only piece that can jump over other pieces.",
        "start_fen":       KNIGHT_D5_FEN,
        "player_color":    "white",
        "objective":       {"type": "reach_square", "square": "f6", "piece": "N", "max_moves": 3},
        "allowed_pieces":  ["N"],
        "max_attempts":    3,
        "hints":           ["The knight always moves in an L-shape: two squares one way, then one square sideways."],
        "solution_text":   ["Move the knight from the square d5 to the square f6."],
        "solution_san":    ["Nf6"],
        "success_text":    "Well done — you found the square f6.",
        "failure_text":    "Not quite — try tracing the knight's L-shaped path again.",
    }
    lesson.update(overrides)
    return lesson


def make_bridge_lesson(**overrides):
    """A well-formed guided-play lesson (free_play bridge objective)."""
    lesson = make_lesson(
        id="free-play-1",
        stage="guided-play",
        title="Play a Short Game",
        goal="Play at least four moves against the coach, applying what you've learned.",
        narration_seed="Now it's time to put it all together in a short game.",
        start_fen=chess.Board().fen(),
        objective={"type": "free_play", "min_moves": 4},
        allowed_pieces=["P", "N", "B", "R", "Q", "K"],
        max_attempts=1,
        hints=["Think about piece safety before every move."],
        solution_text=["There is no single solution here — just keep playing."],
        solution_san=[],
        success_text="Great — you played a full opening sequence.",
        failure_text="Keep playing until you've made enough moves.",
    )
    lesson.update(overrides)
    return lesson


# ---------------------------------------------------------------------------
# 4a.1 — registry shape and disjointness (adjudication #1)
# ---------------------------------------------------------------------------
def test_predicates_registry_disjoint_and_exact():
    """PREDICATES, BRIDGE_OBJECTIVES, and DEFERRED_TYPES are three pairwise-
    disjoint registries — never one merged lookup table (adjudication #1)."""
    assert set(PREDICATES) == {
        "reach_square", "capture_square", "checkmate_in_1", "legal_moves_from_square",
    }
    assert all(callable(fn) for fn in PREDICATES.values())
    assert BRIDGE_OBJECTIVES == {"free_play"}
    assert DEFERRED_TYPES == {"checkmate_in_n", "avoid_capture"}
    assert not (set(PREDICATES) & BRIDGE_OBJECTIVES)
    assert not (set(PREDICATES) & DEFERRED_TYPES)
    assert not (BRIDGE_OBJECTIVES & DEFERRED_TYPES)
    # Structural proof: attempt (slice 4b) looks up PREDICATES[type] to dispatch.
    # free_play has no entry there, so it cannot be routed into attempt.
    assert "free_play" not in PREDICATES


# ---------------------------------------------------------------------------
# 4a.2 — deferred-type rejection, distinct from the unknown-type rejection
# ---------------------------------------------------------------------------
def test_validate_lesson_accepts_well_formed_lesson():
    """Baseline sanity check: the fixture itself is valid, so every mutation
    test below is proven to fail for the mutated reason, not a pre-existing one."""
    assert validate_lesson(make_lesson()) == []


def test_validate_deferred_type_rejected():
    """A deferred type (checkmate_in_n, avoid_capture) is rejected at load,
    and creates no lesson state — this slice ships no state-writing code at
    all, so 'no state created' holds structurally for every rejection path."""
    lesson = make_lesson(objective={"type": "checkmate_in_n", "n": 2})
    errors = validate_lesson(lesson)
    assert errors
    assert any("deferred" in e.lower() for e in errors)


def test_validate_unknown_type_rejected():
    """An unrecognized type must be rejected too, with a DIFFERENT message
    than the deferred-type case — a typo should not look like a roadmap item."""
    lesson = make_lesson(objective={"type": "not_a_real_predicate"})
    errors = validate_lesson(lesson)
    assert errors
    assert not any("deferred" in e.lower() for e in errors)


def test_validate_deferred_and_unknown_messages_are_distinct():
    """The two rejection messages must be textually different strings, not
    just two failures — asserting only that both fail proves nothing."""
    deferred_errors = validate_lesson(make_lesson(objective={"type": "avoid_capture"}))
    unknown_errors = validate_lesson(make_lesson(objective={"type": "not_a_real_predicate"}))
    deferred_msg = next(e for e in deferred_errors if "avoid_capture" in e)
    unknown_msg = next(e for e in unknown_errors if "not_a_real_predicate" in e)
    assert deferred_msg != unknown_msg


# ---------------------------------------------------------------------------
# 4a.3 — stage-constrained registry check (the other half of adjudication #1:
# a bridge objective and a verification predicate can never share a stage)
# ---------------------------------------------------------------------------
def test_validate_bridge_outside_guided_play_rejected():
    """A free_play bridge objective is only valid in stage guided-play."""
    lesson = make_lesson(objective={"type": "free_play", "min_moves": 4})
    errors = validate_lesson(lesson)
    assert errors
    assert any("free_play" in e and "guided-play" in e for e in errors)


def test_validate_bridge_objective_accepted_in_guided_play():
    """The SAME objective type is valid once the stage is guided-play."""
    assert validate_lesson(make_bridge_lesson()) == []


def test_validate_predicate_type_rejected_in_guided_play():
    """Mirror of the above: a verification predicate is rejected in
    guided-play — predicates and bridge objectives never share a stage."""
    lesson = make_bridge_lesson(objective={"type": "reach_square", "square": "f6", "piece": "N"})
    errors = validate_lesson(lesson)
    assert errors
    assert any("reach_square" in e and "guided-play" in e for e in errors)
