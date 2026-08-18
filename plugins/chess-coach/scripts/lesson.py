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

Slice 4a2 implemented the four PREDICATES checker bodies, narration
self-containment, and cross-field FEN/objective occupancy checks. Deferred
to slice 4a3: the single-file loader (load_lesson_file).

Slice 4a3 implemented load_lesson_file and LessonValidationError below —
single-file read + validate only, no bundled/user-dir merge (that is
slice 4b's `list` command).

Imported by that later slice's CLI. Do not run directly.
"""

import json
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
# ---------------------------------------------------------------------------
def check_reach_square(objective, *, board_before=None, move=None, board_after=None,
                        answered_squares=None, player_color=None):
    """Satisfied when the MOVE ends on the target square — keyed off
    move.to_square, never mere occupancy (a piece already sitting there
    before this move must not count)."""
    target = chess.parse_square(objective["square"])
    reached = move is not None and move.to_square == target
    return reached, {"target_square": objective["square"], "reached": reached}


def check_capture_square(objective, *, board_before=None, move=None, board_after=None,
                          answered_squares=None, player_color=None):
    """Satisfied when the learner's move captures on the target square.

    En passant decision: `target` names the CAPTURING pawn's destination
    (move.to_square), not the captured pawn's square — those two differ
    only for en passant (e5xd6 e.p. removes the pawn on d5 but lands on
    d6). One uniform target-square rule for every capture type, instead
    of special-casing en passant's offset capture square.
    """
    target = chess.parse_square(objective["square"])
    captured = (
        board_before is not None and move is not None
        and move.to_square == target and board_before.is_capture(move)
    )
    return captured, {"target_square": objective["square"], "captured": captured}


def check_checkmate_in_1(objective, *, board_before=None, move=None, board_after=None,
                          answered_squares=None, player_color=None):
    """Satisfied ONLY on actual checkmate — move strength is irrelevant.
    Spec scenario 'Strong move that misses the goal': a materially
    winning move that fails to mate is NOT satisfied."""
    mated = board_after is not None and board_after.is_checkmate()
    detail = {
        "is_check": bool(board_after is not None and board_after.is_check()),
        "opponent_replies": (
            len(list(board_after.legal_moves)) if board_after is not None else None
        ),
    }
    return mated, detail


def check_legal_moves_from_square(objective, *, board_before=None, move=None, board_after=None,
                                   answered_squares=None, player_color=None):
    """Quiz check: the answered square SET must exactly equal the legal
    destination set from objective['square'] — not a subset, superset."""
    target = chess.parse_square(objective["square"])
    legal = {
        m.to_square for m in board_before.legal_moves if m.from_square == target
    } if board_before is not None else set()
    answered = set(answered_squares or [])
    detail = {
        "correct_count": len(answered & legal),
        "missing_count": len(legal - answered),
        "extra_count": len(answered - legal),
    }
    return answered == legal, detail


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
# Narration self-containment (spec: English Narration Contract / design F7).
# A bare coordinate ("e4") must read "the square e4" to stay self-contained
# when copy-pasted elsewhere. Lookbehind is case-insensitive on "S" only
# (sentence-initial "Square e4" also counts). Python `re` lookbehind must
# be FIXED-WIDTH, so only the word "square" directly before a coordinate
# is recognized — authors repeat "the square" per coordinate in a list
# ("the square d5 and the square f6"), rather than one plural "squares"
# covering later references in the sentence.
# ---------------------------------------------------------------------------
SQUARE_COORD_RE = re.compile(r"(?<![Ss]quare )[a-h][1-8]\b")

_NARRATION_TEXT_FIELDS = ("goal", "narration_seed", "success_text", "failure_text")
_NARRATION_LIST_FIELDS = ("hints", "solution_text")


def _bare_square_violations(lesson: dict) -> list[str]:
    """Return one error string per user-facing field containing a bare
    (un-phrased) square coordinate."""
    violations = []
    for field in _NARRATION_TEXT_FIELDS:
        if SQUARE_COORD_RE.search(lesson.get(field) or ""):
            violations.append(
                f"Field '{field}' has a bare square coordinate — phrase it "
                f"as 'the square e4', never a naked coordinate."
            )
    for field in _NARRATION_LIST_FIELDS:
        for i, text in enumerate(lesson.get(field) or []):
            if SQUARE_COORD_RE.search(text):
                violations.append(
                    f"Field '{field}[{i}]' has a bare square coordinate — "
                    f"phrase it as 'the square e4', never a naked coordinate."
                )
    return violations


# ---------------------------------------------------------------------------
# Cross-field: an objective's square(s)/piece must match start_fen
# occupancy, or the lesson is unplayable — rejected at load, not mid-drill.
# checkmate_in_1 names no square, so it is never dispatched here.
# ---------------------------------------------------------------------------
def _square_occupancy_errors(objective: dict, obj_type: str, board: "chess.Board",
                              learner_color: bool) -> list[str]:
    errors: list[str] = []

    def _square_or_none(name):
        try:
            return chess.parse_square(name)
        except ValueError:
            errors.append(f"Objective square '{name}' is not a valid square name.")
            return None

    if obj_type == "capture_square":
        sq = _square_or_none(objective.get("square"))
        if sq is not None:
            piece = board.piece_at(sq)
            if piece is None or piece.color == learner_color:
                errors.append(
                    f"Objective capture_square target '{objective['square']}' "
                    f"must hold an opponent piece in start_fen."
                )
    elif obj_type == "reach_square":
        piece_symbol = objective.get("piece")
        if piece_symbol:
            try:
                piece_type = chess.PIECE_SYMBOLS.index(piece_symbol.lower())
            except ValueError:
                piece_type = None
            has_piece = piece_type is not None and any(
                p.piece_type == piece_type and p.color == learner_color
                for p in board.piece_map().values()
            )
            if not has_piece:
                errors.append(
                    f"Objective reach_square names piece '{piece_symbol}' but "
                    f"no such learner piece exists in start_fen."
                )
    elif obj_type == "legal_moves_from_square":
        sq = _square_or_none(objective.get("square"))
        if sq is not None:
            piece = board.piece_at(sq)
            if piece is None or piece.color != learner_color:
                errors.append(
                    f"Objective legal_moves_from_square square "
                    f"'{objective['square']}' must hold a learner piece in "
                    f"start_fen."
                )

    return errors


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

    errors.extend(_bare_square_violations(lesson))

    stage = lesson["stage"]
    if stage not in STAGES:
        errors.append(f"Unknown stage '{stage}'. Must be one of {STAGES}.")

    board = None
    try:
        board = chess.Board(lesson["start_fen"])
    except ValueError as e:
        errors.append(f"Invalid start_fen: {e}")

    player_color = lesson["player_color"]
    learner_color = {"white": chess.WHITE, "black": chess.BLACK}.get(player_color)
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
        elif board is not None and learner_color is not None:
            errors.extend(
                _square_occupancy_errors(lesson["objective"], obj_type, board, learner_color)
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


# ---------------------------------------------------------------------------
# Single-file loader (slice 4a3). No merge/list logic here — that belongs
# to the `list` command (slice 4b), which reads bundled + user-dir lessons
# and merges by id. Mirrors engine.py's load_state: file I/O and JSON-decode
# errors propagate as-is, unwrapped.
# ---------------------------------------------------------------------------
class LessonValidationError(Exception):
    """Raised by load_lesson_file when a lesson fails validate_lesson.

    Carries the FULL list of validation error strings via .errors — a
    caller needs every problem in one pass, not just the first message.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def load_lesson_file(path: str) -> dict:
    """Read a single lesson JSON file from disk and return it validated.

    Raises LessonValidationError if the lesson fails validate_lesson —
    fails loudly rather than returning a partially-usable lesson.
    """
    with open(path) as f:
        lesson = json.load(f)
    errors = validate_lesson(lesson)
    if errors:
        raise LessonValidationError(errors)
    return lesson
