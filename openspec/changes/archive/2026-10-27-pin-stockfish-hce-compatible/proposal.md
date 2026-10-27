# Proposal

## Why

CI and Docker currently install Stockfish from the moving Debian package repository. The installed engine does not expose the `Use NNUE` UCI option required by analysis, and deployments need a reproducible engine version that supports both NNUE search and the classical `eval` output used for HCE evidence.

## What Changes

- Replace the floating apt-based Stockfish installation in CI and Docker with the newest official release verified to support the project's NNUE and HCE workflows.
- Pin the release and verify the downloaded artifact's integrity so CI and Docker use the same engine build.
- Document the compatible engine requirement and validate both analysis modes against the installed binary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `chess-analysis-tooling`: Specify the compatible Stockfish runtime requirement needed to produce NNUE analysis and classical HCE evidence reproducibly.

## Impact

- `.github/workflows/python.yml` and `chess-lens-mcp/Dockerfile` engine installation.
- The chess analysis tooling runtime requirement and user-facing installation documentation.
- Real-Stockfish tests for NNUE analysis and HCE evaluation.
