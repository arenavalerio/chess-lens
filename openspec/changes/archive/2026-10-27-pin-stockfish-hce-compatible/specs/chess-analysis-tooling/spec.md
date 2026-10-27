# Spec Delta

## ADDED Requirements

### Requirement: Provide a Stockfish release compatible with NNUE and HCE
The CI and Docker environments SHALL use the same pinned official Stockfish release, selected as the newest release that supports both NNUE analysis and the classical `eval` output required for HCE evidence. The installed binary SHALL be integrity-verified.

#### Scenario: Install the pinned compatible engine
- **WHEN** CI or the Docker image installs Stockfish
- **THEN** it SHALL install the same fixed official release and verify the downloaded artifact against a fixed SHA-256 digest

#### Scenario: Preserve both analysis modes
- **WHEN** the installed engine is exercised by the real-engine checks
- **THEN** it SHALL support NNUE search and produce the classical evaluation output parsed for HCE evidence
