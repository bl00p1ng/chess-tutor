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


def render_moves(moves_san: list[str], width: int = 50, max_pairs: int = 8) -> str:
    """Return formatted move history (PGN-style pairs), wrapped to width."""
    if not moves_san:
        return f"  {FG_GRAY}(no moves yet){RESET}"

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

    shown_plain   = plain_pairs[-max_pairs:]
    shown_colored = colored_pairs[-max_pairs:]
    if len(plain_pairs) > max_pairs:
        shown_plain   = ["..."] + shown_plain
        shown_colored = [f"{FG_GRAY}...{RESET}"] + shown_colored

    groups = wrap_move_pairs(shown_plain, width)
    lines  = []
    cursor = 0
    for group in groups:
        n = len(group)
        lines.append("  " + "   ".join(shown_colored[cursor:cursor + n]))
        cursor += n
    return "\n".join(lines)


def render_coaching(coaching: str) -> str:
    """Return coaching text lines with yellow color."""
    lines = coaching.strip().split("\n")
    return "\n".join(f"  {FG_YEL}{line}{RESET}" for line in lines)


def render_status(board: chess.Board, state: dict) -> str:
    """Return the bottom status bar."""
    turn     = "⬜ White to move" if board.turn == chess.WHITE else "⬛ Black to move"
    level    = state.get("level", "?").capitalize()
    mode     = state.get("mode",  "?").capitalize()
    color    = state.get("color", "?").capitalize()
    opening  = state.get("opening", "")
    check    = f"  {FG_RED}{BOLD}CHECK!{RESET}" if board.is_check() else ""
    opening_str = f"  {FG_GRAY}│  {opening}{RESET}" if opening else ""
    return (
        f"  {BOLD}{FG_CYAN}{turn}{RESET}{check}"
        f"  {FG_GRAY}│  Level: {level}  │  Mode: {mode}  │  Playing: {color}{RESET}"
        f"{opening_str}"
    )


# ---------------------------------------------------------------------------
# Plain (no-ANSI) render — suitable for capturing into chat output
# ---------------------------------------------------------------------------
def plain_render(state: dict) -> str:
    """Return a clean plain-text board with no ANSI codes."""
    board     = board_from_state(state)
    moves_uci = state.get("moves_uci", [])
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

    moves_san = state.get("moves_san", [])
    if moves_san:
        pairs = []
        for i in range(0, len(moves_san), 2):
            n      = i // 2 + 1
            white_m = moves_san[i]
            black_m = moves_san[i + 1] if i + 1 < len(moves_san) else "..."
            pairs.append(f"{n}. {white_m} {black_m}")
        shown = pairs[-8:]
        if len(pairs) > 8:
            shown = ["..."] + shown
        lines.append("  " + "   ".join(shown))
        lines.append("")

    if coaching:
        lines.append("  " + "─" * 52)
        for line in coaching.strip().split("\n"):
            lines.append(f"  {line}")
        lines.append("  " + "─" * 52)
        lines.append("")

    turn    = "⬜ White to move" if board.turn == chess.WHITE else "⬛ Black to move"
    level   = state.get("level", "?").capitalize()
    mode    = state.get("mode",  "?").capitalize()
    color   = state.get("color", "?").capitalize()
    check   = "  CHECK!" if board.is_check() else ""
    lines.append(f"  {turn}{check}  |  Level: {level}  |  Mode: {mode}  |  Playing: {color}")

    if board.is_game_over():
        lines.append(f"\n  Game over — Result: {state.get('result', '?')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full render
# ---------------------------------------------------------------------------
def full_render(state: dict, do_clear: bool) -> str:
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
    parts.append(render_winbar(wr_white))
    parts.append("")
    parts.append(render_moves(state.get("moves_san", [])))

    if coaching:
        sep = f"  {FG_GRAY}{'─' * 52}{RESET}"
        parts.append(sep)
        parts.append(render_coaching(coaching))
        parts.append(sep)

    parts.append(render_status(board, state))
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
    args = p.parse_args()
    args.state = os.path.expanduser(args.state)

    with open(args.state) as f:
        state = json.load(f)

    if args.plain:
        output = plain_render(state)
    else:
        output = full_render(state, args.clear)
    sys.stdout.write(output)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
