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

import json
import os
import subprocess
import sys
from types import SimpleNamespace

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
    load_lesson_file,
    LessonValidationError,
    cmd_list,
    cmd_show,
    cmd_start,
    cmd_attempt,
    cmd_status,
    _refuse_game_state_path,
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


# ---------------------------------------------------------------------------
# 4a3.1 — load_lesson_file: single-file read + JSON-parse + validate_lesson.
# No merge/list logic here — that belongs to slice 4b's `list` command.
# ---------------------------------------------------------------------------
def test_load_lesson_file_valid_returns_parsed_dict(tmp_path):
    """A well-formed lesson file loads and returns the parsed dict
    unchanged."""
    lesson = make_lesson()
    path = tmp_path / "lesson.json"
    path.write_text(json.dumps(lesson))

    loaded = load_lesson_file(str(path))
    assert loaded == lesson


def test_load_lesson_file_invalid_raises_with_full_error_list(tmp_path):
    """Triangulation: an invalid lesson raises LessonValidationError whose
    .errors carries EVERY problem, not just the first — proven with a
    lesson that fails two independent checks at once (bad stage AND bad
    player_color), neither of which short-circuits the other."""
    lesson = make_lesson(stage="not-a-real-stage", player_color="purple")
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(lesson))

    with pytest.raises(LessonValidationError) as exc_info:
        load_lesson_file(str(path))

    errors = exc_info.value.errors
    assert len(errors) == 2
    assert any("stage" in e.lower() for e in errors)
    assert any("player_color" in e for e in errors)


# ---------------------------------------------------------------------------
# 4b-i — CLI surface: list / show / start; linear-progression gating (F5).
# attempt/hint/status are a later slice (4b-ii) — not built here.
# ---------------------------------------------------------------------------
def write_lesson_file(directory, **overrides):
    """Write a make_lesson() fixture (with overrides) to directory/<id>.json."""
    lesson = make_lesson(**overrides)
    path = os.path.join(directory, f"{lesson['id']}.json")
    with open(path, "w") as f:
        json.dump(lesson, f)
    return lesson


def three_stage_lessons(bundled_dir):
    """Three same-stage lessons in curriculum order l1 < l2 < l3."""
    return [write_lesson_file(bundled_dir, id=f"l{i}", order=i) for i in (1, 2, 3)]


def set_learning_home(monkeypatch, tmp_path, completed_ids, home_name="home"):
    """Point HOME at a fresh tmp directory carrying a learning.json marking
    completed_ids complete — never the real ~/.chess_coach/."""
    home = tmp_path / home_name
    (home / ".chess_coach").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    learning = {
        "schema_version": 1,
        "completed": {
            lid: {"completed_at": "2026-01-01", "attempts": 1, "hints_used": 0}
            for lid in completed_ids
        },
        "last_lesson_id": None,
    }
    (home / ".chess_coach" / "learning.json").write_text(json.dumps(learning))
    return home


def test_list_locked_flags(tmp_path, monkeypatch):
    """locked=False for a completed lesson and for the next selectable one;
    locked=True for anything beyond it (F5)."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    three_stage_lessons(str(bundled))
    set_learning_home(monkeypatch, tmp_path, {"l1"})

    result = cmd_list(SimpleNamespace(bundled_dir=str(bundled), user_dir=str(tmp_path / "user")))
    by_id = {l["id"]: l for l in result["lessons"]}
    assert by_id["l1"]["locked"] is False   # completed
    assert by_id["l2"]["locked"] is False   # next selectable
    assert by_id["l3"]["locked"] is True    # beyond the next lesson


def test_list_locked_flags_completed_out_of_order_stays_unlocked(tmp_path, monkeypatch):
    """Triangulation: a lesson completed OUT of curriculum order (l2 done,
    l1 not) must still read as unlocked — completion, not position, decides
    lock status. A position-only check gets exactly this case wrong."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    three_stage_lessons(str(bundled))
    set_learning_home(monkeypatch, tmp_path, {"l2"}, home_name="home2")

    result = cmd_list(SimpleNamespace(bundled_dir=str(bundled), user_dir=str(tmp_path / "user")))
    by_id = {l["id"]: l for l in result["lessons"]}
    assert by_id["l2"]["locked"] is False   # completed, even though out of order
    assert by_id["l3"]["locked"] is True    # still beyond the first incomplete (l1)


def test_show_found_and_not_found(tmp_path):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    lesson = write_lesson_file(str(bundled), id="l1")

    found = cmd_show(SimpleNamespace(id="l1", bundled_dir=str(bundled), user_dir=str(tmp_path / "user")))
    assert found["ok"] is True
    assert found["lesson"] == lesson

    missing = cmd_show(SimpleNamespace(id="nope", bundled_dir=str(bundled), user_dir=str(tmp_path / "user")))
    assert missing["ok"] is False
    assert "nope" in missing["error"]


def test_start_gates_out_of_order_allows_replay(tmp_path, monkeypatch):
    """Spec scenarios: 'Linear within stage' (reject lesson 3 while lesson 2
    is still incomplete, redirect to it) and 'Replay without tracking' (an
    already-completed lesson stays startable regardless of position). Also
    covers the primary accept path: starting the actual next lesson."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    three_stage_lessons(str(bundled))
    set_learning_home(monkeypatch, tmp_path, {"l1"})
    common = dict(bundled_dir=str(bundled), user_dir=str(tmp_path / "user"),
                  state=str(tmp_path / "lesson_state.json"))

    out_of_order = cmd_start(SimpleNamespace(id="l3", **common))
    assert out_of_order["ok"] is False
    assert out_of_order["next_lesson_id"] == "l2"

    next_lesson = cmd_start(SimpleNamespace(id="l2", **common))
    assert next_lesson["ok"] is True
    assert next_lesson["lesson_id"] == "l2"

    replay = cmd_start(SimpleNamespace(id="l1", **common))
    assert replay["ok"] is True
    assert replay["lesson_id"] == "l1"


def test_cli_list_runs_via_subprocess(tmp_path):
    """Real end-to-end CLI proof: argparse + dispatch + main(), not just the
    cmd_* functions called directly in-process."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    three_stage_lessons(str(bundled))
    home = tmp_path / "home_subprocess"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}

    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "lesson.py"), "list",
         "--bundled-dir", str(bundled), "--user-dir", str(tmp_path / "user")],
        capture_output=True, text=True, env=env,
    )
    out = json.loads(result.stdout)
    assert out["ok"] is True
    assert {l["id"] for l in out["lessons"]} == {"l1", "l2", "l3"}


def test_state_realpath_refusal(tmp_path, monkeypatch):
    """D7-E: refuse any --state whose realpath equals the game file's
    realpath — proven via a non-literal path form (a symlink), not just a
    literal string match, so a symlink/relative/'~' spelling cannot slip
    past cmd_start's refusal."""
    home = tmp_path / "home"
    (home / ".chess_coach").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    game_path = home / ".chess_coach" / "current_game.json"
    game_path.write_text("{}")

    sneaky_link = tmp_path / "sneaky.json"     # non-literal path form
    sneaky_link.symlink_to(game_path)

    result = cmd_start(SimpleNamespace(
        id="anything", state=str(sneaky_link),
        bundled_dir=str(tmp_path / "bundled"), user_dir=str(tmp_path / "user"),
    ))
    assert result["ok"] is False
    assert "in-progress game" in result["error"]

    # Negative control: an unrelated path is never refused by this check —
    # proven directly against the helper (no lesson fixture needed).
    assert _refuse_game_state_path(str(tmp_path / "lesson_state.json")) is None


# ---------------------------------------------------------------------------
# 4b-ii — attempt's reject-before-dispatch gate, and status. Adjudication
# #1 becomes real here: attempt routes PREDICATES only and must never
# reach BRIDGE_OBJECTIVES. attempt's accept path (predicate dispatch, D2
# rebase, budget/reset) and hint land in slice 4b-iii; free_play bridge
# completion evaluation inside status lands in slice 4c.
# ---------------------------------------------------------------------------
def make_lesson_state(lesson, **lesson_block_overrides):
    """Build a current_lesson.json-shaped state dict (State superset
    contract) directly around a lesson definition, for attempt/status
    tests that write state without going through cmd_start."""
    learner_color = lesson["player_color"]
    other_color = "black" if learner_color == "white" else "white"
    lesson_block = {
        "definition": lesson, "moves_history": [],
        "moves_used": 0, "attempts_used": 0, "hints_used": 0, "result": None,
    }
    lesson_block.update(lesson_block_overrides)
    return {
        "color": learner_color, "player_name": "learner",
        "players": {learner_color: "learner", other_color: "ai"},
        "level": "beginner", "mode": "lesson",
        "moves_uci": [], "moves_san": [], "move_records": [],
        "move_count": 0, "result": None, "opening": None,
        "start_fen": lesson["start_fen"],
        "lesson": lesson_block,
    }


def write_state_file(tmp_path, state, name="lesson_state.json"):
    path = tmp_path / name
    path.write_text(json.dumps(state))
    return str(path)


def test_attempt_illegal_move_no_budget_consumed(tmp_path):
    """Spec scenario 'Illegal move': a chess-illegal move is rejected BEFORE
    predicate dispatch, and the moves_used/attempts_used budget counters
    are unchanged."""
    lesson = make_lesson()  # reach_square, KNIGHT_D5_FEN, max_moves=3
    state = make_lesson_state(lesson)
    state_path = write_state_file(tmp_path, state)

    result = cmd_attempt(SimpleNamespace(move="e2e4", state=state_path))  # no piece on e2

    assert result["ok"] is True
    assert result["accepted"] is False
    assert result["reason"]
    with open(state_path) as f:
        saved = json.load(f)
    assert saved["lesson"]["moves_used"] == 0
    assert saved["lesson"]["attempts_used"] == 0


def test_attempt_unknown_objective_type_fails_closed(tmp_path):
    """Fail-closed dispatch: an objective type outside PREDICATES (and not a
    bridge objective either) must be REJECTED, never silently accepted or
    treated as a pass — attempt's own dispatch fails closed even though
    validate_lesson would already have refused this lesson at load time."""
    lesson = make_lesson(objective={"type": "not_a_real_predicate"})
    state = make_lesson_state(lesson)
    state_path = write_state_file(tmp_path, state)

    result = cmd_attempt(SimpleNamespace(move="d5f6", state=state_path))

    assert result["ok"] is False


def test_attempt_quiz_type_dispatch_deferred(tmp_path):
    """legal_moves_from_square is a genuine PREDICATES member — not unknown,
    not a bridge objective — but its --squares quiz dispatch is deferred to
    a later slice (tracked task 4b.9). attempt must say so with wording
    distinct from the bridge redirect, never silently accept it or route it
    to a checker."""
    lesson = make_lesson(objective={"type": "legal_moves_from_square", "square": "d5"})
    state = make_lesson_state(lesson)
    state_path = write_state_file(tmp_path, state)

    result = cmd_attempt(SimpleNamespace(move="d5f6", state=state_path))

    assert result["ok"] is True
    assert result["accepted"] is False
    assert "quiz" in result["reason"].lower()
    assert "bridge" not in result["reason"].lower()


def test_attempt_bridge_lesson_redirects(tmp_path):
    """Spec scenario 'Attempt does not evaluate bridge objectives': a
    free_play bridge-objective lesson always returns accepted:false from
    attempt and is never routed into a PREDICATES checker. A legal move on
    the bridge lesson's own (standard-start) board proves the redirect
    fires regardless of move legality, not as a side effect of rejecting
    an illegal move."""
    lesson = make_bridge_lesson()
    state = make_lesson_state(lesson)
    state_path = write_state_file(tmp_path, state)

    result = cmd_attempt(SimpleNamespace(move="e2e4", state=state_path))  # legal on a standard start

    assert result["ok"] is True
    assert result["accepted"] is False
    assert "bridge" in result["reason"].lower()


def test_refuse_game_state_path_wired_into_attempt_and_status(tmp_path, monkeypatch):
    """D7-E wiring proof for the two new commands this slice adds — the
    helper and its direct-call test already exist (4b-i); this proves each
    calls it as its first line, via the same non-literal (symlink) path
    form used in test_state_realpath_refusal. hint's wiring lands with
    that command in slice 4b-iii."""
    home = tmp_path / "home"
    (home / ".chess_coach").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    game_path = home / ".chess_coach" / "current_game.json"
    game_path.write_text("{}")
    sneaky_link = tmp_path / "sneaky.json"
    sneaky_link.symlink_to(game_path)

    attempt_result = cmd_attempt(SimpleNamespace(move="e2e4", state=str(sneaky_link)))
    status_result = cmd_status(SimpleNamespace(
        state=str(sneaky_link),
        bundled_dir=str(tmp_path / "bundled"), user_dir=str(tmp_path / "user"),
    ))

    for result in (attempt_result, status_result):
        assert result["ok"] is False
        assert "in-progress game" in result["error"]


def test_status_offers_resume(tmp_path, monkeypatch):
    """Spec scenario 'Resume offer': an in-progress drill from a prior
    session (no terminal result yet) is reported as the active/default
    resume entry point, regardless of how it reached that state."""
    home = tmp_path / "home"
    (home / ".chess_coach").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    lesson = make_lesson()
    state = make_lesson_state(lesson, moves_used=1)
    state_path = str(home / ".chess_coach" / "current_lesson.json")
    with open(state_path, "w") as f:
        json.dump(state, f)

    result = cmd_status(SimpleNamespace(
        state=state_path,
        bundled_dir=str(tmp_path / "bundled"), user_dir=str(tmp_path / "user"),
    ))

    assert result["active"] is not None
    assert result["active"]["lesson_id"] == "knight-tour-1"
    assert result["active"]["moves_used"] == 1


def test_status_no_active_lesson_when_state_missing(tmp_path):
    """Triangulation: no prior-session state file means nothing to resume."""
    result = cmd_status(SimpleNamespace(
        state=str(tmp_path / "nope.json"),
        bundled_dir=str(tmp_path / "bundled"), user_dir=str(tmp_path / "user"),
    ))
    assert result["active"] is None


# ---------------------------------------------------------------------------
# 4b-iii — attempt's accept path: predicate dispatch, D2 post-move FEN
# rebase, idle-king legality guard, move/attempt budgets, reset-on-
# exhaustion, and the solved branch (learning.json write). Reuses fixtures
# pre-verified in 4b-ii: KNIGHT_D5_FEN (4a) for the normal-rebase case,
# CHECK_ON_REBASE_FEN below for the legality-guard case.
# ---------------------------------------------------------------------------
CHECK_ON_REBASE_FEN = "7k/8/8/R7/8/8/8/4K3 w - - 0 1"


def test_rebase_turn_and_legality_guard(tmp_path):
    """Fused (one function, per the task's singular name): a normal
    non-solving move rebases start_fen with the turn flipped back to the
    learner, and moves_uci/moves_san are actually populated then cleared
    (non-vacuous — a regression that dropped the clear would leave them
    non-empty and fail this). Second scenario: a move whose rebase would
    leave the IDLE opponent king illegally in check is a malformed-lesson
    error (ok:false), not a silently saved bad position."""
    # Scenario 1: normal rebase.
    lesson = make_lesson()  # reach_square target f6, KNIGHT_D5_FEN, max_moves=3
    state = make_lesson_state(lesson)
    state_path = write_state_file(tmp_path, state, name="rebase.json")

    result = cmd_attempt(SimpleNamespace(move="d5b4", state=state_path))

    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["result"] == "in_progress"
    assert result["position_reset"] is False
    with open(state_path) as f:
        saved = json.load(f)
    assert saved["moves_uci"] == []
    assert saved["moves_san"] == []
    assert saved["lesson"]["moves_used"] == 1
    rebased_board = chess.Board(saved["start_fen"])
    assert rebased_board.turn == chess.WHITE  # flipped back to the learner
    assert rebased_board.piece_at(chess.B4).piece_type == chess.KNIGHT

    # Scenario 2: idle-king legality guard.
    guard_lesson = make_lesson(
        id="guard-lesson", start_fen=CHECK_ON_REBASE_FEN,
        objective={"type": "reach_square", "square": "a1", "piece": "R", "max_moves": 3},
    )
    guard_state = make_lesson_state(guard_lesson)
    guard_path = write_state_file(tmp_path, guard_state, name="guard.json")

    guard_result = cmd_attempt(SimpleNamespace(move="a5h5", state=guard_path))

    assert guard_result["ok"] is False
    assert guard_result["error"]


def test_reset_on_exhausted_budget(tmp_path):
    """Fused: (1) the move budget is exhausted this attempt but attempts
    remain — the position resets to definition.start_fen, attempts_used
    increments, moves_used zeroes; (2) triangulation — attempts are ALSO
    exhausted, so the drill terminates as failed and echoes solution_text
    for narration, rather than silently resetting forever."""
    # Scenario 1: reset, attempts remain.
    lesson = make_lesson(
        objective={"type": "reach_square", "square": "f6", "piece": "N", "max_moves": 1},
        max_attempts=2,
    )
    state = make_lesson_state(lesson)
    state_path = write_state_file(tmp_path, state, name="reset.json")

    result = cmd_attempt(SimpleNamespace(move="d5b4", state=state_path))

    assert result["ok"] is True
    assert result["position_reset"] is True
    assert result["result"] == "in_progress"
    with open(state_path) as f:
        saved = json.load(f)
    assert saved["start_fen"] == KNIGHT_D5_FEN
    assert saved["moves_uci"] == []
    assert saved["lesson"]["attempts_used"] == 1
    assert saved["lesson"]["moves_used"] == 0

    # Scenario 2: attempts also exhausted — terminal failed.
    terminal_lesson = make_lesson(
        id="knight-tour-terminal",
        objective={"type": "reach_square", "square": "f6", "piece": "N", "max_moves": 1},
        max_attempts=1,
    )
    terminal_state = make_lesson_state(terminal_lesson)
    terminal_path = write_state_file(tmp_path, terminal_state, name="failed.json")

    terminal_result = cmd_attempt(SimpleNamespace(move="d5b4", state=terminal_path))

    assert terminal_result["result"] == "failed"
    assert terminal_result["solution_text"] == terminal_lesson["solution_text"]
    with open(terminal_path) as f:
        terminal_saved = json.load(f)
    assert terminal_saved["lesson"]["result"] == "failed"


def test_attempt_solved_records_completion(tmp_path, monkeypatch):
    """Solved path: a satisfying move records result=solved and writes
    completion into learning.json — no separate command (design). HOME is
    monkeypatched so this never touches the real ~/.chess_coach/."""
    home = tmp_path / "home"
    (home / ".chess_coach").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    lesson = make_lesson()  # reach_square target f6
    state = make_lesson_state(lesson)
    state_path = write_state_file(tmp_path, state, name="solved.json")

    result = cmd_attempt(SimpleNamespace(move="d5f6", state=state_path))

    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["result"] == "solved"
    assert result["fen"]
    with open(home / ".chess_coach" / "learning.json") as f:
        learning = json.load(f)
    assert "knight-tour-1" in learning["completed"]
    assert learning["last_lesson_id"] == "knight-tour-1"
