#!/usr/bin/env python3
"""
render.py — ANSI terminal chess board renderer.

Usage:
  python3 render.py --state FILE [--clear]

With --clear: uses ANSI escape codes to overwrite the terminal from the top,
              creating a "fixed position" effect.
Without --clear: plain print (useful for piping or logging).

Layout:
  ♟  Chess Coach
  ┌─── 8×8 board ───┐
  │  Unicode pieces  │
  │  Last move: yellow highlight │
  └──────────────────┘
  Win-rate bar  [████░░░]  W 62% / B 38%
  Move history  1. e4 e5  2. Nf3 ...
  ──────────────────────────────────────
  [Coaching text for last move]
  ──────────────────────────────────────
  ⬜ White to move  │  Level: Intermediate  │  Mode: Play  │  Playing: White
"""

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(__file__))
from common import board_from_state

import chess

# ---------------------------------------------------------------------------
# ANSI constants
# ---------------------------------------------------------------------------
RESET    = "\033[0m"
BOLD     = "\033[1m"
FG_BLACK = "\033[30m"
FG_WHITE = "\033[97m"
FG_GRAY  = "\033[90m"
FG_CYAN  = "\033[96m"
FG_YEL   = "\033[33m"
FG_GREEN = "\033[92m"
FG_RED   = "\033[91m"
FG_MAG   = "\033[95m"

BG_LIGHT  = "\033[48;2;240;240;240m"  # near-white square
BG_DARK   = "\033[48;2;40;40;40m"    # near-black square
BG_HL_L   = "\033[48;2;180;220;180m" # last-move highlight light (green tint)
BG_HL_D   = "\033[48;2;30;100;30m"   # last-move highlight dark (dark green)

# Piece foreground colors — adjusted per square brightness for contrast
FG_W_ON_LIGHT = "\033[38;2;80;80;80m"    # white piece on light sq: dark gray
FG_W_ON_DARK  = "\033[97m"               # white piece on dark sq:  bright white
FG_B_ON_LIGHT = "\033[30m"               # black piece on light sq: true black
FG_B_ON_DARK  = "\033[38;2;190;190;190m" # black piece on dark sq:  light gray

CLEAR_AND_HOME = "\033[2J\033[H"

PIECE_UNICODE = {
    'K': '♔', 'Q': '♕', 'R': '♖', 'B': '♗', 'N': '♘', 'P': '♙',
    'k': '♚', 'q': '♛', 'r': '♜', 'b': '♝', 'n': '♞', 'p': '♟',
}


# ---------------------------------------------------------------------------
# Width measurement — visible terminal columns, ANSI-aware (D5)
# ---------------------------------------------------------------------------
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Unicode variation selectors modify the preceding glyph and add no
# additional terminal column of their own.
_VARIATION_SELECTORS = range(0xFE00, 0xFE10)

# Box drawing (U+2500-257F) and block elements (U+2580-259F) render as
# single-width glyphs here, even though Unicode reports several of them
# (e.g. █, ▲, ▼) as East-Asian-Width "Ambiguous".
_SINGLE_WIDTH_RANGES = ((0x2500, 0x257F), (0x2580, 0x259F))

# Pictographs whose own East-Asian-Width property under-reports their real
# double-width terminal rendering (▲▼ are "Ambiguous", ⚠ is "Neutral").
_PICTOGRAPHS = set("⬜⬛✨💀⚠❌✅💡⭐▲▼🏁")


def _char_width(ch: str) -> int:
    """Return the terminal column width of a single non-ANSI character."""
    code = ord(ch)
    if code in _VARIATION_SELECTORS:
        return 0
    if any(lo <= code <= hi for lo, hi in _SINGLE_WIDTH_RANGES):
        return 1
    if ch in _PICTOGRAPHS:
        return 2
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def visible_width(s: str) -> int:
    """Return the visible terminal column width of s, ignoring ANSI escapes."""
    return sum(_char_width(ch) for ch in _ANSI_RE.sub("", s))


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _truncate_to_width(s: str, width: int) -> str:
    """Truncate s (ANSI-stripped) to at most `width` visible columns."""
    if width <= 0:
        return ""
    out: list[str] = []
    total = 0
    for ch in _ANSI_RE.sub("", s):
        w = _char_width(ch)
        if total + w > width:
            break
        out.append(ch)
        total += w
    return "".join(out)


def effective_width(requested: int) -> int:
    """Clamp a requested column width to the supported [40, 50] band.

    50 is an absolute ceiling, never widened past — the caller-resolved
    `requested` value (an explicit --width flag or terminal-size detection,
    both wired in slice 3b) may only narrow it, never exceed it.
    """
    return _clamp(requested, 40, 50)


def wrap_move_pairs(pair_texts: list[str], width: int) -> list[list[str]]:
    """Greedily group pair_texts onto lines that fit within `width` columns.

    Each line is measured as a 2-column indent plus its pair_texts joined
    by 3 spaces. A single pair is never split across two lines — if one
    pair alone is wider than `width`, it still occupies its own line.
    """
    lines: list[list[str]] = []
    current: list[str] = []
    for text in pair_texts:
        candidate = current + [text]
        candidate_width = 2 + visible_width("   ".join(candidate))
        if current and candidate_width > width:
            lines.append(current)
            current = [text]
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------
def render_board(board: chess.Board, last_uci: str | None = None) -> str:
    """Return the 8×8 ANSI board as a multi-line string."""
    highlight: set[str] = set()
    if last_uci and len(last_uci) >= 4:
        highlight.add(last_uci[:2])
        highlight.add(last_uci[2:4])

    rows = [f"  ┌{'─' * 24}┐"]
    for rank in range(7, -1, -1):
        row = f"{BOLD}{FG_GRAY}{rank + 1}{RESET} │"
        for file in range(8):
            sq      = chess.square(file, rank)
            sq_name = chess.square_name(sq)
            light   = (rank + file) % 2 == 1
            in_hl   = sq_name in highlight

            if in_hl:
                bg = BG_HL_L if light else BG_HL_D
            else:
                bg = BG_LIGHT if light else BG_DARK

            piece = board.piece_at(sq)
            if piece:
                symbol = PIECE_UNICODE[piece.symbol()]
                if piece.color == chess.WHITE:
                    fg = FG_W_ON_LIGHT if light else FG_W_ON_DARK
                else:
                    fg = FG_B_ON_LIGHT if light else FG_B_ON_DARK
                row   += f"{bg}{fg}{BOLD} {symbol} {RESET}"
            else:
                row   += f"{bg}   {RESET}"
        row += f"{BOLD}{FG_GRAY}│{RESET}"
        rows.append(row)

    rows.append(f"  └{'─' * 24}┘")
    rows.append(f"    {'  '.join('abcdefgh')}")
    return "\n".join(rows)


def render_winbar(winrate_white: float, width: int = 50) -> str:
    """Return a text win-probability bar sized to fit within `width` columns."""
    bar_width = _clamp(width - 21, 8, 28)
    w_blocks = round(winrate_white * bar_width)
    b_blocks = bar_width - w_blocks
    bar = (
        f"{BOLD}{FG_WHITE}{'█' * w_blocks}{RESET}"
        f"{BOLD}{FG_GRAY}{'░' * b_blocks}{RESET}"
    )
    w_pct = int(winrate_white * 100)
    b_pct = 100 - w_pct
    return (
        f"  [{bar}]  "
        f"{BOLD}{FG_WHITE}W {w_pct}%{RESET}  /  "
        f"{BOLD}{FG_GRAY}B {b_pct}%{RESET}"
    )


def render_moves(moves_san: list[str], width: int = 50, max_pairs: int = 8,
                  ansi: bool = True) -> str:
    """Return formatted move history (PGN-style pairs), wrapped to width.

    ansi=True colors move numbers/white moves/black moves; ansi=False
    returns the identical visible text with no escape codes, so callers
    like plain_render can delegate instead of duplicating this logic (F8).
    """
    if not moves_san:
        return f"  {FG_GRAY}(no moves yet){RESET}" if ansi else "  (no moves yet)"

    plain_pairs   = []
    colored_pairs = []
    for i in range(0, len(moves_san), 2):
        n       = i // 2 + 1
        white_m = moves_san[i]
        black_m = moves_san[i + 1] if i + 1 < len(moves_san) else "..."
        plain_pairs.append(f"{n}.{white_m} {black_m}")
        colored_pairs.append(
            f"{FG_GRAY}{n}.{RESET}{FG_WHITE}{white_m}{RESET} {FG_GRAY}{black_m}{RESET}"
        )

    shown_plain  = plain_pairs[-max_pairs:]
    shown_output = (colored_pairs if ansi else plain_pairs)[-max_pairs:]
    if len(plain_pairs) > max_pairs:
        shown_plain  = ["..."] + shown_plain
        shown_output = [(f"{FG_GRAY}...{RESET}" if ansi else "...")] + shown_output

    groups = wrap_move_pairs(shown_plain, width)
    lines  = []
    cursor = 0
    for group in groups:
        n = len(group)
        lines.append("  " + "   ".join(shown_output[cursor:cursor + n]))
        cursor += n
    return "\n".join(lines)


def wrap_coaching_lines(coaching: str, width: int) -> list[str]:
    """Greedily wrap coaching prose to at most `width` visible columns.

    Each source line wraps independently, so authored paragraph breaks
    survive instead of collapsing into one run-on block. Words are kept
    whole while they fit; a single token wider than the budget is
    hard-broken, so the bound holds unconditionally rather than as a
    best effort. Width is measured in visible terminal columns (D5), not
    in `len()` — coaching prose carries double-width pictographs.
    """
    if width <= 0:
        return []
    wrapped: list[str] = []
    for source in _ANSI_RE.sub("", coaching.strip()).split("\n"):
        words = source.split()
        if not words:
            wrapped.append("")
            continue
        current = ""
        for word in words:
            while visible_width(word) > width:
                if current:
                    wrapped.append(current)
                    current = ""
                head = _truncate_to_width(word, width)
                wrapped.append(head)
                word = word[len(head):]
            if not word:
                continue
            candidate = f"{current} {word}" if current else word
            if current and visible_width(candidate) > width:
                wrapped.append(current)
                current = word
            else:
                current = candidate
        if current:
            wrapped.append(current)
    return wrapped


def render_coaching(coaching: str, width: int = 50) -> str:
    """Return coaching text lines with yellow color, bounded to `width`.

    Wrapping is decided on the plain text and color is applied per wrapped
    line, so an ANSI run never spans a line boundary and the visible text
    matches the plain path exactly (F8). The 2-column indent is charged
    against the budget.
    """
    lines = wrap_coaching_lines(coaching, width - 2)
    return "\n".join(f"  {FG_YEL}{line}{RESET}" for line in lines)


def render_status(board: chess.Board, state: dict, width: int = 50,
                   ansi: bool = True) -> str:
    """Return the bottom status bar.

    Renders as a single line when it fits within `width` visible columns;
    otherwise stacks onto three lines — turn+check / level+playing /
    mode+opening (D5). The opening name is truncated with an ellipsis so
    the mode+opening line never exceeds `width`. ansi=False returns the
    identical visible text with no escape codes (F8, plain path).
    """
    turn_icon = "⬜" if board.turn == chess.WHITE else "⬛"
    turn_txt  = "White to move" if board.turn == chess.WHITE else "Black to move"
    level     = state.get("level", "?").capitalize()
    mode      = state.get("mode",  "?").capitalize()
    color     = state.get("color", "?").capitalize()
    opening   = state.get("opening", "")
    is_check  = board.is_check()

    def wrap(text: str, code: str) -> str:
        return f"{code}{text}{RESET}" if ansi else text

    turn_str  = wrap(f"{turn_icon} {turn_txt}", f"{BOLD}{FG_CYAN}")
    check_str = f"  {wrap('CHECK!', f'{FG_RED}{BOLD}')}" if is_check else ""
    l1 = f"  {turn_str}{check_str}"

    level_str   = f"Level: {level}"
    mode_str    = f"Mode: {mode}"
    playing_str = f"Playing: {color}"

    # Single-line format (existing wide "  │  " spacing) — used only when it fits.
    tail = wrap(f"│  {level_str}  │  {mode_str}  │  {playing_str}", FG_GRAY)
    single = f"{l1}  {tail}"
    if opening:
        single += f"  {wrap(f'│  {opening}', FG_GRAY)}"
    if visible_width(single) <= width:
        return single

    # Stacked format — narrow " │ " spacing to hit the D5 worst-case bounds.
    l2 = f"  {wrap(f'{level_str} │ {playing_str}', FG_GRAY)}"

    mode_prefix = f"{mode_str} │ "
    if opening:
        budget = width - 2 - visible_width(mode_prefix)  # 2 = line indent
        if visible_width(opening) > budget:
            opening_disp = _truncate_to_width(opening, max(budget - 1, 0)) + "…"
        else:
            opening_disp = opening
        l3_plain = f"{mode_prefix}{opening_disp}"
    else:
        l3_plain = mode_str
    l3 = f"  {wrap(l3_plain, FG_GRAY)}"

    return "\n".join([l1, l2, l3])


# ---------------------------------------------------------------------------
# Plain (no-ANSI) render — suitable for capturing into chat output
# ---------------------------------------------------------------------------
def plain_render(state: dict, width: int = 50) -> str:
    """Return a clean plain-text board with no ANSI codes, bounded to
    `width` visible columns (D5). This is the chat-facing path the user
    actually reads, so it delegates move-history and status rendering to
    the shared width-aware renderers via ansi=False instead of
    duplicating them (F8).
    """
    board     = board_from_state(state)
    records   = state.get("move_records", [])
    wr_white  = records[-1]["winrate_white"] if records else 0.5
    coaching  = records[-1].get("coaching") if records else None

    pieces = {
        'K': '♔', 'Q': '♕', 'R': '♖', 'B': '♗', 'N': '♘', 'P': '♙',
        'k': '♚', 'q': '♛', 'r': '♜', 'b': '♝', 'n': '♞', 'p': '♟',
    }

    lines = []
    lines.append("    a   b   c   d   e   f   g   h")
    lines.append("  ┌───┬───┬───┬───┬───┬───┬───┬───┐")
    for rank in range(7, -1, -1):
        row = f"{rank + 1} │"
        for file in range(8):
            sq = chess.square(file, rank)
            p  = board.piece_at(sq)
            row += f" {pieces[p.symbol()] if p else ' '} │"
        lines.append(row)
        if rank > 0:
            lines.append("  ├───┼───┼───┼───┼───┼───┼───┼───┤")
    lines.append("  └───┴───┴───┴───┴───┴───┴───┴───┘")
    lines.append("    a   b   c   d   e   f   g   h")
    lines.append("")

    w_pct = int(wr_white * 100)
    b_pct = 100 - w_pct
    lines.append(f"  W {w_pct}%  /  B {b_pct}%")
    lines.append("")

    lines.append(render_moves(state.get("moves_san", []), width=width, ansi=False))
    lines.append("")

    if coaching:
        sep = "  " + "─" * (width - 2)
        lines.append(sep)
        for line in wrap_coaching_lines(coaching, width - 2):
            lines.append(f"  {line}")
        lines.append(sep)
        lines.append("")

    lines.append(render_status(board, state, width=width, ansi=False))

    if board.is_game_over():
        lines.append(f"\n  Game over — Result: {state.get('result', '?')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full render
# ---------------------------------------------------------------------------
def full_render(state: dict, do_clear: bool, width: int = 50) -> str:
    board      = board_from_state(state)
    moves_uci  = state.get("moves_uci", [])
    last_uci   = moves_uci[-1] if moves_uci else None
    records    = state.get("move_records", [])
    wr_white   = records[-1]["winrate_white"] if records else 0.5
    coaching   = records[-1].get("coaching") if records else None

    parts = []
    if do_clear:
        parts.append(CLEAR_AND_HOME)

    parts.append(f"\n  {BOLD}{FG_MAG}♟  Chess Coach{RESET}\n")
    parts.append(render_board(board, last_uci))
    parts.append("")
    parts.append(render_winbar(wr_white, width=width))
    parts.append("")
    parts.append(render_moves(state.get("moves_san", []), width=width))

    if coaching:
        sep = f"  {FG_GRAY}{'─' * (width - 2)}{RESET}"
        parts.append(sep)
        parts.append(render_coaching(coaching, width=width))
        parts.append(sep)

    parts.append(render_status(board, state, width=width))
    parts.append("")

    if board.is_game_over():
        result = state.get("result", "?")
        parts.append(f"  {BOLD}{FG_GREEN}🏁 Game over  —  Result: {result}{RESET}\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="ANSI chess board renderer")
    p.add_argument("--state", default="~/.chess_coach/current_game.json")
    p.add_argument("--clear", action="store_true",
                   help="Clear the terminal before rendering (fixed-position effect)")
    p.add_argument("--plain", action="store_true",
                   help="Output plain text with no ANSI codes (for capturing into chat)")
    p.add_argument("--width", type=int, default=None,
                   help="Requested terminal width in columns; always routed through "
                        "effective_width (D5), which clamps to [40, 50] — this flag "
                        "may only narrow the render below 50, never widen past it. "
                        "Defaults to the detected terminal width.")
    args = p.parse_args()
    args.state = os.path.expanduser(args.state)

    requested = (
        args.width if args.width is not None
        else shutil.get_terminal_size(fallback=(80, 24)).columns
    )
    width = effective_width(requested)

    with open(args.state) as f:
        state = json.load(f)

    if args.plain:
        output = plain_render(state, width=width)
    else:
        output = full_render(state, args.clear, width=width)
    sys.stdout.write(output)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
