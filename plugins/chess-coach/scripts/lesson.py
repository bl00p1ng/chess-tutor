"""
lesson.py — Lesson definitions, strict validation, and objective-verification
predicates for the Learn Mode curriculum.

This module is a library only in this slice: it defines the two disjoint
objective registries (PREDICATES, BRIDGE_OBJECTIVES), the deferred-type
allowlist (DEFERRED_TYPES), and lesson-schema validation (validate_lesson)
covering required fields, stage validity, FEN/player-color consistency, and
the stage-constrained two-registry objective-type dispatch, and — as of
slice 4b-i — a CLI exposing list/show/start. Slice 4b-ii adds attempt's
reject-before-dispatch gate (adjudication #1: illegal move / bridge
redirect / fail-closed unknown type) and status's resume/next-lesson
report; attempt's accept path (predicate dispatch, D2 rebase, budget,
reset) and hint land in slice 4b-iii.

Slice 4a2 implemented the four PREDICATES checker bodies, narration
self-containment, and cross-field FEN/objective occupancy checks. Deferred
to slice 4a3: the single-file loader (load_lesson_file).

Slice 4a3 implemented load_lesson_file and LessonValidationError below —
single-file read + validate only, no bundled/user-dir merge (that is
slice 4b's `list` command).

Runnable directly as a CLI (list/show/start/attempt/status) or imported
as a library.
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

import chess

from common import board_from_state
from engine import parse_move

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


# ---------------------------------------------------------------------------
# CLI surface (slice 4b-i): list / show / start. attempt/hint/status are a
# later slice (4b-ii) — see design's command-surface table. Every home-
# relative default below stays an UNEXPANDED string, expanded only at call
# time (inside main() or the function that reads it) — never baked at
# import time — so a test's HOME override always takes effect.
# ---------------------------------------------------------------------------
GAME_STATE_PATH = "~/.chess_coach/current_game.json"
LEARNING_STATE_PATH = "~/.chess_coach/learning.json"
BUNDLED_LESSONS_DIR_DEFAULT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "lessons")
)
USER_LESSONS_DIR_DEFAULT = "~/.chess_coach/lessons"


def _refuse_game_state_path(state_path: str) -> dict | None:
    """D7-E: refuse a --state that resolves to the live game file, however
    it is spelled (symlink, relative '..' segments, or '~' expansion) —
    realpath comparison, never a literal string match, so no path form can
    slip through. A marker key inside the lesson file cannot catch this: an
    omitted --state means the game file is never opened in the first place."""
    resolved_state = os.path.realpath(os.path.expanduser(state_path))
    resolved_game = os.path.realpath(os.path.expanduser(GAME_STATE_PATH))
    if resolved_state == resolved_game:
        return {
            "ok": False,
            "error": (
                "Refusing to use the in-progress game file as lesson state. "
                "Lesson progress and the active game must stay in separate files."
            ),
        }
    return None


def load_learning_progress() -> dict:
    """Read learning.json, defaulting to an empty progress record when the
    file does not exist yet (first-ever lesson session)."""
    path = os.path.expanduser(LEARNING_STATE_PATH)
    if not os.path.exists(path):
        return {"schema_version": 1, "completed": {}, "last_lesson_id": None}
    with open(path) as f:
        return json.load(f)


def _curriculum_order(lessons: dict) -> list:
    """Sort lessons into one global curriculum sequence: stage index first,
    then in-stage order, then id as a final tiebreaker."""
    return sorted(
        lessons.values(),
        key=lambda l: (STAGES.index(l["stage"]), l["order"], l["id"]),
    )


def _gating_info(ordered: list, completed_ids: set) -> tuple:
    """Linear progression (F5): the next selectable NEW lesson is the first
    uncompleted one in curriculum order; every uncompleted lesson AFTER it
    is locked. Completion — never position alone — decides lock status, so
    an already-completed lesson is never locked, wherever it falls."""
    next_index = next(
        (i for i, lesson in enumerate(ordered) if lesson["id"] not in completed_ids),
        None,
    )
    locks = {
        lesson["id"]: (
            lesson["id"] not in completed_ids
            and next_index is not None
            and i > next_index
        )
        for i, lesson in enumerate(ordered)
    }
    next_id = ordered[next_index]["id"] if next_index is not None else None
    return next_id, locks


def _collect_lessons(bundled_dir: str, user_dir: str) -> tuple:
    """Load every *.json lesson from bundled_dir then user_dir — user wins
    on id collision (mirrors persona.py's load order). One invalid file is
    reported, never raised: a single bad lesson must not hide the rest of
    the curriculum."""
    lessons: dict = {}
    invalid: list = []
    for directory in [bundled_dir, user_dir]:
        for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
            try:
                lesson = load_lesson_file(path)
            except (LessonValidationError, OSError, json.JSONDecodeError) as e:
                invalid.append({"file": path, "error": str(e)})
                continue
            lessons[lesson["id"]] = lesson
    return lessons, invalid


def cmd_list(args) -> dict:
    lessons, invalid = _collect_lessons(args.bundled_dir, args.user_dir)
    completed_ids = set(load_learning_progress().get("completed", {}))
    ordered = _curriculum_order(lessons)
    _, locks = _gating_info(ordered, completed_ids)
    summaries = [
        {
            "id": lesson["id"], "stage": lesson["stage"], "order": lesson["order"],
            "title": lesson["title"], "completed": lesson["id"] in completed_ids,
            "locked": locks[lesson["id"]],
        }
        for lesson in ordered
    ]
    return {"ok": True, "lessons": summaries, "invalid": invalid}


def cmd_show(args) -> dict:
    lessons, _ = _collect_lessons(args.bundled_dir, args.user_dir)
    lesson = lessons.get(args.id)
    if lesson is None:
        return {"ok": False, "error": f"Lesson '{args.id}' not found."}
    return {"ok": True, "lesson": lesson}


def _save_lesson_state(state: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def cmd_start(args) -> dict:
    refusal = _refuse_game_state_path(args.state)
    if refusal:
        return refusal

    lessons, _ = _collect_lessons(args.bundled_dir, args.user_dir)
    lesson = lessons.get(args.id)
    if lesson is None:
        return {"ok": False, "error": f"Lesson '{args.id}' not found."}

    completed_ids = set(load_learning_progress().get("completed", {}))
    ordered = _curriculum_order(lessons)
    next_id, locks = _gating_info(ordered, completed_ids)
    if locks.get(args.id):
        return {
            "ok": False,
            "error": f"Lesson '{args.id}' is locked — complete earlier lessons first.",
            "next_lesson_id": next_id,
        }

    board = chess.Board(lesson["start_fen"])
    learner_color = lesson["player_color"]
    other_color = "black" if learner_color == "white" else "white"
    state_path = os.path.expanduser(args.state)
    state = {
        "color": learner_color,
        "player_name": "learner",
        "players": {learner_color: "learner", other_color: "ai"},
        "level": "beginner",
        "mode": "lesson",
        "moves_uci": [], "moves_san": [], "move_records": [],
        "move_count": 0, "result": None, "opening": None,
        "start_fen": lesson["start_fen"],
        "lesson": {
            "definition": lesson, "moves_history": [],
            "moves_used": 0, "attempts_used": 0, "hints_used": 0,
            "result": None,
        },
    }
    _save_lesson_state(state, state_path)

    return {
        "ok": True,
        "lesson_id": lesson["id"], "title": lesson["title"],
        "stage": lesson["stage"], "goal": lesson["goal"],
        "narration_seed": lesson["narration_seed"],
        "fen": board.fen(), "player_color": learner_color,
        "objective": lesson["objective"],
        "allowed_pieces": lesson["allowed_pieces"],
        "max_moves": lesson["objective"].get("max_moves"),
        "max_attempts": lesson["max_attempts"],
        "hints_available": len(lesson["hints"]),
        "state_file": state_path,
    }


def _load_lesson_state(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _rebase(board_after: "chess.Board", player_color: bool) -> tuple:
    """D2: after a non-terminal accepted move, rewrite the position so the
    learner is to move again (no opponent in a solo drill) with no pending
    en passant capture. Fails loudly — returns an error instead of a FEN —
    if the flip would leave the IDLE opponent king illegally in check: a
    malformed-lesson condition, not a normal user-facing rejection."""
    rebased = board_after.copy()
    rebased.turn = player_color
    rebased.ep_square = None
    idle_king = rebased.king(not player_color)
    if idle_king is not None and rebased.is_attacked_by(player_color, idle_king):
        return None, "This lesson's setup is invalid after that move."
    return rebased.fen(), None


def _record_completion(lesson_id: str, attempts_used: int, hints_used: int) -> None:
    """Record a solved lesson into learning.json (design: 'no separate
    command'). hints_used is stored for informational display only — it
    never fed the gate that got us here (spec: Hints Never Gate)."""
    path = os.path.expanduser(LEARNING_STATE_PATH)
    progress = load_learning_progress()
    progress["completed"][lesson_id] = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "attempts": attempts_used,
        "hints_used": hints_used,
    }
    progress["last_lesson_id"] = lesson_id
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


def _attempt_move(state: dict, lesson_block: dict, definition: dict, objective: dict,
                   obj_type: str, board_before: "chess.Board", move: "chess.Move",
                   state_path: str) -> dict:
    """Evaluate an accepted (chess-legal) move against its PREDICATES
    checker and apply the outcome: solved (+learning.json write), a D2
    rebase for a non-terminal miss, or a reset once the move budget for
    this attempt is exhausted (itself terminal as 'failed' once attempts
    are exhausted too). move_uci/move_san are appended to the state's
    top-level lists BEFORE the solved/rebase/reset branch below so the
    branch's own clearing (or, on solve, deliberate non-clearing) is
    provably non-vacuous rather than clearing an already-empty list."""
    move_san = board_before.san(move)
    move_uci = move.uci()
    board_after = board_before.copy()
    board_after.push(move)

    player_color = chess.WHITE if definition["player_color"] == "white" else chess.BLACK
    satisfied, detail = PREDICATES[obj_type](
        objective, board_before=board_before, move=move, board_after=board_after,
        player_color=player_color,
    )

    state["moves_uci"].append(move_uci)
    state["moves_san"].append(move_san)
    lesson_block["moves_used"] += 1
    max_moves = objective.get("max_moves", 1)
    max_attempts = definition["max_attempts"]

    response = {
        "ok": True, "accepted": True,
        "move_san": move_san, "move_uci": move_uci,
        "detail": detail, "goal": definition["goal"],
    }

    if satisfied:
        # Terminal: no more moves needed, so no rebase — moves_uci/
        # moves_san stay populated with this winning move, which keeps
        # board_from_state(state) consistent with the reported fen below.
        lesson_block["result"] = "solved"
        response["result"] = "solved"
        response["fen"] = board_after.fen()
        response["success_text"] = definition["success_text"]
        _record_completion(definition["id"], lesson_block["attempts_used"], lesson_block["hints_used"])
    elif lesson_block["moves_used"] < max_moves:
        rebased_fen, guard_error = _rebase(board_after, player_color)
        if guard_error:
            # Malformed lesson — fail loudly, save nothing (D2 guard).
            return {"ok": False, "error": guard_error}
        state["start_fen"] = rebased_fen
        state["moves_uci"] = []
        state["moves_san"] = []
        response["result"] = "in_progress"
        response["fen"] = rebased_fen
        response["position_reset"] = False
    else:
        # Move budget for this attempt is exhausted — reset to the
        # lesson's ORIGINAL fen (already load-time validated), never a
        # rebased one, and count the attempt.
        lesson_block["attempts_used"] += 1
        lesson_block["moves_used"] = 0
        state["start_fen"] = definition["start_fen"]
        state["moves_uci"] = []
        state["moves_san"] = []
        response["position_reset"] = True
        response["fen"] = definition["start_fen"]
        if lesson_block["attempts_used"] >= max_attempts:
            lesson_block["result"] = "failed"
            response["result"] = "failed"
            response["solution_text"] = definition["solution_text"]
            response["failure_text"] = definition["failure_text"]
        else:
            response["result"] = "in_progress"

    response["moves_used"] = lesson_block["moves_used"]
    response["moves_remaining"] = max(0, max_moves - lesson_block["moves_used"])
    response["attempts_used"] = lesson_block["attempts_used"]
    response["attempts_remaining"] = max(0, max_attempts - lesson_block["attempts_used"])

    _save_lesson_state(state, state_path)
    return response


def _parse_squares(squares_arg: str) -> tuple:
    """Parse a comma-separated square list ("d5,e5,f6") into a set of
    chess square indices. Returns (squares, error) — mirroring parse_move's
    (move, err) convention — so a malformed square name is reported
    instead of raising and crashing the CLI."""
    squares = set()
    for token in squares_arg.split(","):
        name = token.strip()
        if not name:
            continue
        try:
            squares.add(chess.parse_square(name))
        except ValueError:
            return None, f"'{name}' is not a valid square name."
    return squares, None


def _attempt_quiz(state: dict, lesson_block: dict, definition: dict, objective: dict,
                   board_before: "chess.Board", answered_squares: set, state_path: str) -> dict:
    """Evaluate a legal_moves_from_square quiz submission (task 4b.9). The
    quiz has its OWN budget model, distinct from a move-type attempt:
    every submission — right or wrong — consumes ONE unit of attempts_used
    directly. No moves_used, no D2 rebase, no idle-king guard, no position
    reset — no chess move is made, only an answer over the unchanged
    position. Design's detail contract: full square sets are disclosed
    only on a terminal result (solved/failed) — never mid-quiz, so the
    checker's own counts-only detail is left untouched and the sets are
    attached here, gated on the terminal branch alone."""
    satisfied, detail = check_legal_moves_from_square(
        objective, board_before=board_before, answered_squares=answered_squares,
    )

    max_attempts = definition["max_attempts"]
    response = {
        "ok": True, "accepted": True,
        "detail": detail, "goal": definition["goal"],
        "fen": board_before.fen(),
    }

    if satisfied:
        lesson_block["result"] = "solved"
        response["result"] = "solved"
        response["success_text"] = definition["success_text"]
        _record_completion(definition["id"], lesson_block["attempts_used"], lesson_block["hints_used"])
    else:
        lesson_block["attempts_used"] += 1
        if lesson_block["attempts_used"] >= max_attempts:
            lesson_block["result"] = "failed"
            response["result"] = "failed"
            response["solution_text"] = definition["solution_text"]
            response["failure_text"] = definition["failure_text"]
        else:
            response["result"] = "in_progress"

    if response["result"] in ("solved", "failed"):
        target = chess.parse_square(objective["square"])
        legal = {m.to_square for m in board_before.legal_moves if m.from_square == target}
        detail["legal_squares"] = sorted(chess.square_name(sq) for sq in legal)
        detail["answered_squares"] = sorted(chess.square_name(sq) for sq in answered_squares)

    response["attempts_used"] = lesson_block["attempts_used"]
    response["attempts_remaining"] = max(0, max_attempts - lesson_block["attempts_used"])

    _save_lesson_state(state, state_path)
    return response


def cmd_attempt(args) -> dict:
    """Reject-before-dispatch gate (tasks 4b.1/4b.2 — adjudication #1's
    dispatch boundary), then the accept path: predicate dispatch (task
    4b.7, move-type predicates; task 4b.9, the legal_moves_from_square
    quiz), D2 post-move rebase with the idle-king legality guard,
    move/attempt budgets, reset-on-exhaustion, and the solved branch."""
    refusal = _refuse_game_state_path(args.state)
    if refusal:
        return refusal

    state = _load_lesson_state(args.state)
    lesson_block = state["lesson"]
    definition = lesson_block["definition"]
    objective = definition["objective"]
    obj_type = objective.get("type")

    # Adjudication #1: attempt dispatches PREDICATES only, and never a
    # bridge objective (spec: "Attempt does not evaluate bridge objectives").
    # This check stays ahead of every predicate reference below, including
    # the quiz arg-kind check that follows.
    if obj_type in BRIDGE_OBJECTIVES:
        return {
            "ok": True, "accepted": False,
            "reason": (
                "This lesson is a free-play bridge, not a scored drill. "
                "Use the bridge commands to play moves instead."
            ),
        }
    # legal_moves_from_square is a genuine PREDICATES member, dispatched
    # through --squares rather than --move (task 4b.9). A --move (or no
    # answer at all) submitted here is an arg-kind mismatch, not a move to
    # evaluate — reject cleanly, wording distinct from the bridge redirect.
    if obj_type == "legal_moves_from_square":
        squares_arg = getattr(args, "squares", None)
        if not squares_arg:
            return {
                "ok": True, "accepted": False,
                "reason": (
                    "This lesson is a squares quiz — answer it with the "
                    "square names, not a chess move."
                ),
            }
        answered_squares, parse_err = _parse_squares(squares_arg)
        if parse_err:
            return {"ok": False, "error": parse_err}
        board_before = board_from_state(state)
        return _attempt_quiz(state, lesson_block, definition, objective,
                              board_before, answered_squares, args.state)
    if obj_type not in PREDICATES:
        # Fail-closed: validate_lesson should already refuse this at load
        # time; this is the defensive last line inside attempt itself.
        return {"ok": False, "error": f"Objective type '{obj_type}' is not a recognized predicate."}

    # Every remaining PREDICATES member is move-type. A --squares answer
    # (or a missing --move) is the mirror arg-kind mismatch — reject
    # cleanly rather than crash on a None move (args carries no .move
    # attribute at all when only --squares was given).
    move_arg = getattr(args, "move", None)
    if not move_arg:
        return {
            "ok": True, "accepted": False,
            "reason": "This lesson expects a chess move, not a list of squares.",
        }

    board_before = board_from_state(state)
    move, err = parse_move(move_arg, board_before)
    if err:
        # Chess-illegal move: rejected BEFORE predicate dispatch, no budget
        # consumed (spec scenario: Illegal move).
        return {
            "ok": True, "accepted": False,
            "reason": "That move is not legal in the current position.",
        }

    return _attempt_move(state, lesson_block, definition, objective, obj_type,
                          board_before, move, args.state)


def cmd_hint(args) -> dict:
    """Records hint usage as informational data only (spec: Hints Never
    Gate) — never blocks, delays, or alters completion (task 4b.3)."""
    refusal = _refuse_game_state_path(args.state)
    if refusal:
        return refusal

    state = _load_lesson_state(args.state)
    lesson_block = state["lesson"]
    hints = lesson_block["definition"]["hints"]
    if not hints:
        return {"ok": False, "error": "This lesson has no hints to give."}

    index = min(lesson_block["hints_used"], len(hints) - 1)
    hint_text = hints[index]
    lesson_block["hints_used"] += 1
    _save_lesson_state(state, args.state)

    return {
        "ok": True, "hint": hint_text,
        "hints_used": lesson_block["hints_used"],
        "hints_remaining": max(0, len(hints) - lesson_block["hints_used"]),
    }


def _stage_summary(ordered: list, completed_ids: set) -> dict:
    summary: dict = {}
    for lesson in ordered:
        entry = summary.setdefault(lesson["stage"], {"total": 0, "completed": 0})
        entry["total"] += 1
        if lesson["id"] in completed_ids:
            entry["completed"] += 1
    return summary


def cmd_status(args) -> dict:
    refusal = _refuse_game_state_path(args.state)
    if refusal:
        return refusal

    # The resume default (spec: Resume Offer) — an in-progress drill (no
    # terminal result yet) from a prior session, if one exists. Reads
    # whatever moves_used/attempts_used/result already sit in the state
    # file; independent of how attempt got them there (4b-iii).
    active = None
    if os.path.exists(args.state):
        state = _load_lesson_state(args.state)
        lesson_block = state.get("lesson")
        if lesson_block and lesson_block.get("result") is None:
            definition = lesson_block["definition"]
            board = board_from_state(state)
            active = {
                "lesson_id": definition["id"], "title": definition["title"],
                "goal": definition["goal"], "fen": board.fen(),
                "moves_used": lesson_block["moves_used"],
                "attempts_used": lesson_block["attempts_used"],
                "result": lesson_block["result"],
            }

    progress = load_learning_progress()
    completed_ids = set(progress.get("completed", {}))
    lessons, _ = _collect_lessons(args.bundled_dir, args.user_dir)
    ordered = _curriculum_order(lessons)
    next_id, _ = _gating_info(ordered, completed_ids)
    next_lesson = None
    if next_id is not None:
        nxt = lessons[next_id]
        next_lesson = {"id": nxt["id"], "title": nxt["title"], "stage": nxt["stage"]}

    return {
        "ok": True, "active": active,
        "completed_ids": sorted(completed_ids),
        "next_lesson": next_lesson,
        "stage_summary": _stage_summary(ordered, completed_ids),
    }


def main():
    p = argparse.ArgumentParser(description="Lesson curriculum CLI")
    sub = p.add_subparsers(dest="command")

    ls = sub.add_parser("list")
    ls.add_argument("--bundled-dir", default=BUNDLED_LESSONS_DIR_DEFAULT)
    ls.add_argument("--user-dir",    default=USER_LESSONS_DIR_DEFAULT)

    sh = sub.add_parser("show")
    sh.add_argument("--id", required=True)
    sh.add_argument("--bundled-dir", default=BUNDLED_LESSONS_DIR_DEFAULT)
    sh.add_argument("--user-dir",    default=USER_LESSONS_DIR_DEFAULT)

    st = sub.add_parser("start")
    st.add_argument("--id",    required=True)
    st.add_argument("--state", default=LESSON_STATE_PATH)
    st.add_argument("--bundled-dir", default=BUNDLED_LESSONS_DIR_DEFAULT)
    st.add_argument("--user-dir",    default=USER_LESSONS_DIR_DEFAULT)

    at = sub.add_parser("attempt")
    at.add_argument("--move")
    at.add_argument("--squares")
    at.add_argument("--state", default=LESSON_STATE_PATH)

    su = sub.add_parser("status")
    su.add_argument("--state", default=LESSON_STATE_PATH)
    su.add_argument("--bundled-dir", default=BUNDLED_LESSONS_DIR_DEFAULT)
    su.add_argument("--user-dir",    default=USER_LESSONS_DIR_DEFAULT)

    hi = sub.add_parser("hint")
    hi.add_argument("--state", default=LESSON_STATE_PATH)

    args = p.parse_args()
    if not args.command:
        p.print_help()
        sys.exit(1)

    # Not every subcommand defines every flag (e.g. attempt has no
    # --bundled-dir/--user-dir) — guard each expansion individually rather
    # than assuming the full flag set unconditionally.
    if hasattr(args, "bundled_dir"):
        args.bundled_dir = os.path.expanduser(args.bundled_dir)
    if hasattr(args, "user_dir"):
        args.user_dir = os.path.expanduser(args.user_dir)
    if hasattr(args, "state"):
        args.state = os.path.expanduser(args.state)

    dispatch = {
        "list": cmd_list, "show": cmd_show, "start": cmd_start,
        "attempt": cmd_attempt, "status": cmd_status, "hint": cmd_hint,
    }
    result = dispatch[args.command](args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
