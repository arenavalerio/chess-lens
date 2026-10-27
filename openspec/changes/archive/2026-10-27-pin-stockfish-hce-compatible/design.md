# Design

## Context

The Python package shells out to a Stockfish UCI executable at `/usr/games/stockfish`. Its move analysis configures `Use NNUE`, while HCE evidence sends the `eval` command after disabling NNUE. Both CI and Docker currently install the engine from Debian's unpinned package repository.

## Goals / Non-Goals

**Goals:**
- Keep the engine version and artifact integrity check consistent between CI and Docker.
- Select the newest official release that passes checks for both NNUE analysis and the classical evaluation output.
- Preserve the existing executable path and analysis behavior.

**Non-Goals:**
- Change how NNUE search or HCE values are calculated.
- Add support for every operating system or CPU architecture.
- Change the Python package dependencies to include a Stockfish wrapper.

## Decisions

- **Use an official, versioned Stockfish release artifact instead of the Debian package.** The current package repository can change the engine independently of the project, and the reported CI failure demonstrates that a newer package may no longer expose the UCI options this code uses. Choose the most recent official release that passes the compatibility checks; do not use a moving `latest` download URL.
- **Pin the verified release asset.** Stockfish 16.0 (`sf_16`), asset `stockfish-ubuntu-x86-64.tar`, has SHA-256 `efca1c60ec11fd9628425f3ee40644ad1618535ddf881c16385a86f7fc9e0983`. Its UCI options include `Use NNUE`; depth-1 NNUE search succeeds, and `Use NNUE false` produces the classical MG/EG table parsed by `parse_eval_output`.
- **Keep the version and integrity data in one shared installation source.** Add a small installer and pinned release metadata under `chess-lens-mcp/`, then have both the workflow and Docker build invoke it. This avoids separate version/checksum values drifting. The installer will verify the downloaded archive against a fixed SHA-256 digest before extracting it.
- **Preserve the expected executable location and bundled runtime files.** Install the release in a stable directory, retain any network files required by that release, and expose its executable at `/usr/games/stockfish` so application defaults need not change.
- **Require behavioral compatibility checks before pinning a candidate.** Exercise the candidate through the same UCI option configuration, NNUE search, and classical `eval` output consumed by the application. Retain the real-engine integration coverage in CI so future edits cannot silently break either path.

## Risks / Trade-offs

- [Official release assets or their layout vary between releases] -> Verify the selected archive contents during implementation and make the installer validate required files and executable startup.
- [A release asset changes or cannot be downloaded in CI] -> Pin a release URL and SHA-256 digest; a changed artifact fails closed rather than installing an unexpected engine.
- [A pinned engine receives no automatic updates] -> Treat engine upgrades as an explicit compatibility change: select the newest compatible official release, update its pinned integrity data, and rerun both real-engine paths.
- [The chosen binary may require CPU features unavailable on some Docker hosts] -> Select an asset compatible with the supported CI and Docker target and state any resulting platform constraint in the installation documentation.

## Migration Plan

1. Identify and verify the newest official release compatible with NNUE and classical HCE evaluation; record the exact artifact and SHA-256 digest.
2. Add the shared pinned installer and use it in CI and Docker, removing only the Stockfish package install.
3. Run targeted real-engine checks and the repository quality gate, then update installation documentation.
4. If deployment fails, revert the installer integration to the existing package installation while preserving the previous version of the workflow and Docker image.
