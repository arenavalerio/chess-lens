# MCP Tool Reference

Full input/output contract for the five tools exposed by the `chess-lens-mcp` server. The [README](../README.md#mcp-tools) carries the at-a-glance table; this document carries the details and JSON samples.

The normative form of this contract lives in [`openspec/specs/chess-analysis-tooling/spec.md`](../openspec/specs/chess-analysis-tooling/spec.md). When the two disagree, the spec wins.

## Shared conventions

The server exposes exactly five tools: `analyze_position`, `analyze_move`, `analyze_game`, `identify_key_moves`, and `fen_from_pgn`. Every call takes a single JSON object and returns a JSON object (or array) directly; invalid input raises a clear error and never fabricates evaluation or feature data.

NNUE scores are relative to the side to move before the candidate move. HCE evaluations and feature totals are White-relative net blends tapered with the material phase in `[0, 24]` and rounded to two decimals; zero and null values are omitted. `hce_deltas` always includes `total` and includes any other feature only when its absolute shift exceeds `0.05`. The `hce` and `missed` maps in `analyze_game` follow the same feature-selection and pruning rule.

## `analyze_position`

Accepts either a complete legal FEN, or exactly one legal PGN with a positive `move_number` and `color` (`white` or `black`). The PGN selector addresses a main-line move by its displayed move number and mover color, and analysis uses the position immediately before that move. Do not combine FEN and PGN inputs. `candidate_count` is optional and positive (default `1`) in either form. It returns the analyzed position's blended `baseline_hce` and up to that many legal moves ranked by NNUE search, each with flattened score fields (`score_cp` and optional `mate_in`), a principal variation (`pv`) capped at 6 ply, and `hce_deltas` relative to baseline. The `fen` result field contains the analyzed position's FEN. A terminal position returns an empty `candidates` list.

Sample input:

```json
{
  "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
  "candidate_count": 2
}
```

Alternatively, select a position directly from the PGN main line:

```json
{
  "pgn": "[Result \"*\"]\n\n1. e4 e5 *",
  "move_number": 1,
  "color": "black",
  "candidate_count": 2
}
```

Sample output:

```json
{
  "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
  "score_perspective": "side to move",
  "baseline_hce": {
    "total": 0.65,
    "Material": 0.07,
    "Pawns": -0.02,
    "Mobility": 0.41,
    "King_safety": 0.07,
    "Threats": -0.02,
    "Space": 0.13
  },
  "candidates": [
    {
      "move": "e7e5",
      "score_cp": -34,
      "pv": ["e7e5", "g1f3", "b8c6", "b1c3", "g8f6", "f1b5"],
      "hce_deltas": {
        "total": -0.65,
        "King_safety": -0.07,
        "Material": -0.07,
        "Mobility": -0.41,
        "Space": -0.13
      }
    },
    {
      "move": "c7c5",
      "score_cp": -45,
      "pv": ["c7c5", "g1f3", "e7e6", "b1c3", "b8c6", "d2d4"],
      "hce_deltas": {
        "total": -0.14,
        "Mobility": -0.1,
        "Space": -0.1
      }
    }
  ]
}
```

## `analyze_move`

Accepts either a complete legal FEN and one legal UCI move, or exactly one legal PGN with a positive `move_number` and `color` (`white` or `black`). PGN selection addresses a main-line move by its displayed move number and mover color; the tool infers the played move's UCI and analyzes it from the position immediately before that move. Do not combine FEN and PGN inputs or provide `move_uci` with PGN input. It returns the analyzed position's `baseline_hce`, the forced-root NNUE `score_cp` (with optional `mate_in`), the move's truncated `pv`, and `hce_deltas` for the resulting position. Its `fen` result field contains the pre-move FEN, and `move` contains the analyzed move in UCI. Invalid positions, selectors, or moves raise a clear error.

Sample input:

```json
{
  "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "move_uci": "e2e4"
}
```

Alternatively, the PGN selector identifies the move to evaluate; no `move_uci` is needed:

```json
{
  "pgn": "[Result \"*\"]\n\n1. e4 e5 *",
  "move_number": 1,
  "color": "black"
}
```

Sample output:

```json
{
  "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "move": "e2e4",
  "score_perspective": "side to move before the move",
  "score_cp": 40,
  "pv": ["e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4"],
  "baseline_hce": {
    "total": 0.0
  },
  "hce_deltas": {
    "total": 0.65,
    "King_safety": 0.07,
    "Material": 0.07,
    "Mobility": 0.41,
    "Space": 0.13
  }
}
```

## `analyze_game`

Accepts exactly one legal PGN game and an optional positive integer `depth` (default `12`), and returns a chronological JSON array with one lean object per main-line ply. The tool runs Stockfish itself at the requested depth, computing NNUE and HCE evidence over FEN-deduplicated positions and comparing each played move against the engine's best move for that ply's pre-move position. Each element is delta-only:

- `ply` — the 1-based ply number.
- `move` — the played move in SAN.
- `best` — the engine's best move in UCI, present only when the played move differs from it.
- `cpl` — the mover-relative centipawn loss `best - played`, present only when non-zero. Mate is folded into centipawns with the existing signed convention `sign * (10000 - 100 * |mate_in|)`.
- `hce` — played-result-minus-baseline blended feature shifts; always includes `total`.
- `missed` — played-result-minus-best blended feature shifts, present only when the played move differs from the engine best.

No principal variation or game metadata is returned. Empty, malformed, illegal, or multiple-game PGNs and non-positive `depth` raise a clear input error and no partial evidence is returned.

> **Migration note:** an earlier `analyze_game` returned a single object with `metadata`, `strategic_snapshots`, and `key_moments`. Callers holding that object shape must migrate to the per-ply array.

Sample input:

```json
{
  "pgn": "[Result \"*\"]\n\n1. e4 e5 *",
  "depth": 12
}
```

Sample output:

```json
[
  {
    "ply": 1,
    "move": "e4",
    "hce": { "total": 0.65, "Material": 0.07, "Mobility": 0.41, "Space": 0.13 }
  },
  {
    "ply": 2,
    "move": "e5",
    "best": "g8f6",
    "cpl": 18,
    "hce": { "total": -0.1, "Mobility": -0.08 },
    "missed": { "total": -0.18, "Mobility": -0.14 }
  }
]
```

## `identify_key_moves`

Accepts exactly one legal PGN, an optional positive integer `depth` (default `12`), and an optional boolean `include_hce` (default `true`). The tool runs Stockfish itself: it evaluates every main-line ply with NNUE at the requested depth, derives each ply's best move, best and second-best evaluations (the second-best is unavailable when the position has a single legal move), and the played move's evaluation (a game-ending move is scored as a synthesized terminal result), then selects commentary-worthy plies. When `include_hce` is `true` it also computes White-relative blended HCE comparisons for each selected ply's pre-move baseline, played-move result, and best-move result; when `false` it runs no HCE evaluation and omits both HCE delta fields. Empty, malformed, illegal, or multi-game PGNs and non-positive `depth` or non-boolean `include_hce` raise a clear input error before any engine analysis.

It returns a chronological JSON array of selected plies with `ply`, the played SAN `move`, best UCI `analysis_move`, primary `type`, `priority`, and `delta_w`. Available values include `sharpness`, `swing`, `best_eval`, `played_eval`, and mover-perspective `nnue_eval_change_cp`; material-equal moments may carry `tags: ["positional_shift"]`. When HCE is enabled, `hce_deltas` (played minus baseline) and `missed_hce_deltas` (played minus best) are included when those comparisons are available. Delta-W, Sharpness, and Swing use `W(e) = 1 / (1 + 10^(-e / 4.0))` for `e = score_cp / 100`; positive mate scores map to `1.0` and negative mate scores to `0.0`. Delta-W and Sharpness are mover-relative, while Swing is White-relative. Primary types are `major_blunder_turning_point` (`delta_w > 0.25`), `brilliant_only_move` (`delta_w < 0.02` and `sharpness > 0.30`), `missed_opportunity` (`delta_w > 0.12`), and `evaluation_swing` (`swing > 0.20`). Selection is capped at `ceil(15 * ceil(plies / 2) / 40)` moments, reserves qualifying positional shifts across represented phases when possible, and never invents a moment to meet the target.

Sample input:

```json
{
  "pgn": "[Result \"*\"]\n\n1. e4 e5 2. Qh5 Nc6 3. Qxf7+ Kxf7 *",
  "depth": 12,
  "include_hce": true
}
```

Sample output:

```json
[
  {
    "ply": 5,
    "move": "Qxf7+",
    "analysis_move": "f1c4",
    "type": "major_blunder_turning_point",
    "priority": 0.8425,
    "delta_w": 0.4212,
    "best_eval": { "score_cp": -34 },
    "played_eval": { "score_cp": -604 },
    "sharpness": 0.0712,
    "swing": 0.4212,
    "nnue_eval_change_cp": 570,
    "hce_deltas": { "total": 0.62, "Material": 0.31, "Queens": 0.17, "Threats": 0.23 },
    "missed_hce_deltas": { "total": -0.55, "Bishops": 0.06, "King_safety": -1.12, "Material": 0.24, "Queens": 0.17, "Threats": 0.21 }
  }
]
```

## `fen_from_pgn`

Accepts exactly one legal PGN game, a positive move number, and a color (`white` or `black`). It returns the board position immediately before that side's move at that move number. Malformed, illegal, multiple-game, or out-of-range selections raise a clear error.

Sample input:

```json
{
  "pgn": "[Event \"Sample\"]\n[Result \"1-0\"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n",
  "move_number": 1,
  "color": "black"
}
```

Sample output:

```json
{
  "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
}
```

## Workflows

For a position, pass its FEN to `analyze_position` with `candidate_count=3` to get the baseline and top candidate moves. Or pass a PGN with `move_number` and `color` to either analysis tool; the selector identifies a main-line move and analysis starts from its pre-move position. Use `analyze_move` with FEN plus UCI for a specified legal move, or with PGN selection to infer and evaluate the played move. A terminal position returns no candidate moves.

For a game, pass the legal PGN to `analyze_game` to get the full chronological per-ply timeline, or to `identify_key_moves` to get only the selected commentary-worthy moments; both compute their NNUE and HCE evidence internally. Use `fen_from_pgn` when you need the pre-move FEN itself.
