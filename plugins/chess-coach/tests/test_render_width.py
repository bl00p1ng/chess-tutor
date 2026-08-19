import os
import sys

import chess

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from render import (
    visible_width, effective_width, wrap_move_pairs, render_moves, render_winbar,
    render_status, plain_render, full_render, render_coaching,
    wrap_coaching_lines,
)

# Black king on e8, White queen on e1, open e-file: Black to move, in check.
# A minimal synthetic position (not a real game) used only to exercise the
# CHECK! indicator independently of move-history/board-square fixtures.
CHECK_FEN = "4k3/8/8/8/8/8/8/4Q1K1 b - - 0 1"


def test_visible_width_strips_ansi_before_measuring():
    """ANSI escape codes contribute zero visible columns."""
    bold_reset = "\033[1mhello\033[0m"
    assert visible_width(bold_reset) == 5
    assert visible_width("\033[2J\033[H") == 0


def test_visible_width_classifies_by_eaw_and_overrides():
    """Each width class render.py actually emits is measured correctly."""
    # Box drawing + block elements: single-width overrides (13 glyphs, 1 col each).
    assert visible_width("┌─┐│└┘├┤┬┴┼█░") == 13
    # Pictographs whose Unicode East-Asian-Width under-reports them as
    # Ambiguous (▲▼) or Neutral (⚠): the override forces double-width.
    assert visible_width("▲▼⚠") == 6
    # Generic East-Asian-Width Wide/Fullwidth characters, not in any
    # override list, fall through to unicodedata directly.
    assert visible_width("中Ａ") == 4
    # Default class: ASCII, chess piece glyphs (Neutral), and remaining
    # Ambiguous characters not in the pictograph override all count 1.
    assert visible_width("aA1. ♔♟★") == 8


def test_visible_width_variation_selector_is_zero_width():
    """A variation selector modifies the preceding pictograph, adding no width."""
    assert visible_width("⚠️") == 2


def test_effective_width_ceiling():
    """50 is an absolute ceiling; 40 is the floor. Both ends must hold,
    and a value already inside the band must pass through unchanged —
    proving this is a real clamp, not a constant."""
    # Ceiling: a wide/no-flag terminal (e.g. an ultrawide monitor slice)
    # or an explicit --width above 50 must never widen past 50.
    assert effective_width(50) == 50
    assert effective_width(51) == 50
    assert effective_width(120) == 50
    assert effective_width(200) == 50
    # Floor: a narrow terminal or an explicit --width below 40 is raised to 40.
    assert effective_width(39) == 40
    assert effective_width(1) == 40
    # Mid-band: an in-range value passes through unchanged.
    assert effective_width(45) == 45


def test_wrap_move_pairs_groups_by_width_without_splitting_pairs():
    """Pairs are greedily packed onto lines that fit width; a pair is
    never broken across two lines."""
    pairs = [f"{n}.e4 e5" for n in range(1, 9)]  # 8 pairs, 7 visible cols each
    assert wrap_move_pairs(pairs, 40) == [
        ["1.e4 e5", "2.e4 e5", "3.e4 e5", "4.e4 e5"],
        ["5.e4 e5", "6.e4 e5", "7.e4 e5", "8.e4 e5"],
    ]


def test_wrap_move_pairs_single_line_when_it_fits():
    """A short list that fits within width stays on a single line —
    proving the helper does not always split."""
    pairs = ["1.e4 e5", "2.Nf3 Nc6"]
    assert wrap_move_pairs(pairs, 50) == [pairs]


def test_render_moves_wraps_long_history():
    """A move history longer than one line wraps without splitting a pair;
    the windowing '...' prefix is itself wrapped as an ordinary token."""
    moves_san = ["e4", "e5"] * 10  # 10 pairs; windowing keeps the last 8 + '...'.
    result = render_moves(moves_san, width=40)
    lines = result.split("\n")
    assert len(lines) == 3
    for line in lines:
        assert visible_width(line) <= 40
    assert "..." in lines[0]
    assert "10." in lines[2]
    assert "1." not in result  # windowed out, not merely wrapped
    assert "2." not in result


def test_render_moves_short_history_stays_on_one_line():
    """A short history that fits within width renders as a single line."""
    result = render_moves(["e4", "e5", "Nf3", "Nc6"], width=50)
    assert "\n" not in result
    assert "1." in result and "2." in result


def test_render_winbar_within_bound():
    """bar = clamp(width - 21, 8, 28); the whole bar stays within width at
    both the ceiling and the floor. winrate_white=0.5 keeps the 50/50%
    labels at a fixed digit count (overhead=21) so both ends are exact,
    not just bounded."""
    at_ceiling = render_winbar(0.5, width=50)
    bar_blocks_ceiling = at_ceiling.count("█") + at_ceiling.count("░")
    assert bar_blocks_ceiling == 28
    assert visible_width(at_ceiling) == 49

    at_floor = render_winbar(0.5, width=40)
    bar_blocks_floor = at_floor.count("█") + at_floor.count("░")
    assert bar_blocks_floor == 19
    assert visible_width(at_floor) == 40


def test_render_status_stacks_long_opening():
    """A status carrying level/mode/playing plus the longest bundled
    opening name and a check indicator cannot fit on one line at width
    50, so render_status stacks onto three lines matching the D5
    worst-case bound exactly: L1=26 (turn+check), L2=38 (level+playing),
    L3=44 (mode+opening, longest OPENINGS name = 27 chars)."""
    board = chess.Board(CHECK_FEN)
    state = {
        "level": "intermediate", "mode": "lesson", "color": "white",
        "opening": "Ruy López (Spanish Opening)",
    }
    result = render_status(board, state, width=50)
    lines = result.split("\n")
    assert len(lines) == 3
    for line in lines:
        assert visible_width(line) <= 50
    assert visible_width(lines[0]) == 26
    assert visible_width(lines[1]) == 38
    assert visible_width(lines[2]) == 44
    assert "CHECK!" in lines[0]
    assert "Black to move" in lines[0]
    assert "Level: Intermediate" in lines[1]
    assert "Playing: White" in lines[1]
    assert "Mode: Lesson" in lines[2]
    assert "Ruy López (Spanish Opening)" in lines[2]  # fits untruncated at width 50


def test_render_status_truncates_opening_at_narrower_width():
    """At the width floor (40), the same L3 (44 visible cols at width 50)
    no longer fits, so the opening name is truncated with an ellipsis —
    proving truncation is driven by `width`, not a fixed constant."""
    board = chess.Board(CHECK_FEN)
    state = {
        "level": "intermediate", "mode": "lesson", "color": "white",
        "opening": "Ruy López (Spanish Opening)",
    }
    result = render_status(board, state, width=40)
    lines = result.split("\n")
    assert len(lines) == 3
    for line in lines:
        assert visible_width(line) <= 40
    assert "…" in lines[2]
    assert "Ruy López (Spanish Opening)" not in lines[2]  # truncated, not dropped
    assert "Mode: Lesson" in lines[2]


def test_render_status_single_line_when_it_fits():
    """A width wide enough for the full status keeps it on a single
    line — proving the fits-check is real conditional logic, not an
    always-stack fake."""
    board = chess.Board()  # standard start: White to move, no check
    state = {"level": "beginner", "mode": "play", "color": "white"}
    result = render_status(board, state, width=100)
    assert "\n" not in result
    assert visible_width(result) <= 100
    assert "White to move" in result
    assert "Level: Beginner" in result
    assert "Mode: Play" in result
    assert "Playing: White" in result


def test_plain_render_bounded():
    """plain_render is the chat-facing path; it must delegate to the
    shared width-aware render_moves/render_status (F8) instead of
    duplicating unbounded inline formatting. Every emitted line stays
    within the requested width, including a long move history, a long
    opening name, and a check indicator — none of which the old
    duplicated inline code bounded at all."""
    state = {
        "start_fen": CHECK_FEN,
        "moves_uci": [],
        "moves_san": ["e4", "e5"] * 10,  # 10 pairs — forces wrapping (3a-proven)
        "level": "intermediate",
        "mode": "lesson",
        "color": "white",
        "opening": "Ruy López (Spanish Opening)",
        "move_records": [
            {"winrate_white": 0.5, "coaching": "Line one.\nLine two."}
        ],
    }
    result = plain_render(state, width=40)
    lines = result.split("\n")
    assert lines  # non-empty — production code actually ran
    for line in lines:
        assert visible_width(line) <= 40

    assert "CHECK!" in result
    assert "…" in result  # long opening name truncated to fit width 40

    sep_lines = [line for line in lines if line.strip() and set(line.strip()) == {"─"}]
    assert sep_lines, "expected a coaching separator line"
    for sep_line in sep_lines:
        assert visible_width(sep_line) == 40  # width-2 dashes + 2-col indent = width exactly


# ---------------------------------------------------------------------------
# Coaching width bound (3c) — the tracked gap left open by 3a/3b: coaching
# prose was emitted with a 2-column indent and no wrapping at all, so a
# single long sentence blew past the absolute 50-column ceiling in both the
# plain and the ANSI/full render paths.
# ---------------------------------------------------------------------------

LONG_COACHING = (
    "This coaching sentence is deliberately longer than the absolute "
    "fifty-column rendering ceiling."
)


def test_wrap_coaching_lines_bounds_every_line_without_splitting_words():
    """The shared helper greedily fills lines within the width budget and
    never breaks a word that fits on a line of its own."""
    lines = wrap_coaching_lines(LONG_COACHING, 50)
    assert len(lines) > 1  # the source line genuinely had to wrap
    for line in lines:
        assert visible_width(line) <= 50
    # Every word survives, in order, with no character lost or duplicated.
    assert " ".join(lines).split() == LONG_COACHING.split()


def test_wrap_coaching_lines_hard_breaks_an_oversized_token():
    """A single token wider than the budget cannot be word-wrapped, so it is
    hard-broken — the bound holds unconditionally, never 'best effort'."""
    lines = wrap_coaching_lines("x" * 120, 50)
    assert len(lines) == 3
    for line in lines:
        assert visible_width(line) <= 50
    assert "".join(lines) == "x" * 120


def test_wrap_coaching_lines_preserves_source_line_structure():
    """Each source line wraps independently, so authored paragraph breaks in
    coaching text are not collapsed into one run-on block."""
    lines = wrap_coaching_lines("Line one.\n\nLine two.", 50)
    assert lines == ["Line one.", "", "Line two."]


def test_wrap_coaching_lines_measures_double_width_glyphs():
    """Width is measured in visible terminal columns, not len() — a run of
    double-width pictographs consumes two columns each."""
    lines = wrap_coaching_lines("💡 " + "⬜" * 40, 50)
    for line in lines:
        assert visible_width(line) <= 50
    assert len(lines) > 1


def test_render_coaching_wraps_long_prose_within_width():
    """The ANSI coaching renderer honours the width bound, and no ANSI colour
    run spans a line boundary — every emitted line resets its own colour."""
    result = render_coaching(LONG_COACHING, width=50)
    lines = result.split("\n")
    assert len(lines) > 1
    for line in lines:
        assert visible_width(line) <= 50
        assert line.startswith("  \033[33m")
        assert line.endswith("\033[0m")


def test_render_coaching_matches_plain_wrapping_exactly():
    """The ANSI and plain paths share one wrapping decision: stripping the
    escape codes from render_coaching reproduces the plain wrapped lines."""
    ansi_lines = render_coaching(LONG_COACHING, width=42).split("\n")
    stripped = [line.replace("\033[33m", "").replace("\033[0m", "")[2:]
                for line in ansi_lines]
    assert stripped == wrap_coaching_lines(LONG_COACHING, 42 - 2)


def test_plain_render_wraps_long_coaching_at_ceiling():
    """Regression for the verification blocker: the plain path produced a
    97-column line for this exact coaching sentence at requested width 50."""
    state = {
        "color": "white",
        "level": "beginner",
        "mode": "lesson",
        "moves_uci": [],
        "moves_san": [],
        "move_records": [{"winrate_white": 0.5, "coaching": LONG_COACHING}],
        "opening": None,
        "result": None,
    }
    result = plain_render(state, width=50)
    lines = result.split("\n")
    assert any(LONG_COACHING.split()[0] in line for line in lines)
    for line in lines:
        assert visible_width(line) <= 50


def test_full_render_wraps_long_coaching_at_ceiling():
    """The ANSI/full path carries the same bound as the plain path."""
    state = {
        "color": "white",
        "level": "beginner",
        "mode": "lesson",
        "moves_uci": [],
        "moves_san": [],
        "move_records": [{"winrate_white": 0.5, "coaching": LONG_COACHING}],
        "opening": None,
        "result": None,
    }
    result = full_render(state, False, width=50)
    for line in result.split("\n"):
        assert visible_width(line) <= 50
