import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from render import (
    visible_width, effective_width, wrap_move_pairs, render_moves, render_winbar,
)


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
