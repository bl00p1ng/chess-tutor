"""
test_lesson.py — Lesson library: registries, validate_lesson, and the four
verification predicate checks (plugins/chess-coach/scripts/lesson.py).

lesson.py ships as an inert library in this slice: no CLI, no argparse, no
command dispatch. Every test below imports and calls library functions
directly (pure-function style, matching test_render_width.py) — there is
nothing to shell out to yet.

Covers: PREDICATES/BRIDGE_OBJECTIVES/DEFERRED_TYPES disjointness
(adjudication #1); validate_lesson required fields, stage-constrained
dispatch, narration self-containment (F7), cross-field FEN/objective
occupancy; the four PREDICATES checker functions. load_lesson_file lands
in slice 4a3.
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
    check_reach_square,
    check_capture_square,
    check_checkmate_in_1,
    check_legal_moves_from_square,
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


# ---------------------------------------------------------------------------
# 4a2.1a — narration self-containment: bare square coordinates rejected
# ---------------------------------------------------------------------------
def test_validate_bare_square_coordinate_rejected():
    """A naked coordinate like 'e4' (no 'square' before it) fails loudly —
    the string would be meaningless if copy-pasted into a separate tutor
    session without board context."""
    lesson = make_lesson(goal="Move your knight to e4 in three moves.")
    errors = validate_lesson(lesson)
    assert errors
    assert any("goal" in e and "bare square" in e for e in errors)


def test_validate_square_phrasing_allows_repeated_square_word():
    """Near-miss that must NOT be rejected: two coordinates in one field,
    each properly phrased with its own 'the square', prove the regex does
    not blanket-reject every bare-looking coordinate — only ones missing
    the phrasing word directly before them."""
    lesson = make_lesson(
        goal="Move your pawn to the square d5 and to the square f6."
    )
    assert validate_lesson(lesson) == []


# ---------------------------------------------------------------------------
# 4a2.1b — FEN <-> objective occupancy cross-field checks
# ---------------------------------------------------------------------------
def test_validate_capture_square_target_must_hold_opponent_piece():
    """capture_square's target square must be occupied by an OPPONENT
    piece in start_fen — an empty or own-piece target is unplayable."""
    lesson = make_lesson(
        start_fen="4k3/8/8/8/4P3/8/8/4K3 w - - 0 1",  # d5 is empty
        objective={"type": "capture_square", "square": "d5"},
    )
    errors = validate_lesson(lesson)
    assert errors
    assert any("capture_square" in e and "opponent piece" in e for e in errors)


def test_validate_reach_square_piece_must_exist_on_board():
    """reach_square's named piece must actually exist for the learner on
    start_fen, or the objective can never be satisfied."""
    lesson = make_lesson(
        start_fen="4k3/8/8/8/8/8/8/4K3 w - - 0 1",  # no knight anywhere
        objective={"type": "reach_square", "square": "f6", "piece": "N"},
    )
    errors = validate_lesson(lesson)
    assert errors
    assert any("reach_square" in e and "no such learner piece" in e for e in errors)


def test_validate_legal_moves_square_must_hold_learner_piece():
    """legal_moves_from_square's square must hold a learner piece — empty
    or opponent-occupied makes the quiz unanswerable."""
    lesson = make_lesson(
        objective={"type": "legal_moves_from_square", "square": "e5"},  # empty
    )
    errors = validate_lesson(lesson)
    assert errors
    assert any("legal_moves_from_square" in e and "learner piece" in e for e in errors)


def test_validate_capture_and_legal_moves_accept_valid_occupancy():
    """Positive path (triangulation): correctly-occupied targets are
    accepted, proving the checks read real board state instead of
    always rejecting."""
    capture_lesson = make_lesson(
        start_fen="4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1",  # d5 holds a black pawn
        objective={"type": "capture_square", "square": "d5"},
    )
    assert validate_lesson(capture_lesson) == []

    quiz_lesson = make_lesson(
        objective={"type": "legal_moves_from_square", "square": "d5"},  # holds the white knight
    )
    assert validate_lesson(quiz_lesson) == []


# ---------------------------------------------------------------------------
# 4a2.2 — check_checkmate_in_1 (spec scenario: "Strong move that misses
# the goal" — engine evaluation is irrelevant, only actual mate counts)
# ---------------------------------------------------------------------------
CHECKMATE_FIXTURE_FEN = "6k1/5ppp/8/8/7r/8/8/R3K2Q w - - 0 1"


def test_checkmate_in_1_ignores_move_strength():
    """A move that wins a whole rook (objectively strong by any engine
    evaluation) but does not deliver mate must NOT be satisfied — this is
    the entire point of the predicate: strength is irrelevant."""
    board_before = chess.Board(CHECKMATE_FIXTURE_FEN)
    strong_move = chess.Move.from_uci("h1h4")  # Qxh4, wins a rook, no check
    board_after = board_before.copy()
    board_after.push(strong_move)
    assert not board_after.is_checkmate()  # fixture sanity

    satisfied, _ = check_checkmate_in_1(
        {"type": "checkmate_in_1"},
        board_before=board_before, move=strong_move, board_after=board_after,
    )
    assert satisfied is False


def test_checkmate_in_1_satisfied_on_actual_mate():
    """Triangulation: the same fixture, a genuine mating move IS satisfied."""
    board_before = chess.Board(CHECKMATE_FIXTURE_FEN)
    mate_move = chess.Move.from_uci("a1a8")  # Ra8#, back-rank mate
    board_after = board_before.copy()
    board_after.push(mate_move)
    assert board_after.is_checkmate()  # fixture sanity

    satisfied, detail = check_checkmate_in_1(
        {"type": "checkmate_in_1"},
        board_before=board_before, move=mate_move, board_after=board_after,
    )
    assert satisfied is True
    assert detail["opponent_replies"] == 0


# ---------------------------------------------------------------------------
# 4a2.3 — check_reach_square (spec scenario: "Satisfying move")
# ---------------------------------------------------------------------------
def test_reach_square_satisfied():
    board_before = chess.Board(KNIGHT_D5_FEN)
    move = chess.Move.from_uci("d5f6")
    satisfied, detail = check_reach_square(
        {"type": "reach_square", "square": "f6"},
        board_before=board_before, move=move,
    )
    assert satisfied is True
    assert detail["reached"] is True


def test_reach_square_stale_occupancy_not_satisfied():
    """Triangulation: a piece already sitting on the target square BEFORE
    this move must not count — only move.to_square matters, never mere
    occupancy of the target square."""
    board_before = chess.Board("3k4/8/5N2/8/8/8/8/4K3 w - - 0 1")  # N already on f6
    move = chess.Move.from_uci("e1e2")  # unrelated king shuffle
    satisfied, _ = check_reach_square(
        {"type": "reach_square", "square": "f6"},
        board_before=board_before, move=move,
    )
    assert satisfied is False


# ---------------------------------------------------------------------------
# 4a2.4 — check_legal_moves_from_square (spec scenario: "Wrong quiz answer")
# ---------------------------------------------------------------------------
def test_legal_moves_from_square_wrong_answer():
    board_before = chess.Board(KNIGHT_D5_FEN)
    answered = {chess.F6, chess.B4}  # missing 6 of the 8 real destinations
    satisfied, detail = check_legal_moves_from_square(
        {"type": "legal_moves_from_square", "square": "d5"},
        board_before=board_before, answered_squares=answered,
    )
    assert satisfied is False
    assert detail["missing_count"] == 6
    assert detail["extra_count"] == 0


def test_legal_moves_from_square_exact_match_satisfied():
    """Triangulation: the exact legal-destination set is satisfied."""
    board_before = chess.Board(KNIGHT_D5_FEN)
    legal = {m.to_square for m in board_before.legal_moves if m.from_square == chess.D5}
    satisfied, detail = check_legal_moves_from_square(
        {"type": "legal_moves_from_square", "square": "d5"},
        board_before=board_before, answered_squares=legal,
    )
    assert satisfied is True
    assert detail["missing_count"] == 0
    assert detail["extra_count"] == 0


def test_legal_moves_from_square_superset_not_satisfied():
    """Gate-finding remediation: a superset answer — every correct
    destination PLUS one extra illegal square — must NOT be satisfied.
    This proves exact-set equality, not merely a correct subset; only the
    subset and exact-match cases were covered before this test."""
    board_before = chess.Board(KNIGHT_D5_FEN)
    legal = {m.to_square for m in board_before.legal_moves if m.from_square == chess.D5}
    answered = legal | {chess.A1}  # every correct square, plus one extra
    satisfied, detail = check_legal_moves_from_square(
        {"type": "legal_moves_from_square", "square": "d5"},
        board_before=board_before, answered_squares=answered,
    )
    assert satisfied is False
    assert detail["missing_count"] == 0
    assert detail["extra_count"] == 1


# ---------------------------------------------------------------------------
# 4a2.5 — check_capture_square
# ---------------------------------------------------------------------------
def test_capture_square_satisfied_on_capture():
    board_before = chess.Board("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1")
    move = chess.Move.from_uci("e4d5")
    satisfied, detail = check_capture_square(
        {"type": "capture_square", "square": "d5"},
        board_before=board_before, move=move,
    )
    assert satisfied is True
    assert detail["captured"] is True


def test_capture_square_not_satisfied_non_capture():
    """Triangulation: a legal, non-capturing move to a different square."""
    board_before = chess.Board("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1")
    move = chess.Move.from_uci("e1e2")  # king shuffle, no capture
    satisfied, _ = check_capture_square(
        {"type": "capture_square", "square": "d5"},
        board_before=board_before, move=move,
    )
    assert satisfied is False


# ---------------------------------------------------------------------------
# Gate-finding remediation (slice 4a2 validator fail, closed in 4a3): the
# en-passant regression test below was written, GREEN, then cut for review
# budget in 4a2 — restored here per the tracked follow-up. No production
# code changes: check_capture_square already implements the documented
# en-passant convention correctly; these prove it with executable coverage.
# ---------------------------------------------------------------------------
EN_PASSANT_FEN = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"


def test_capture_square_satisfied_on_en_passant_destination():
    """En-passant target names the CAPTURING pawn's destination square
    (d6 for e5xd6 e.p.) — that convention, documented in the checker's
    docstring, is satisfied here with an executable assertion."""
    board_before = chess.Board(EN_PASSANT_FEN)
    move = chess.Move.from_uci("e5d6")
    assert board_before.is_en_passant(move)  # fixture sanity

    satisfied, detail = check_capture_square(
        {"type": "capture_square", "square": "d6"},
        board_before=board_before, move=move,
    )
    assert satisfied is True
    assert detail["captured"] is True


def test_capture_square_en_passant_captured_square_not_satisfied():
    """Triangulation: the CAPTURED pawn's square (d5) must NOT satisfy —
    only the capturing pawn's destination (d6) counts. This is the exact
    regression this test guards: a target-square convention flip between
    d5 and d6 would silently break en-passant lessons."""
    board_before = chess.Board(EN_PASSANT_FEN)
    move = chess.Move.from_uci("e5d6")

    satisfied, detail = check_capture_square(
        {"type": "capture_square", "square": "d5"},
        board_before=board_before, move=move,
    )
    assert satisfied is False
    assert detail["captured"] is False
