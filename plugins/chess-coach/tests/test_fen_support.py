"""
test_fen_support.py — Custom start-position (FEN) support across the chess-coach scripts.

Covers:
  - common.board_from_state / common.is_custom_start (pure functions)
  - engine.py new_game --fen (validation, storage, response fields)
  - engine.py opening-detection guard for custom starts (cmd_move / cmd_ai_move)
  - coach.py explain_ai / evaluate_user honoring a custom start
  - profile.py update excluding drill/custom-start states from ELO history + archive
"""

import json
import os
import subprocess
import sys

import chess
import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")

sys.path.insert(0, SCRIPTS)
from common import board_from_state, is_custom_start, evaluate, score_to_winrate  # noqa: E402

KQ_VS_K_FEN = "8/8/8/8/4k3/8/4Q3/4K3 w - - 0 1"


# ---------------------------------------------------------------------------
# 2.1 — common.board_from_state / common.is_custom_start
# ---------------------------------------------------------------------------
def test_board_from_state_with_fen():
    """board_from_state must start from start_fen and still replay moves_uci on top of it."""
    state = {"start_fen": KQ_VS_K_FEN, "moves_uci": []}
    board = board_from_state(state)
    assert board.fen() == KQ_VS_K_FEN

    # moves_uci must still apply on top of the custom start (Qe2-e4, vertical, e3 empty)
    state_with_move = {"start_fen": KQ_VS_K_FEN, "moves_uci": ["e2e4"]}
    board_after = board_from_state(state_with_move)
    assert board_after.piece_at(chess.E4).piece_type == chess.QUEEN
    assert board_after.piece_at(chess.E2) is None


def test_board_from_state_no_fen_regression():
    """A state without start_fen must reconstruct the standard start exactly as before."""
    state = {"moves_uci": ["e2e4", "e7e5"]}
    board = board_from_state(state)
    expected = chess.Board()
    expected.push(chess.Move.from_uci("e2e4"))
    expected.push(chess.Move.from_uci("e7e5"))
    assert board.fen() == expected.fen()

    # A state missing moves_uci entirely must also default cleanly (pre-existing behavior)
    assert board_from_state({}).fen() == chess.Board().fen()


def test_is_custom_start_predicate():
    """is_custom_start distinguishes drill/custom-FEN states from standard-start states."""
    assert is_custom_start({"start_fen": KQ_VS_K_FEN}) is True
    assert is_custom_start({"moves_uci": ["e2e4"]}) is False
    assert is_custom_start({}) is False


# ---------------------------------------------------------------------------
# 2.2 / 2.3 — engine.py new_game --fen
# ---------------------------------------------------------------------------
def run_new_game(tmp_path, fen=None, color="white", level="intermediate"):
    state_path = str(tmp_path / "game.json")
    cmd = [sys.executable, f"{SCRIPTS}/engine.py", "new_game",
           "--color", color, "--level", level, "--state", state_path]
    if fen is not None:
        cmd += ["--fen", fen]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(r.stdout), state_path


def test_new_game_fen_valid(tmp_path):
    """--fen with a valid FEN stores start_fen; omitting --fen stores none (F9: 'stored only when provided')."""
    result, state_path = run_new_game(tmp_path, fen=KQ_VS_K_FEN)
    assert result["ok"] is True
    with open(state_path) as f:
        state = json.load(f)
    assert state["start_fen"] == KQ_VS_K_FEN

    no_fen_result, no_fen_state_path = run_new_game(tmp_path, fen=None)
    assert no_fen_result["ok"] is True
    with open(no_fen_state_path) as f:
        no_fen_state = json.load(f)
    assert "start_fen" not in no_fen_state


def test_new_game_fen_invalid_rejected(tmp_path):
    """An invalid FEN is rejected with an error and no state file is written."""
    result, state_path = run_new_game(tmp_path, fen="not-a-fen-at-all")
    assert result["ok"] is False
    assert "error" in result
    assert not os.path.exists(state_path)


@pytest.mark.parametrize("fen", [
    "8/8/8/8/4k3/8/4Q3/4K3 w - - 0 1",  # White heavily winning (K+Q vs K)
    "4k3/8/8/8/8/8/4q3/4K3 w - - 0 1",  # Black heavily winning (mirror: K vs K+Q)
])
def test_new_game_fen_response_reflects_position(tmp_path, fen):
    """new_game response fen/score_cp/winrate_white must derive from the FEN-built board (F9)."""
    result, _ = run_new_game(tmp_path, fen=fen)
    assert result["ok"] is True

    board = chess.Board(fen)
    expected_score = evaluate(board)
    expected_winrate = score_to_winrate(expected_score, chess.WHITE)

    assert result["fen"] == board.fen()
    assert result["fen"] != chess.Board().fen()
    assert result["score_cp"] == expected_score
    assert result["winrate_white"] == expected_winrate
    assert result["score_cp"] != 0


# ---------------------------------------------------------------------------
# 2.4 — opening detection suppressed for custom starts (cmd_move + cmd_ai_move)
# ---------------------------------------------------------------------------
# Black queen removed from the standard start: still a "custom start" (has start_fen),
# but c2-c4 remains legal so its SAN is the unambiguous single-token "c4".
QUEENLESS_START_FEN = "rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

# Black's ONLY legal move is c5-c4 (SAN "c4"); queen on b6 covers all three king
# flight squares (a7/b7/b8) without giving check. Makes ai_move fully deterministic.
FORCED_C4_FEN = "k7/8/1Q6/2p5/8/8/8/4K3 b - - 0 1"


def test_opening_suppressed_custom_start(tmp_path):
    """A custom-start game must never receive an opening label, even for a textually-matching move."""
    result, state_path = run_new_game(tmp_path, fen=QUEENLESS_START_FEN)
    assert result["ok"] is True

    move_result = subprocess.run(
        [sys.executable, f"{SCRIPTS}/engine.py", "move",
         "--move", "c4", "--state", state_path],
        capture_output=True, text=True,
    )
    move_json = json.loads(move_result.stdout)
    assert move_json["ok"] is True
    assert move_json["move_san"] == "c4"
    assert move_json["opening"] is None

    with open(state_path) as f:
        state = json.load(f)
    assert state["opening"] is None


def test_opening_still_detected_without_custom_start(tmp_path):
    """Regression contrast: the SAME move, from a standard (non-custom) start, still gets labeled."""
    result, state_path = run_new_game(tmp_path, fen=None)
    assert result["ok"] is True

    move_result = subprocess.run(
        [sys.executable, f"{SCRIPTS}/engine.py", "move",
         "--move", "c4", "--state", state_path],
        capture_output=True, text=True,
    )
    move_json = json.loads(move_result.stdout)
    assert move_json["ok"] is True
    assert move_json["opening"] == "English Opening"


def test_opening_suppressed_custom_start_ai_move(tmp_path):
    """The same guard must apply to the cmd_ai_move code path, not only cmd_move."""
    result, state_path = run_new_game(tmp_path, fen=FORCED_C4_FEN)
    assert result["ok"] is True

    ai_result = subprocess.run(
        [sys.executable, f"{SCRIPTS}/engine.py", "ai_move", "--state", state_path],
        capture_output=True, text=True,
    )
    ai_json = json.loads(ai_result.stdout)
    assert ai_json["ok"] is True
    assert ai_json["move_san"] == "c4"
    assert ai_json["opening"] is None

    with open(state_path) as f:
        state = json.load(f)
    assert state["opening"] is None


# ---------------------------------------------------------------------------
# 2.5 — coach.py honors custom start (cmd_explain_ai reconstruction + hint guard)
# ---------------------------------------------------------------------------
def write_state(tmp_path, state, name="game.json"):
    state_path = tmp_path / name
    state_path.write_text(json.dumps(state))
    return str(state_path)


def test_explain_ai_uses_custom_start(tmp_path):
    """cmd_explain_ai must reconstruct the board from start_fen, not a hardcoded standard start."""
    fen = "4k3/8/8/8/8/8/4Q3/4K3 w - - 0 1"  # White K+Q vs bare Black K
    state = {
        "color": "white", "player_name": "human",
        "players": {"white": "human", "black": "ai"},
        "level": "beginner", "mode": "coach",
        "start_fen": fen,
        "moves_uci": ["e2e7"], "moves_san": ["Qe7+"],
        "move_records": [{
            "move_san": "Qe7+", "move_uci": "e2e7",
            "player": "white", "actor": "ai",
            "score_before_cp": 0, "score_after_cp": 500,
            "winrate_white": 0.9, "coaching": None,
        }],
        "move_count": 1, "result": None, "opening": None,
    }
    state_path = write_state(tmp_path, state)

    result = subprocess.run(
        [sys.executable, f"{SCRIPTS}/coach.py", "explain_ai", "--state", state_path],
        capture_output=True, text=True,
    )
    out = json.loads(result.stdout)
    assert out["ok"] is True
    # Fixed: the moved piece is a Queen (per start_fen), it captured nothing (e7 was
    # empty on the custom board), and Qe7 delivers check to the king on e8.
    assert any("Queen" in line for line in out["coaching_lines"])
    assert not any("Captures" in line for line in out["coaching_lines"])
    assert any("Delivers check" in line for line in out["coaching_lines"])


def test_evaluate_user_opening_hint_suppressed_custom_start(tmp_path):
    """cmd_evaluate_user must skip opening_hint entirely for a custom-start (drill) state."""
    fen = "4k3/8/8/8/8/8/4Q3/4K3 w - - 0 1"
    state = {
        "color": "white", "player_name": "human",
        "players": {"white": "human", "black": "ai"},
        "level": "beginner", "mode": "coach",
        "start_fen": fen,
        "moves_uci": [], "moves_san": [], "move_records": [],
        "move_count": 0, "result": None, "opening": None,
    }
    state_path = write_state(tmp_path, state)

    result = subprocess.run(
        [sys.executable, f"{SCRIPTS}/coach.py", "evaluate_user",
         "--state", state_path, "--move", "e2e7"],
        capture_output=True, text=True,
    )
    out = json.loads(result.stdout)
    assert out["ok"] is True
    # Unguarded code emits this exact tip for a move_num=0 queen move (opening_hint,
    # coach.py:89-90) — it must be suppressed for a custom start.
    assert not any("early queen development" in line for line in out["coaching_lines"])
    assert not any(line.startswith("Opening:") for line in out["coaching_lines"])


# ---------------------------------------------------------------------------
# 2.6 — profile.py cmd_update skips mutation for custom-start (drill) states
# ---------------------------------------------------------------------------
def run_profile_update(tmp_path, state):
    """Run `profile.py update` with HOME redirected under tmp_path, isolating both the
    default profile file and the default games archive from the real ~/.chess_coach/."""
    home = tmp_path / "home"
    home.mkdir()
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state))
    env = {**os.environ, "HOME": str(home)}
    result = subprocess.run(
        [sys.executable, f"{SCRIPTS}/profile.py", "update", "--state", str(state_path)],
        capture_output=True, text=True, env=env,
    )
    return json.loads(result.stdout), home


def test_profile_update_skips_drill_state(tmp_path):
    """A completed lesson/drill (custom-start) state must not touch elo_history or the archive."""
    state = {
        "color": "white",
        "start_fen": KQ_VS_K_FEN,
        "moves_uci": ["e2e4"],
        "move_records": [
            {"player": "white", "score_before_cp": 0, "score_after_cp": 900},
        ],
    }
    result, home = run_profile_update(tmp_path, state)
    assert result["ok"] is True
    assert result["skipped"] is True

    profile_path = home / ".chess_coach" / "profile.json"
    assert not profile_path.exists()

    games_dir = home / ".chess_coach" / "games"
    assert not games_dir.exists() or list(games_dir.glob("*.json")) == []


def test_profile_update_normal_game_unchanged(tmp_path):
    """A normal (non-custom-start) completed game still appends ELO history and archives."""
    state = {
        "color": "white",
        "moves_uci": ["e2e4", "e7e5", "g1f3", "b8c6"],
        "move_records": [
            {"player": "white", "score_before_cp": 0,   "score_after_cp": 30},
            {"player": "black", "score_before_cp": 30,  "score_after_cp": -10},
            {"player": "white", "score_before_cp": -10, "score_after_cp": 40},
            {"player": "black", "score_before_cp": 40,  "score_after_cp": 0},
        ],
    }
    result, home = run_profile_update(tmp_path, state)
    assert result["ok"] is True
    assert "skipped" not in result

    profile_path = home / ".chess_coach" / "profile.json"
    assert profile_path.exists()
    profile = json.loads(profile_path.read_text())
    assert profile["games_played"] == 1
    assert len(profile["elo_history"]) == 1

    games_dir = home / ".chess_coach" / "games"
    archived = list(games_dir.glob("*.json"))
    assert len(archived) == 1
