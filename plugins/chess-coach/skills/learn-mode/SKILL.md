---
name: learn-mode
description: "Trigger: learn chess, chess lessons, resume chess lesson, guided chess practice. Run staged script-verified Learn Mode."
license: Apache-2.0
metadata:
  author: "bl00p1ng"
  version: "1.0"
---

# Learn Mode

## Activation Contract

Use this skill when a user asks to learn chess from the beginning, continue a lesson, or enter guided practice. Keep all learner-facing narration as self-contained English prose.

## Hard Rules

- Start with exactly one status call. Treat the script verdict as authoritative. Do not independently judge whether a move, quiz answer, bridge, or completion passed.
- Narrate returned lesson text and verdicts; never expose internal lesson IDs or predicate names as learner-facing explanations.
- Use only `lesson.py` bridge commands to mutate guided-play state. Never add `--state` to `bridge_eval`, `bridge_move`, or `bridge_ai`; they bind the lesson state in code.
- Bridge objectives are redirected by `attempt`; `attempt` never evaluates them. Use the bound `bridge_eval`, `bridge_move`, and `bridge_ai` commands for guided play.
- Use `render.py --state ~/.chess_coach/current_lesson.json` only for read-only lesson rendering. Never read or write `current_game.json` during a lesson.

## Decision Gates

| Status result | Action |
|---|---|
| `active` is present | Offer to resume it first; render only after acceptance. |
| No `active`, `next_lesson` is present | Offer that lesson; do not select a later locked lesson. |
| No `active` and no `next_lesson` | Offer graduation; require an explicit acceptance before handoff. |

## Execution Steps

1. Run:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lesson.py" status
   ```
   Read `active`, `next_lesson`, and the script-computed progress.
2. On an accepted resume, render the saved lesson:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render.py" --plain --state ~/.chess_coach/current_lesson.json
   ```
   On an accepted new lesson, start the `next_lesson.id` returned by status:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lesson.py" start --id <lesson-id>
   ```
   Narrate its returned goal.
3. For a scored drill, narrate only the returned result. Give a hint without changing eligibility:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lesson.py" hint
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lesson.py" attempt --move <uci>
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lesson.py" attempt --squares <square1,square2,...>
   ```
4. For a guided-play bridge, evaluate first, ask whether to commit, then make the exchange and render it:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lesson.py" bridge_eval --move <uci>
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lesson.py" bridge_move --move <uci>
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lesson.py" bridge_ai
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render.py" --plain --state ~/.chess_coach/current_lesson.json
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lesson.py" status
   ```
   Let the final status decide bridge completion.
5. When status shows no active or next lesson, say: Ask: "You completed the curriculum. Would you like to start Coach or Play from the standard chess position?" Do not start Coach or Play automatically. Never run `engine.py new_game` before explicit graduation acceptance. Only after an explicit yes, Load the `chess-coach` skill and continue its selected normal-mode flow.

## Output Contract

State the script result, one next action, and any board render verbatim in a fenced block. Keep the graduation offer separate from a handoff and wait for the user's explicit answer.

## References

- [`../chess-coach/SKILL.md`](../chess-coach/SKILL.md) — normal Coach and Play handoff.
- [`../../scripts/lesson.py`](../../scripts/lesson.py) — authoritative lesson command surface.
