"""
lesson.py — Lesson definitions, strict validation, and objective-verification
predicates for the Learn Mode curriculum.

This module is a library only in this slice: it defines the two disjoint
objective registries (PREDICATES, BRIDGE_OBJECTIVES), the deferred-type
allowlist (DEFERRED_TYPES), and lesson-schema validation (validate_lesson)
covering required fields, stage validity, FEN/player-color consistency, and
the stage-constrained two-registry objective-type dispatch. It exposes no
command-line interface — list/show/start/attempt/hint/status are built on
top of this library in a later slice.

Deferred to slice 4a2 (review-budget split): real logic for the four
PREDICATES checkers (currently NotImplementedError stubs — see their
docstrings), the narration self-containment + cross-field occupancy checks
in validate_lesson, and the single-file loader.

Imported by that later slice's CLI. Do not run directly.
"""

import re

import chess

# ---------------------------------------------------------------------------
# Lesson state path (module-level constant so a later slice can bind it in
# code rather than accepting it as a user-typed --state argument — see D7-B).
# ---------------------------------------------------------------------------
LESSON_STATE_PATH = "~/.chess_coach/current_lesson.json"

# ---------------------------------------------------------------------------
# Curriculum stages, in progression order. Only the last stage carries bridge
# objectives; the first three carry verification predicates.
# ---------------------------------------------------------------------------
STAGES = ["board-and-pieces", "special-rules", "material-and-mate", "guided-play"]
BRIDGE_STAGE = "guided-play"


# ---------------------------------------------------------------------------
# Verification predicate checks — dispatched by the `attempt` command
# (slice 4b) via PREDICATES[objective["type"]]. Every checker shares one
# uniform keyword-only signature so dispatch never needs per-type branching.
#
# STUB NOTICE: real check logic is deferred to slice 4a2 (review-budget
# split — see sdd/learn-mode/apply-progress). Every stub raises
# NotImplementedError rather than returning a fake verdict: fail LOUD, never
# silently report a wrong pass/fail. Nothing in this slice calls them yet —
# the registry-shape test only asserts they are callable, not their result.
# ---------------------------------------------------------------------------
def check_reach_square(objective, *, board_before=None, move=None, board_after=None,
                        answered_squares=None, player_color=None):
    """Stub — implemented in 4a2. Will be satisfied when the learner's MOVE
    lands the designated piece on the target square (keyed off the move's
    destination, not mere occupancy)."""
    raise NotImplementedError("check_reach_square: real check logic lands in slice 4a2")


def check_capture_square(objective, *, board_before=None, move=None, board_after=None,
                          answered_squares=None, player_color=None):
    """Stub — implemented in 4a2. Will be satisfied when the learner's move
    captures the piece on the target square."""
    raise NotImplementedError("check_capture_square: real check logic lands in slice 4a2")


def check_checkmate_in_1(objective, *, board_before=None, move=None, board_after=None,
                          answered_squares=None, player_color=None):
    """Stub — implemented in 4a2. Will be satisfied only when the position
    after the learner's move is checkmate, ignoring move strength entirely."""
    raise NotImplementedError("check_checkmate_in_1: real check logic lands in slice 4a2")


def check_legal_moves_from_square(objective, *, board_before=None, move=None, board_after=None,
                                   answered_squares=None, player_color=None):
    """Stub — implemented in 4a2. Will be satisfied when the answered square
    set exactly equals the script-computed legal destination set for the
    piece on objective['square'] (an answer check, not a move check)."""
    raise NotImplementedError("check_legal_moves_from_square: real check logic lands in slice 4a2")


# ---------------------------------------------------------------------------
# Objective registries — TWO DISJOINT SETS (design D3 / adjudication #1).
#
# PREDICATES: verification predicates, dispatched by `attempt` (slice 4b).
# Exactly four types. Fail-closed: any objective type outside both
# PREDICATES and BRIDGE_OBJECTIVES is rejected at load by validate_lesson.
#
# BRIDGE_OBJECTIVES: NOT verification predicates. Never dispatched by
# `attempt` — evaluated only by `status` from len(moves_uci) >= min_moves.
# Deliberately a plain set of names, not a dict of checker functions: there
# is no checker to look up here, so `attempt` structurally cannot dispatch
# one even by mistake.
# ---------------------------------------------------------------------------
PREDICATES = {
    "reach_square":             check_reach_square,
    "capture_square":           check_capture_square,
    "checkmate_in_1":           check_checkmate_in_1,
    "legal_moves_from_square":  check_legal_moves_from_square,
}

BRIDGE_OBJECTIVES = {"free_play"}

# Recognized but not implemented yet. Rejected at load with a message
# distinct from the unknown-type error — the user must learn it is
# deferred, not that it is a typo.
DEFERRED_TYPES = {"checkmate_in_n", "avoid_capture"}

# Structural disjointness — fails LOUDLY at import time if ever violated,
# rather than silently letting one lookup table serve both roles.
assert not (set(PREDICATES) & BRIDGE_OBJECTIVES), \
    "PREDICATES and BRIDGE_OBJECTIVES must be disjoint"
assert not (set(PREDICATES) & DEFERRED_TYPES), \
    "PREDICATES and DEFERRED_TYPES must be disjoint"
assert not (BRIDGE_OBJECTIVES & DEFERRED_TYPES), \
    "BRIDGE_OBJECTIVES and DEFERRED_TYPES must be disjoint"


# ---------------------------------------------------------------------------
# Lesson schema v1 validation
# ---------------------------------------------------------------------------
REQUIRED_KEYS = [
    "schema_version", "id", "stage", "order", "title", "goal",
    "narration_seed", "start_fen", "player_color", "objective",
    "allowed_pieces", "max_attempts", "hints", "solution_text",
    "solution_san", "success_text", "failure_text",
]


def validate_lesson(lesson: dict) -> list[str]:
    """Validate a lesson definition dict against schema v1.

    Returns a list of human-readable error strings; an empty list means the
    lesson is valid. Never raises — a malformed lesson is reported, not
    crashed on, so a caller can surface every problem in one pass.
    """
    errors: list[str] = []

    for key in REQUIRED_KEYS:
        if key not in lesson:
            errors.append(f"Missing required field '{key}'.")
    if errors:
        return errors  # further checks would just KeyError on missing fields

    stage = lesson["stage"]
    if stage not in STAGES:
        errors.append(f"Unknown stage '{stage}'. Must be one of {STAGES}.")

    board = None
    try:
        board = chess.Board(lesson["start_fen"])
    except ValueError as e:
        errors.append(f"Invalid start_fen: {e}")

    player_color = lesson["player_color"]
    if player_color not in ("white", "black"):
        errors.append(f"player_color must be 'white' or 'black', got '{player_color}'.")
    elif board is not None:
        expected_turn = chess.WHITE if player_color == "white" else chess.BLACK
        if board.turn != expected_turn:
            errors.append(
                f"start_fen side-to-move does not match player_color '{player_color}'."
            )

    # Objective type must belong to exactly one of the two disjoint registries,
    # stage-constrained, or be explicitly deferred, or rejected as unknown
    # (fail-closed). Predicates and bridge objectives never share a stage.
    obj_type = lesson["objective"].get("type")
    if obj_type in PREDICATES:
        if stage == BRIDGE_STAGE:
            errors.append(
                f"Verification predicate '{obj_type}' is not allowed in stage "
                f"'{BRIDGE_STAGE}' — only bridge objectives are valid there."
            )
    elif obj_type in BRIDGE_OBJECTIVES:
        if stage != BRIDGE_STAGE:
            errors.append(
                f"Bridge objective '{obj_type}' is only allowed in stage "
                f"'{BRIDGE_STAGE}', not '{stage}'."
            )
    elif obj_type in DEFERRED_TYPES:
        errors.append(
            f"Objective type '{obj_type}' is recognized but not supported yet "
            f"(deferred)."
        )
    else:
        errors.append(
            f"Objective type '{obj_type}' is not a recognized predicate or "
            f"bridge objective type."
        )

    return errors
