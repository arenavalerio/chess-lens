---
name: analyze-chess-game
description: "Comment one legal chess game, annotating only the moves that matter, and return a commented PGN grounded in MCP engine evidence."
argument-hint: "Paste one PGN game, or provide a path to a .pgn file."
---

# Analyze a Chess Game

Use this skill when the user supplies one chess game as PGN text or as a path to a PGN file and asks for commentary. Return the complete input game as a commented PGN that annotates only the moves worth explaining, grounded in MCP engine evidence. Work only from returned evidence; never invent an evaluation, feature, better move, or continuation.

## Input Handling

1. Accept either PGN text or a path to a `.pgn` file. If given a path, read the file; if the path does not exist or cannot be read, report that as a clear error and stop.
2. Require exactly one legal game. Reject and stop with a clear, specific error when the input is:
   - empty;
   - malformed or unparseable;
   - illegal (an illegal move or illegal position);
   - more than one game.
3. On any rejection, return only the error message. Do not return a partial, repaired, or fabricated PGN.

## Evidence Procedure

Gather evidence in tiers, calling each tool only for what a ply actually needs:

1. Call `analyze_game` once with the full game PGN. It returns a chronological per-ply array. Each element carries `ply` and the played SAN `move`, always carries `hce` (played-result minus pre-move-baseline White-relative blended feature shifts, always including `total`), and only when the played move differs from the engine best also carries `best` (the engine move in UCI), mover-relative `cpl`, and `missed` (played-result minus best-result shifts). Use it to rank candidate plies for commentary and to size errors for glyphs.
2. Call `identify_key_moves` once with the same PGN at the default depth. It returns the selected turning points, each with `ply`, `move`, `analysis_move`, `type`, `delta_w`, `priority`, `best_eval`, `played_eval`, and, when available, `sharpness`, `swing`, `nnue_eval_change_cp`, `tags`, `hce_deltas`, and `missed_hce_deltas`. Treat this list as the primary shortlist of moves to comment, and use its `brilliant_only_move` classification to justify `!`/`!!`.
3. Only when a selected ply's comment needs a concrete continuation or a clearer alternative, call `fen_from_pgn` to obtain that ply's pre-move FEN, then call `analyze_position` (for ranked candidates and a `pv`) or `analyze_move` (to score one legal UCI move). Keep these calls targeted; do not run them for every ply.

If required engine evidence is unavailable for a ply, either omit the comment or say the point cannot be substantiated. Never substitute an invented score, feature, value, or move.

## Selecting Moves to Comment

Do not annotate every ply. A comment earns its place only when it teaches the reader something about the position. Select plies in this order of priority:

1. **Outcome-deciding moves:** the mating or drawing move, a decisive blunder, a winning sacrifice, a resignation-worthy turn.
2. **`identify_key_moves` turning points:** every ply marked `brilliant_only_move`, plus the highest-priority entries of other types.
3. **Errors that change the character of the game:** roughly `cpl` 100 and above, or `cpl` 50 and above when the move also abandons a concrete plan, king safety, material, or structure.
4. **Plan markers:** the move that commits to a structure, the first real departure from theory, the move that creates or breaks a bind, the start of a pawn storm, the transition into an endgame.

Leave uncommented: book moves, routine recaptures, obvious developing moves, moves that follow an already-explained plan, and any move whose only story would be that it matched or missed the engine's top line. Aim for roughly one comment per three to five plies; a 40-ply game should normally carry about 8–12 comments. Fewer, sharper comments beat full coverage.

At most one comment per ply. Adjacent comments are appropriate when a short sequence is decisive, for example a sacrifice and its forced reply.

## Comment Style

Write like a coach explaining ideas, not like an engine readout.

- Lead with the move's purpose: the plan it serves, the threat it creates, the square or file it fights for, or the weakness it concedes.
- For an error, state the consequence in chess terms first, then name the alternative as the idea that avoided it. Name a better move only when its purpose can be stated concretely.
- Never write engine-speak inside a comment: no "the engine prefers", "the engine's choice", "best move", "engine best", "centipawns", or `cpl`.
- When the comment's point rests on an NNUE score or an HCE feature shift, close it with one brief source-tagged note in pawns, for example `(NNUE -2.1)`, `(NNUE +2.1 to -2.1)`, `(HCE total -0.4)`, or `(HCE king safety -1.1)`. Every score is White-relative.
- Do not praise or blame a move merely for matching or missing the top line. A comment must add a plan, a threat, a structural consequence, or a tactical point.
- Prefer concrete chess nouns: the file, the diagonal, the outpost, the weak square, the defender, the pawn break.

Good: `13...Rxd7?` — "The rook recapture drags the last defender off d8; 13...Nxd7 keeps the rook at home and the king's cover intact. (NNUE -0.4 to +1.3)"

Bad: "13...Rxd7? The engine prefers 13...Nxd7 by 113 centipawns."

## Evidence Mapping

Use the returned fields to decide what is true, then say it in chess language:

- **`hce` deltas:** the concrete changes the move made (space, activity, structure, king safety, material). These supply the plan or the cost you describe.
- **`best` plus `missed`:** what the played move gave up; `missed` is played-result minus best-result, so a negative total means the alternative preserved more. Translate the largest `missed` concepts into the concrete thing the alternative held.
- **`cpl`:** how large the error is. It sizes the wording and the glyph; it never appears in the comment.
- **Score notes:** `best_eval` and `played_eval` are NNUE scores; label them `NNUE`. `hce` and `missed` are HCE feature shifts; label them `HCE` with the feature you cite. `analyze_position`'s `score_cp` is side-to-move relative, so convert it to White's view before quoting it.
- **`identify_key_moves` type and `sharpness`:** the classification behind `!`/`!!` and how emphatically the move can be described.

## Move-Quality Glyphs (main line only)

Map mover-relative `cpl` to standard glyphs:

| `cpl` | glyph |
| --- | --- |
| below 50, or unavailable | none |
| 50–99 | `?!` |
| 100–199 | `?` |
| 200 or more | `??` |

- Reserve `!` and `!!` for a ply that `identify_key_moves` classifies as `brilliant_only_move`. Use `!` for that classification and `!!` when the classification is exceptional (sharpness at least `0.50`). Never assign `!`/`!!` from a best-move match, a missing `best`, or a low `cpl` alone.
- When `cpl` is unavailable for a main-line move, assign no glyph; do not infer one from the missing value.
- Apply glyphs to main-line moves only. Replace any existing main-line quality glyph with the computed glyph, or remove it when the computed result is none. Leave variation glyphs and annotations unchanged.

## Comment Content Rules

- **Selected plies only.** Insert a comment only after a selected main-line move; leave all other moves bare. Never comment inside a variation.
- **Length.** Cap each comment, including its score note, at 35 whitespace-separated words. When shortening, cut at a sentence boundary; never cut inside a word.
- **Grounding without jargon.** Every claim must trace to returned evidence, expressed as chess reasoning. Render a `best` UCI as SAN only when the conversion is unambiguous; otherwise describe the idea without asserting a specific square. Never emit a malformed SAN as if it were exact.

## Accessibility and Perspective

- Use the HCE feature deltas to choose what to say, but write only chess concepts: material, piece activity, king safety, threats and tactics, space, pawn structure, and the relevant piece quality. Raw feature names, signed decimals, and the `total` field appear only inside the single score note.
- HCE feature deltas are White-relative. When the commented move is Black's, reverse their reading: a White-positive shift is bad for Black and a White-negative shift is good for Black. Describe Black's move from Black's point of view.
- Score notes stay White-relative for both colors; only the prose around them takes the mover's perspective.
- `cpl` is already mover-relative, so for either color a positive loss is a loss for the mover. Do not re-sign it.

## Terminal Moves

- A terminal main-line ply is always selected for commentary, even when no other selection rule applies.
- If a main-line move delivers checkmate, state that it checkmates and stop; do not invent a continuation.
- If a main-line move produces stalemate or another drawn ending, state the drawn outcome. Do not fabricate a continuation, an evaluation, or a better move.

## Output Assembly

Edit the supplied movetext in place rather than rebuilding the game from evidence:

1. Start from the input game's headers and movetext. Preserve every header, the result token, the main-line move order, and all side variations together with their annotations and glyphs.
2. Discard every comment present in the input, in both the main line and inside variations.
3. Insert a `{comment}` after each selected main-line move token and nowhere else. Do not pad the game to reach a comment count.
4. Never emit `{`, `}`, or a line break inside a comment.
5. Self-check before returning: no ply carries more than one comment, every comment sits on a ply that meets the selection rules, the density is plausible for the game length, every `{` has a matching `}` with no nesting, and every score note names its source and matches the returned numbers.

## Output Contract

- Return the complete commented PGN in the chat, inside a fenced code block the user can copy. Do not write a file by default and do not create side-effect files.
- Save a file only when the user explicitly asks. When saving, write the commented PGN next to the input file (or to the path the user names) and never overwrite an existing file; use a new name or ask first.
- If the commentary is sparse, that is correct behaviour, not an omission. Do not apologise for uncommented moves or offer to annotate more unless the user asks.
