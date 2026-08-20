# ♟ claude-chess

> I got tired of alt-tabbing between my terminal and a chess website. So I built a chess coach that lives inside Claude Code.

> **This is a fork of [yongqyu/claude-chess](https://github.com/yongqyu/claude-chess).**
> Upstream assumes you already know how to play. This fork adds **Learn Mode**:
> a lesson curriculum that teaches chess from zero, so you can arrive at the
> board without knowing how a pawn moves. Everything upstream does, it still does.

---

## What is this?

It's a Claude Code plugin that lets you play chess — right in your terminal, with Claude as your coach.

After every move, Claude tells you if it was good or terrible, shows you what you *should* have played, and explains why it made its own move. When you're done, it saves a full game review to a Markdown file, including your estimated ELO.

And if you've never played at all, you don't need to find a tutorial first. Say you want to learn, and Claude runs you through a ten-lesson curriculum before you ever face an opponent. That part is what this fork adds — see [Learn Mode](#learn-mode--from-zero) below.

No Stockfish required. No separate app. Just Claude.

---

## What it looks like in practice

The board appears in two places, each optimized for its context.

**In the Claude Code chat** — plain Unicode, readable inline after every move:

```
    a   b   c   d   e   f   g   h
  ┌───┬───┬───┬───┬───┬───┬───┬───┐
8 │ ♜ │ ♞ │ ♝ │ ♛ │ ♚ │ ♝ │ ♞ │ ♜ │
  ├───┼───┼───┼───┼───┼───┼───┼───┤
7 │ ♟ │ ♟ │ ♟ │ ♟ │   │ ♟ │ ♟ │ ♟ │
  ├───┼───┼───┼───┼───┼───┼───┼───┤
6 │   │   │   │   │   │   │   │   │
  ├───┼───┼───┼───┼───┼───┼───┼───┤
5 │   │   │   │   │ ♟ │   │   │   │
  ├───┼───┼───┼───┼───┼───┼───┼───┤
4 │   │   │   │   │   │   │   │   │
  ├───┼───┼───┼───┼───┼───┼───┼───┤
3 │   │   │   │   │   │ ♘ │   │   │
  ├───┼───┼───┼───┼───┼───┼───┼───┤
2 │ ♙ │ ♙ │ ♙ │ ♙ │ ♙ │ ♙ │ ♙ │ ♙ │
  ├───┼───┼───┼───┼───┼───┼───┼───┤
1 │ ♖ │ ♘ │ ♗ │ ♕ │ ♔ │ ♗ │   │ ♖ │
  └───┴───┴───┴───┴───┴───┴───┴───┘
    a   b   c   d   e   f   g   h

  W 54%  /  B 46%

  1. Nf3 e5

  ────────────────────────────────────────────────────
  AI played e5.
    Pawn e7 → e5.
    Maintains balance while staying active.
  Win rate (White): 54%
  ────────────────────────────────────────────────────

  ⬜ White to move  |  Level: Intermediate  |  Mode: Play  |  Playing: White
```

**In the terminal** (press `Ctrl+O` in Claude Code, or run in a plain terminal) — full color ANSI board with highlighted last move, colored squares, and win-probability bar:

<img width="1196" height="790" alt="image" src="https://github.com/user-attachments/assets/d816f062-38a9-4557-b464-c573d45b9bad" />

ANSI color codes are terminal-only — they can't render in markdown chat. The chat board gives you what you need at a glance; the terminal board is there when you want the full visual.

---

## Learn Mode — from zero

Say **"I want to learn chess"** and Claude runs the curriculum. Ten lessons,
four stages, unlocked in order:

| Stage | Lessons | What you practice |
|---|---:|---|
| `board-and-pieces` | 4 | How the pawn, knight, bishop and rook move |
| `special-rules` | 2 | Pawn captures and castling |
| `material-and-mate` | 2 | The queen's reach, and mate in one |
| `guided-play` | 2 | Your first real exchanges, with the coach beside you |

### What a lesson actually is

A tiny position, a goal in plain English, and a check the script can run.
The first one puts a single pawn on the board and says:

> Move your pawn from the square e2 to the square e4 in one move.

Note what it does *not* say: no "advance the e-pawn two squares", no algebraic
notation you haven't been taught yet. Every lesson names squares the long way
and defines its own vocabulary, so you can read it cold.

You get **three attempts** on a drill. Then:

- **An illegal move costs you nothing.** If you try something the rules don't
  allow, it tells you and your attempts stay where they were. Not knowing the
  rules yet is the entire point — it can't be a penalty.
- **A legal move that misses the goal costs one.** Strength doesn't matter,
  only whether it did what the lesson asked. A brilliant move that ignores the
  goal is still a miss.
- **Hints are free.** They never cost an attempt and never block you from
  completing the lesson. Ask for all of them if you want.

Some lessons are quizzes instead: *name every square this piece can move to.*
Your answer has to be the exact set — no missing squares, no extras. While
you're still trying, it tells you how many you got and nothing else; the full
answer only appears once the lesson is over.

### Guided play

The last two lessons are real games, played a few moves at a time. Here you
can ask what a move would do **before committing to it** — the coach evaluates
it, you decide, and only then does it get played. It's the bridge between
solving positions and actually playing.

### The rules underneath

- **The script decides, Claude narrates.** Whether you solved a lesson is a
  verdict from `lesson.py`, never Claude's opinion. It cannot be talked into
  passing you, and it won't drift between sessions.
- **Your game is untouchable.** Lesson state lives in its own file. You can
  have a game in progress, do three lessons, and come back to it
  byte-for-byte unchanged. Lessons don't need a game to exist, either.
- **Graduation is opt-in.** Finishing the last lesson *offers* you a real
  game and waits. It never starts one on its own.
- **Replaying is free.** Redo any lesson you've already passed; your progress
  doesn't move backwards.

Progress lives in `~/.chess_coach/learning.json`. Come back a week later, say
you want to continue, and it resumes exactly where you stopped.

---

## Features

- **Learn from zero** — a ten-lesson beginner curriculum with script-verified objectives, free hints, and guided first games
- **Two board views** — plain Unicode board in the chat after every move; full color ANSI board with highlighted last move and win-probability bar in the terminal (`Ctrl+O` in Claude Code)
- **Personas** — play against historical chess legends (Fischer, Tal, Petrosian, Carlsen), each with their own opening repertoire, aggression level, and coaching voice; or extract a persona from any game record collection
- **Real-time coaching** — rates every move (brilliant ✨ / good ✅ / inaccuracy ⚠️ / mistake ❌ / blunder 💀), shows win probability shift, and suggests better alternatives
- **AI explains itself** — after every AI move, Claude tells you *why* it played that, in the active persona's voice
- **Opening detection** — recognizes 20 common openings and names them as they appear
- **ELO tracking** — estimates your ELO from each game using average centipawn loss + blunder rate, smoothed across sessions
- **Auto difficulty** — reads your game history at startup and sets difficulty to match your level
- **Game reviews** — saves a Markdown file after each game with your PGN, an ASCII win-probability chart, a full annotated move table, and a blunder breakdown
- **Persistent profile** — everything lives in `~/.chess_coach/`, survives restarts, no setup between sessions

---

## Getting started

Install the plugin from this repository's marketplace:

```
/plugin marketplace add bl00p1ng/chess-tutor
/plugin install chess-coach@chess-tutor
```

Then install the one Python dependency:

```bash
pip install chess --break-system-packages -q
```

Start a new Claude Code session and say either:

> **"I want to learn chess"** — starts the lesson curriculum from zero.

> **"Let's play chess"** — jumps straight into a game.

That's it. For lessons, Claude picks up wherever you left off. For a game, it
asks your color preference, checks your game history, sets difficulty, and
opens the board.

Installing is a deliberate, separate step: cloning this repository does not
load anything into a Claude Code session on its own.

---

## Personas

Instead of playing against a generic AI, choose a persona — a bot with a distinct playing style, opening repertoire, and character voice.

At the start of every game Claude asks: **"Play against the standard AI, or choose a persona?"**

**Bundled personas:**

| Persona | Style | Aggression | Depth |
|---------|-------|-----------|-------|
| Bobby Fischer | Precise, open games, e4 | High | 3 |
| Mikhail Tal | Sacrificial chaos, king attacks | Max | 3 |
| Tigran Petrosian | Prophylactic, exchange-heavy | Low | 3 |
| Magnus Carlsen | Universal, endgame-dominant | Medium | 3 |

Each persona narrates the game in their own voice — Fischer is cold and clinical, Tal is gleefully provocative, Petrosian is quiet and ominous.

**Extract your own persona** from your game history or any PGN file:

> *"Extract a persona from my games"*

Or from a PGN file of a historical player:

> *"Extract a persona from Magnus's games"* (then provide the PGN path)

See [PERSONAS.md](PERSONAS.md) for the full details on how personas work and how to create them.

---

## How to make moves

You can type moves however feels natural:

| Format | Example |
|--------|---------|
| Standard notation | `e4`, `Nf3`, `O-O`, `Rxe5` |
| UCI | `e2e4`, `g1f3` |
| Plain English | `"kingside castle"`, `"pawn to e4"`, `"knight to f3"` |

---

## ELO estimation

At the end of each game, your ELO is estimated from your moves (not the AI's):

```
ELO ≈ 1800 − (avg centipawn loss × 6) − (blunder rate% × 40)
```

This is based on Guid & Bratko (2006) and Lichess ACPL research — the same approach Lichess uses to estimate strength from game accuracy. It gets more accurate the more games you play.

Your history is smoothed over your last 5 games and used to auto-set difficulty next time:

| Your ELO | Difficulty | Engine behavior |
|----------|-----------|-----------------|
| < 900 | Beginner | Plays suboptimally on purpose |
| 900–1300 | Intermediate | Solid, no deliberate mistakes |
| > 1300 | Advanced | Thinks 3 moves ahead |

---

## How it's built

Everything is plain Python scripts. Claude just calls them in sequence and translates the JSON output into natural language.

```
scripts/
  common.py       Evaluation, minimax, opening DB, ELO formula
  engine.py       Move validation, AI moves (--persona flag), game state
  coach.py        Move quality, coaching text, annotations
  render.py       Board renderer — `--plain` for chat, `--clear` for ANSI terminal
  profile.py      ELO history, difficulty recommendation
  review.py       End-of-game Markdown review
  persona.py      Persona management — list, show, extract, import PGN
  pgn_adapter.py  Converts PGN files to internal game record format

personas/
  fischer.json    Bobby Fischer
  tal.json        Mikhail Tal
  petrosian.json  Tigran Petrosian
  carlsen.json    Magnus Carlsen
```

User-extracted personas are saved to `~/.chess_coach/personas/` and override bundled ones with the same ID.

All game state is saved to `~/.chess_coach/current_game.json` after every move. If Claude loses context mid-game (long sessions), it recovers instantly by reading the file — you won't lose your position.

---

## Honest limitations

- The engine is a custom minimax (no Stockfish). At max depth it plays around 1200–1400 ELO — enough to beat beginners and challenge intermediate players, but a strong player will find it easy.
- ELO estimates are ballpark figures, not official ratings. They're most useful for tracking your own improvement over time.
- Needs a terminal with ANSI + Unicode support (basically any modern terminal on Mac/Linux).

---

## Ideas for contributors

- Stockfish integration (optional, for stronger analysis)
- More bundled historical personas (Kasparov, Karpov, Morphy…)
- More openings in the detection database
- Endgame and tactical pattern coaching
- A `--flip` flag to render the board from Black's perspective
- Persona vs persona simulation (two bots)

PRs welcome!

---

## Requirements

- Claude Code
- Python 3.10+
- `pip install chess`

---

## License

MIT — see [LICENSE](LICENSE).

This project is a fork of [yongqyu/claude-chess](https://github.com/yongqyu/claude-chess). Upstream's README states MIT, but upstream does not publish a `LICENSE` file. This fork adds one, crediting the original author.
