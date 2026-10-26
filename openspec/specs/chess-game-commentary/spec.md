# chess-game-commentary Specification

## Purpose
Produces a portable commented PGN from one legal chess game, placing short, engine-grounded, plain-language comments after the selected, commentary-worthy main-line plies so club players and amateurs can follow the analysis inside ordinary chess software without reading a comment after every move.

## Requirements

### Requirement: Produce a commented PGN from one legal game
The skill SHALL accept one legal game as PGN text or as a path to a PGN file and SHALL return a commented PGN. The returned PGN SHALL preserve the game's headers, main-line moves, result, and side variations, and SHALL discard every comment present in the input. Generated commentary SHALL be added to main-line moves only. The skill SHALL report empty, malformed, illegal, or multiple-game input as a clear error and SHALL NOT return a partial or fabricated PGN.

#### Scenario: Comment PGN text
- **WHEN** the user supplies valid PGN text containing exactly one legal game
- **THEN** the skill SHALL return that game as a commented PGN

#### Scenario: Comment a PGN file
- **WHEN** the user supplies a path to a PGN file containing exactly one legal game
- **THEN** the skill SHALL read the file and return that game as a commented PGN

#### Scenario: Preserve structure while discarding input comments
- **WHEN** the input game has headers, a result, side variations, and existing comments
- **THEN** the returned PGN SHALL retain the headers, result, and side variations and SHALL contain none of the input comments

#### Scenario: Reject invalid input
- **WHEN** the input is empty, malformed, illegal, or contains more than one game
- **THEN** the skill SHALL identify the input problem and SHALL NOT return a commented PGN

### Requirement: Comment every main-line ply
The returned PGN SHALL contain exactly one generated comment after every main-line ply, for both colors. Each comment SHALL be short, at most 35 whitespace-separated words, and SHALL use sentence boundaries when shortened. Moves inside side variations SHALL NOT receive generated comments.

#### Scenario: One comment per main-line move
- **WHEN** a valid game is annotated
- **THEN** every White and Black main-line move SHALL have exactly one generated comment

#### Scenario: Leave variation moves uncommented
- **WHEN** the input game contains side variations
- **THEN** the returned PGN SHALL add no generated comment to any move inside a variation

#### Scenario: Bound comment length
- **WHEN** a comment would exceed the word cap
- **THEN** it SHALL be shortened at a sentence boundary and SHALL NOT be cut inside a word

### Requirement: Ground commentary in MCP evidence
The skill SHALL derive commentary only from evidence returned by the available MCP tools: `analyze_game` for the per-ply played and best moves with centipawn loss and HCE deltas, `identify_key_moves` for turning-point classification and brilliant-move support, and `fen_from_pgn` with `analyze_position` or `analyze_move` when a specific ply needs a continuation or a clearer better move. Each comment SHALL explain the played move using the returned centipawn loss and HCE feature deltas and SHALL name the engine's better move when the played move differs. The skill SHALL NOT fabricate an evaluation, feature value, better move, or continuation, and SHALL surface unavailable evidence instead of inventing it.

#### Scenario: Explain the engine's choice
- **WHEN** a ply's played move matches the engine's best move
- **THEN** the comment SHALL describe what the move achieves using its returned HCE feature deltas

#### Scenario: Explain a mistake and its better move
- **WHEN** a ply's played move differs from the engine's best move
- **THEN** the comment SHALL describe what is wrong with the played move and SHALL name the engine's better move using the returned comparison evidence

#### Scenario: Use key-move classification for a brilliant glyph
- **WHEN** `identify_key_moves` classifies a ply as an evidence-backed brilliant or only move
- **THEN** the comment SHALL reflect that classification

#### Scenario: Cross-reference a position for a continuation
- **WHEN** a ply's comment needs a specific continuation or a clearer better move
- **THEN** the skill SHALL obtain the pre-move position with `fen_from_pgn` and evaluate it with `analyze_position` or `analyze_move`

#### Scenario: Surface missing evidence
- **WHEN** required engine evidence is unavailable for a ply
- **THEN** the comment SHALL identify the missing evidence and SHALL NOT invent a score, feature, or move

### Requirement: Apply standard move-quality glyphs on the main line
The skill SHALL apply standard PGN move-quality glyphs to main-line moves from centipawn loss: `?!` for 50 to 99, `?` for 100 to 199, and `??` for 200 or more. It SHALL omit a glyph for lower loss. It SHALL reserve `!` for an evidence-backed brilliant or only move and `!!` for an exceptional such move, and SHALL NOT assign `!` or `!!` from a best-move match alone. When centipawn loss is unavailable, it SHALL NOT infer a glyph. It SHALL replace existing main-line quality glyphs and SHALL leave variation glyphs unchanged.

#### Scenario: Mark move quality from centipawn loss
- **WHEN** a main-line move has a returned centipawn loss in a glyph band
- **THEN** the returned PGN SHALL carry the corresponding glyph on that move

#### Scenario: Leave routine moves unmarked
- **WHEN** a main-line move's centipawn loss is below 50
- **THEN** the move SHALL carry no quality glyph unless separate evidence supports a reserved glyph

#### Scenario: Reserve exceptional glyphs
- **WHEN** a move matches the engine's best move but no further evidence supports it
- **THEN** the skill SHALL NOT assign `!` or `!!`

#### Scenario: Avoid a glyph without a loss
- **WHEN** centipawn loss is unavailable for a main-line move
- **THEN** the skill SHALL NOT assign a glyph inferred from a missing value

#### Scenario: Clear stale main-line glyphs only
- **WHEN** a main-line move already carries a quality glyph
- **THEN** the returned PGN SHALL replace it with the computed glyph or remove it, while variation glyphs remain unchanged

### Requirement: Use accessible language and correct perspective
Comments SHALL be written in plain language that translates returned HCE feature deltas into chess concepts such as space, piece activity, king safety, or material, and SHALL NOT narrate engine preference or dump raw feature vectors. When a comment's point rests on an NNUE score or an HCE feature shift, it SHALL close with exactly one brief source-tagged score note, written in pawns and White-relative, such as `(NNUE -2.1)`, `(HCE total -0.4)`, or `(HCE king safety -1.1)`. Because HCE feature values are White-relative, the skill SHALL reverse their reading when describing a Black move, while centipawn loss SHALL be interpreted from the mover's perspective. Each comment SHALL distinguish positional reasoning drawn from HCE deltas from evaluation magnitude drawn from NNUE scores.

#### Scenario: Translate feature deltas into concepts
- **WHEN** a ply's evidence includes HCE feature deltas
- **THEN** the comment SHALL express them as chess concepts rather than a raw feature vector, closing with one source-tagged score note

#### Scenario: Cite the score behind the claim
- **WHEN** a comment's point rests on an NNUE score or an HCE feature shift
- **THEN** the comment SHALL end with one source-tagged score note in pawns, White-relative

#### Scenario: Reverse White-relative evidence for Black
- **WHEN** the commented move is Black's
- **THEN** the skill SHALL describe White-relative HCE deltas from Black's point of view

#### Scenario: Keep loss mover-relative
- **WHEN** the commented move is Black's and the engine's better move is better for Black
- **THEN** the described centipawn loss SHALL be positive from Black's perspective

### Requirement: Comment terminal and game-ending moves
When a ply ends the game, the comment SHALL state the terminal outcome, including checkmate, stalemate, or another draw, and SHALL NOT fabricate a continuation.

#### Scenario: Comment a checkmating move
- **WHEN** a main-line move delivers checkmate
- **THEN** the comment SHALL state that the move checkmates and SHALL NOT invent a continuation

#### Scenario: Comment a drawn ending
- **WHEN** a main-line move produces stalemate or another drawn result
- **THEN** the comment SHALL state the drawn outcome

### Requirement: Return the commented PGN in chat
The skill SHALL return the complete commented PGN in the response in a form the user can copy, and SHALL save it to a file only when the user asks. When saving, it SHALL NOT overwrite an existing file.

#### Scenario: Return in chat by default
- **WHEN** the user requests commentary without asking to save a file
- **THEN** the skill SHALL return the commented PGN in the response and SHALL NOT create a file

#### Scenario: Save only on request
- **WHEN** the user asks to save the commented PGN
- **THEN** the skill SHALL save it and SHALL NOT overwrite an existing file
