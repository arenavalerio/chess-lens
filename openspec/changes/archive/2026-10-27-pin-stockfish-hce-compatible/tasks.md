# Tasks

## 1. Select and install the compatible release

- [x] 1.1 Pin **Stockfish 16.0**, official release tag `sf_16` (not 16.1 or newer): it is the newest official release confirmed to retain both the `Use NNUE` UCI option and the classical `eval` term table consumed by `parse_eval_output`. Use the official `stockfish-ubuntu-x86-64.tar` release asset for the current x86-64 CI/Docker target; verify the claims by inspecting UCI options and confirming `eval` output contains White/Black/Total MG/EG rows, then record the asset SHA-256.
- [x] 1.2 Implement the shared installer under `chess-lens-mcp/` to download only the pinned Stockfish 16.0 asset, verify its digest before extraction, preserve required runtime files, and expose the executable at `/usr/games/stockfish`; verify it starts, accepts `Use NNUE true`, and with `Use NNUE false` produces `eval` output parseable into the expected HCE features.

## 2. Integrate the installer in CI and Docker

- [x] 2.1 Replace the Debian Stockfish installation in `.github/workflows/python.yml` and `chess-lens-mcp/Dockerfile` with the shared installer; verify both environments install the same pinned release and the Docker test target builds.
- [x] 2.2 Update the README installation requirements to describe the pinned compatible-engine approach and its supported platform; verify the documented steps match the installer and runtime path.

## 3. Verify end-to-end analysis

- [x] 3.1 Ensure real-engine integration coverage exercises NNUE analysis and HCE evaluation with the pinned binary; run the focused integration tests and the Python CI quality commands successfully.
