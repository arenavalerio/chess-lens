# chess-analysis-tooling Specification

## Purpose
This capability provides compact, directly consumable evidence for the chess game-analysis skill and batches its HCE requests to reduce unnecessary response volume, process startup, and tool round trips.

## Requirements

### Requirement: Expose exactly five task-oriented MCP tools
The MCP server SHALL expose exactly `analyze_position`, `analyze_move`, `analyze_game`, `identify_key_moves`, and `fen_from_pgn`. It SHALL NOT expose other replaced tool names or compatibility aliases.

#### Scenario: List the replacement tools
- **WHEN** an MCP client lists available tools
- **THEN** it SHALL receive exactly the five specified tool names

#### Scenario: Reject removed tool names
- **WHEN** an MCP client calls a replaced tool name other than `analyze_game`
- **THEN** the server SHALL report that the tool is unavailable

### Requirement: Analyze a position and candidate move with NNUE and HCE evidence
`analyze_position` SHALL accept a complete legal FEN and an optional positive candidate count defaulting to 1. It SHALL return the input position's tapered blended `baseline_hce` and up to that many legal moves ranked by NNUE search, each with flattened score fields (`score_cp` and optional `mate_in`), a truncated principal variation (`pv` capped at 6 ply), and an `hce_deltas` object.

`analyze_move` SHALL accept a complete legal FEN and one legal candidate move. It SHALL return the input position's `baseline_hce`, the move's flattened score fields, truncated `pv` (capped at 6 ply), and resulting `hce_deltas`.

HCE feature blending SHALL calculate a single float per feature using non-pawn material phase $P \in [0, 24]$ as `((MG * Phase) + (EG * (24 - Phase))) / 24`, rounded to two decimal places. Features with a blended value of `0.00` or `null` SHALL be omitted. Features SHALL use net Total values from White's perspective and normalized Stockfish feature names with underscores (e.g., `King_safety`).

`hce_deltas` SHALL always include `"total"` representing the net shift in composite evaluation. Other features SHALL be included in `hce_deltas` if and only if the absolute change between the post-move position and the baseline position exceeds 0.05. NNUE scores SHALL be relative to the side to move before the candidate move.

#### Scenario: Return the default top candidate
- **WHEN** `analyze_position` receives a valid non-terminal FEN without a candidate count
- **THEN** it SHALL return the input position's blended `baseline_hce` and the highest-ranked legal move with `score_cp`, `pv` capped at 6 ply, and `hce_deltas` relative to baseline

#### Scenario: Return the requested candidate count
- **WHEN** `analyze_position` receives a valid FEN and positive candidate count
- **THEN** it SHALL return no more than that many legal moves in NNUE rank order, each with `score_cp`, `pv` capped at 6 ply, and `hce_deltas` relative to baseline

#### Scenario: Evaluate a specified legal move
- **WHEN** `analyze_move` receives a valid FEN and legal candidate move
- **THEN** it SHALL return the input position's `baseline_hce`, the move's NNUE `score_cp`, `pv` capped at 6 ply, and `hce_deltas` relative to baseline

#### Scenario: Include total in candidate deltas
- **WHEN** `analyze_position` or `analyze_move` evaluates a candidate move whose total evaluation delta has an absolute value less than or equal to 0.05
- **THEN** `hce_deltas` SHALL still include `"total"`

#### Scenario: Prune zero and negligible feature deltas
- **WHEN** a non-total HCE feature shifts by 0.05 or less between baseline and candidate move
- **THEN** that feature SHALL be omitted from `hce_deltas`

#### Scenario: Reject invalid positions and moves
- **WHEN** either tool receives an invalid FEN or `analyze_move` receives an invalid or illegal candidate
- **THEN** it SHALL return a clear input error and SHALL NOT fabricate evaluation or feature data

#### Scenario: Analyze a terminal position
- **WHEN** `analyze_position` receives a valid position with no legal moves
- **THEN** it SHALL return the blended `baseline_hce` and an empty candidate list without fabricating a move

### Requirement: Resolve a pre-move FEN from a PGN game
`fen_from_pgn` SHALL accept exactly one legal PGN game, a positive move number, and a color. It SHALL return the board position immediately before the selected color's move at that move number. It SHALL report malformed, illegal, multiple-game, or out-of-range selections as clear errors.

#### Scenario: Return White's pre-move position
- **WHEN** the tool receives move number N and White
- **THEN** it SHALL return the position after Black's move N-1 and immediately before White's move N

#### Scenario: Return Black's pre-move position
- **WHEN** the tool receives move number N and Black
- **THEN** it SHALL return the position after White's move N and immediately before Black's move N

#### Scenario: Reject a move selection absent from the game
- **WHEN** the requested color and move number do not identify a move in the PGN
- **THEN** the tool SHALL return a clear out-of-range error

### Requirement: Compute key-move evidence and identify commentary-worthy plies from a PGN
`identify_key_moves` SHALL accept exactly one legal PGN, an optional positive integer `depth` defaulting to `12`, and an optional boolean `include_hce` defaulting to `true`. It SHALL compute all NNUE and HCE evidence internally and SHALL NOT require caller-supplied per-ply evaluations. For every main-line ply it SHALL determine, in White-relative centipawns or signed mate scores, the best move with its evaluation, the second-best evaluation when the position before the move has at least two legal moves, and the played move's evaluation. It SHALL evaluate NNUE for every ply at the requested `depth`. When `include_hce` is `true`, it SHALL compute White-relative blended HCE maps for the pre-move baseline, the played-move result, and the best-move result of each selected ply; when `include_hce` is `false`, it SHALL perform no HCE evaluation and SHALL omit HCE comparison fields. The tool SHALL select and return an ordered JSON array of commentary-worthy plies.

#### Scenario: Select moments from a PGN by computing evidence internally
- **WHEN** the tool receives one legal PGN
- **THEN** it SHALL run NNUE and, when enabled, HCE itself and return a JSON array containing only qualifying selected plies

#### Scenario: Use the default search depth
- **WHEN** the tool is called without a `depth`
- **THEN** every NNUE analysis SHALL use depth `12`

#### Scenario: Use a caller-supplied search depth
- **WHEN** the tool receives a positive integer `depth`
- **THEN** every NNUE analysis SHALL use that depth

#### Scenario: Skip HCE when disabled
- **WHEN** the tool receives `include_hce` as `false`
- **THEN** it SHALL perform no HCE evaluation and SHALL NOT return HCE comparison fields

#### Scenario: Handle a position with a single legal move
- **WHEN** a ply's pre-move position has exactly one legal move
- **THEN** the second-best evaluation SHALL be unavailable and Sharpness SHALL not qualify for that ply

#### Scenario: Handle a game-ending move
- **WHEN** the played move ends the game
- **THEN** the played-move evaluation SHALL reflect the terminal result and SHALL NOT be fabricated or omitted

#### Scenario: Reject invalid controls
- **WHEN** `depth` is not a positive integer or `include_hce` is not a boolean
- **THEN** the tool SHALL return a clear input error and SHALL NOT run an analysis

#### Scenario: Reject invalid PGN
- **WHEN** the PGN is empty, malformed, illegal, or contains more than one game
- **THEN** the tool SHALL report a clear input error and SHALL NOT return selected moments

### Requirement: Calculate win-probability key-moment metrics and classifications
The tool SHALL convert centipawns `cp` to a bounded win probability using `W(e) = 1 / (1 + 10^(-e / 4.0))`, where `e = cp / 100`. A mate score for White SHALL map to `1.0` and a mate score for Black SHALL map to `0.0`. For each ply, `Delta_W` SHALL be the mover-perspective win probability of the best move minus that of the played move; `Sharpness` SHALL be the mover-perspective win probability of the best move minus that of the second-best move; and `Swing` SHALL be the absolute difference between consecutive played-move win probabilities from White's perspective. When second-best evidence or a preceding ply is unavailable, the corresponding metric SHALL be unavailable and its trigger SHALL not qualify.

The tool SHALL identify `major_blunder_turning_point` when `Delta_W > 0.25` with priority `Delta_W * 2.0`; `brilliant_only_move` when `Delta_W < 0.02` and `Sharpness > 0.30` with priority `Sharpness * 1.5`; `missed_opportunity` when `Delta_W > 0.12` with priority `Delta_W`; and `evaluation_swing` when `Swing > 0.20` with priority `Swing * 1.2`. A ply meeting multiple triggers SHALL have one primary `type`, chosen by highest calculated priority, with deterministic tie-breaking in the order listed above. It SHALL also carry the `positional_shift` tag when the post-move material is equal and `Delta_W > 0.15`.

#### Scenario: Convert centipawns and mate scores to bounded probabilities
- **WHEN** an evaluation is a centipawn value or a signed mate score
- **THEN** the tool SHALL apply the specified logistic mapping or mate mapping and every resulting probability SHALL be in `[0.0, 1.0]`

#### Scenario: Calculate Black's move loss from Black's perspective
- **WHEN** Black's best-move evaluation is better for Black than the played-move evaluation
- **THEN** the tool SHALL report a positive `Delta_W` and calculate `Sharpness` from Black's perspective as well

#### Scenario: Calculate swings using consistent White perspective
- **WHEN** consecutive plies have played-move evaluations with opposite movers
- **THEN** the tool SHALL convert both evaluations to White win probabilities before calculating their absolute difference

#### Scenario: Apply strict classification thresholds
- **WHEN** a metric is exactly equal to a classification threshold
- **THEN** that strict `>` or `<` trigger SHALL not qualify at equality

#### Scenario: Tag a material-equal positional shift
- **WHEN** the position after a played move has equal material for both sides and `Delta_W > 0.15`
- **THEN** the selected ply SHALL include the `positional_shift` tag

### Requirement: Pace and phase-balance the selected key moments
The selector SHALL rank qualifying plies by descending primary priority and return no more than a target budget scaled to game length, corresponding to 8–15 moments per 40 full moves. It SHALL return fewer moments when fewer plies qualify and SHALL NOT invent a moment to meet the target. It SHALL classify phases using material phase: opening at phase `>= 20`, middlegame at phase `8–19`, and endgame below `8`, measured after the played move. Selection SHALL reserve the highest-priority qualifying `positional_shift` moment in each phase where one exists, then fill remaining slots by priority; when the budget cannot cover all eligible phases, it SHALL prefer the highest-priority phase representatives.

#### Scenario: Apply a game-length-scaled selection budget
- **WHEN** a game has qualifying moments
- **THEN** the selector SHALL scale its lower and upper targets proportionally to the number of played plies from the 8–15-per-40-full-moves range and SHALL return at most the scaled upper target

#### Scenario: Return fewer moments than the target when evidence is sparse
- **WHEN** fewer plies qualify than the scaled target
- **THEN** the selector SHALL return only the qualifying plies

#### Scenario: Balance positional moments across available phases
- **WHEN** qualifying positional-shift candidates exist in multiple phases and the selection budget permits
- **THEN** the selector SHALL include the highest-priority such candidate from each available phase before filling remaining slots

#### Scenario: Keep selected output chronologically ordered
- **WHEN** selection and phase balancing are complete
- **THEN** the returned JSON array SHALL be ordered by ascending ply number

### Requirement: Return selected move, NNUE, and HCE evidence
Each selected object SHALL include `ply`, the played move in SAN as `move`, the engine's best move as `analysis_move` in UCI, primary `type`, numeric `priority`, and numeric `delta_w`. It SHALL include available `sharpness`, `swing`, best and played NNUE evaluations, and the mover-perspective centipawn change from played move to best move. When `include_hce` is `true`, `hce_deltas` SHALL contain played-result-minus-baseline feature changes and `missed_hce_deltas` SHALL contain played-result-minus-best-result feature changes, when their respective comparisons are available. When `include_hce` is `false`, both HCE delta properties SHALL be omitted. Unavailable comparison evidence SHALL be omitted rather than replaced with fabricated values.

#### Scenario: Return requested key-moment details
- **WHEN** a ply is selected
- **THEN** its object SHALL include the required identity, classification, priority, and Delta-W properties plus each available optional metric and NNUE comparison

#### Scenario: Include both HCE comparisons when HCE is enabled
- **WHEN** `include_hce` is `true` and baseline, played-result, and best-result HCE feature maps are available for a selected ply
- **THEN** its object SHALL include both `hce_deltas` and `missed_hce_deltas`, with each value computed as the named minuend minus subtrahend

#### Scenario: Omit HCE evidence when disabled
- **WHEN** `include_hce` is `false`
- **THEN** the selected object SHALL omit `hce_deltas` and `missed_hce_deltas` and SHALL NOT represent them as zero

#### Scenario: Omit unavailable HCE evidence
- **WHEN** an HCE comparison is unavailable while HCE is enabled
- **THEN** the corresponding HCE delta property SHALL be omitted and SHALL NOT be inferred from NNUE scores

### Requirement: Analyze a whole game into a per-ply move-evidence array
`analyze_game` SHALL accept exactly one legal PGN game and an optional positive integer `depth` defaulting to `12`, and return a JSON array containing one object per main-line ply, in chronological order. It SHALL compute all NNUE and HCE evidence internally at the requested search depth and SHALL NOT require caller-supplied per-ply input. It SHALL always include HCE evidence. For each ply it SHALL compare the played move with the engine's best move for that ply's pre-move position.

Each object SHALL include the integer `ply` and the played move in SAN as `move`. It SHALL include the engine's best move in UCI as `best` only when the played move differs from the engine's best move. It SHALL include a numeric mover-relative centipawn loss as `cpl` only when that loss is non-zero. It SHALL include an `hce` object of played-result-minus-baseline blended feature shifts that always contains `total`, and a `missed` object of played-result-minus-best blended feature shifts only when that comparison is non-empty. Feature selection and pruning in `hce` and `missed` SHALL follow the HCE delta rules defined for `analyze_position` and `analyze_move`. Mate evaluations SHALL be folded into centipawn values using the existing signed convention. The tool SHALL NOT return a principal variation, game metadata, or selection fields, and SHALL NOT reuse the previous object-shaped `analyze_game` response. Empty, malformed, illegal, or multiple-game PGN input, or a non-positive-integer `depth`, SHALL raise a clear error and SHALL NOT return partial evidence.

#### Scenario: Return a chronological per-ply array
- **WHEN** `analyze_game` receives one legal PGN
- **THEN** it SHALL return a JSON array with one object per main-line ply, ordered by ascending ply number

#### Scenario: Use the fixed search depth
- **WHEN** the tool analyzes any ply and no `depth` is supplied
- **THEN** every NNUE search SHALL use depth `12`

#### Scenario: Use a caller-supplied search depth
- **WHEN** the tool receives a positive integer `depth`
- **THEN** every NNUE search SHALL use that depth

#### Scenario: Reject an invalid search depth
- **WHEN** `depth` is not a positive integer
- **THEN** the tool SHALL return a clear input error and SHALL NOT run an analysis

#### Scenario:

#### Scenario: Omit a zero centipawn loss
- **WHEN** a ply's played move loses no centipawns relative to the engine's best move
- **THEN** that object SHALL omit `cpl`

#### Scenario: Include HCE deltas with total
- **WHEN** a ply is analyzed
- **THEN** its object SHALL include an `hce` object of played-result-minus-baseline shifts whose `total` entry is always present

#### Scenario: Include missed HCE deltas only when a better move existed
- **WHEN** a ply's played move differs from the engine's best move
- **THEN** its object SHALL include a `missed` object of played-result-minus-best shifts, and the object SHALL omit `missed` when the played move is the engine's best move

#### Scenario: Calculate loss from the mover's perspective
- **WHEN** a Black ply's best move is better for Black than the played move
- **THEN** `cpl` SHALL be a positive centipawn loss from Black's perspective

#### Scenario: Score a game-ending move
- **WHEN** the played move ends the game
- **THEN** the ply's comparison SHALL reflect the terminal result and SHALL NOT be fabricated or omitted

#### Scenario: Reject invalid PGN
- **WHEN** the PGN is empty, malformed, illegal, or contains more than one game
- **THEN** the tool SHALL report a clear input error and SHALL NOT return partial evidence
