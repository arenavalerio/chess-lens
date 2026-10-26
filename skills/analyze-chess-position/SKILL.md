---
name: analyze-chess-position
description: "Analyze a validated chess FEN with Stockfish HCE data; return a grounded evaluation, White and Black plans, and up to three next moves with 300-word caps."
argument-hint: "Provide one complete FEN string."
---

# Analyze a Chess Position

Use this skill when a user supplies one FEN and asks for a positional evaluation, plans for both colors, or candidate moves. Return the sections `Evaluation`, `White Plans`, `Black Plans`, and `Next Move`.

## Procedure

1. Validate the input as a complete, legal six-field FEN. Reject malformed positions and do not invent analysis.
2. Determine the side to move, terminal status, material balance, and game phase.
3. Call `analyze_position` with `candidate_count=3`. It returns blended `baseline_hce` features and up to three legal moves ranked by NNUE, each with flattened score fields (`score_cp` and optional `mate_in`), truncated PV (capped at 6 ply), and candidate `hce_deltas`.
4. Call `analyze_move` only when the user asks about a specific legal move not included in the returned candidates. Use its NNUE `score_cp`, truncated PV, and resulting `hce_deltas` relative to `baseline_hce`; do not use shell commands or the retired split-tool workflow.
5. Synthesize the four fields from returned evidence. Preserve score perspectives and do not add unsupported claims.
6. Cap each field at 300 whitespace-separated words. For terminal positions, report the state and return no candidate moves.

## Output Contract

- `Evaluation`: State the side to move, material and positional balance, game phase, and dominant factors. Ground baseline evaluation in `baseline_hce` (which is White-relative). Candidate NNUE scores (`score_cp`) are from the pre-move side-to-move perspective.
- `White Plans`: Give 1-3 concrete plans grounded in `baseline_hce` features or a candidate PV.
- `Black Plans`: Give 1-3 concrete plans grounded in `baseline_hce` features or a candidate PV. Include defense or counterplay when Black is worse.
- `Next Move`: List up to three legal moves in returned NNUE rank order, with UCI or SAN, score (`score_cp`), truncated PV, and concise positional or tactical reasoning grounded in candidate `hce_deltas` (e.g., shifts in mobility, threats, king safety, or material).

## Grounding Rules

- Ground current position evaluations and plans in `baseline_hce` features, and explain candidate moves using `hce_deltas` (noting specific feature trade-offs), score evidence (`score_cp`), or PV continuations. HCE feature values and deltas are White-relative; reverse the sign when describing Black's perspective.
- White and Black plans must reflect their respective imbalances and must not be verbatim copies.
- A losing side must include at least one defensive or counterplay plan.
- Do not fabricate evaluations, alternatives, feature values, or plans. Report engine/model failures without partial fabricated values.

## Word Caps

Each field is limited to 300 words. Prefer a sentence boundary when shortening; never cut inside a word.
