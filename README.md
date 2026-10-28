# Chess Lens MCP

[![Python CI](https://github.com/arenavalerio/sandbox/actions/workflows/python.yml/badge.svg?branch=main)](https://github.com/arenavalerio/sandbox/actions/workflows/python.yml)
[![Docker Build](https://github.com/arenavalerio/sandbox/actions/workflows/docker.yml/badge.svg?branch=main)](https://github.com/arenavalerio/sandbox/actions/workflows/docker.yml)
[![Markdown Lint](https://github.com/arenavalerio/sandbox/actions/workflows/markdown.yml/badge.svg?branch=main)](https://github.com/arenavalerio/sandbox/actions/workflows/markdown.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Stockfish-backed [MCP](https://modelcontextprotocol.io/) server with a single aim: give LLMs chess evaluations that are **strong** and, at the same time, **understandable by humans**. The engine supplies the verdict; classical handcrafted-evaluation (HCE) terms give an LLM the vocabulary to explain *why*.

## Goals

- **Analyzing positions and plans.** Engine-backed evaluations for LLMs: validate a position, rank candidate moves, score one move, walk a whole game ply by ply, select the commentary-worthy moments, resolve a pre-move FEN from a PGN. Strength comes from Stockfish NNUE search scores, and responses stay JSON-only — no prose, no invented evaluations.
- **Understanding the reasons for good and bad moves.** HCE evaluations: alongside the engine score, each move carries classical HCE feature deltas (space, mobility, king safety, material, threats), computed by the same engine with `Use NNUE false`.
- **Identify key moments.** Walk a game and surface the moves worth commenting on — blunders that turn the game, brilliant only-moves, missed opportunities, and evaluation swings.

## Features

- **Five task-oriented MCP tools** — `analyze_position`, `analyze_move`, `analyze_game`, `identify_key_moves`, and `fen_from_pgn`.
- **Two evidence layers per move** — NNUE search scores for strength and classical HCE feature deltas for *why* a move helps or hurts (space, mobility, king safety, material, threats).
- **PGN in, evidence out** — feed one legal game and get a per-ply timeline or a ranked set of turning points.
- **FEN or PGN selectors** — analyze a raw position, or address a specific move by `move_number` and `color`.
- **Two agent skills** — `comment-game` produces a commented PGN; `evaluate-position` returns a grounded evaluation with plans and candidate moves.

## Quick Start

```bash
git clone https://github.com/arenavalerio/sandbox.git
cd sandbox/chess-lens-mcp
docker build -t chess-lens-mcp .
```

Point an MCP client at the image (example: `opencode.json`):

```json
{
  "mcp": {
    "chess-lens-mcp": {
      "type": "local",
      "command": ["docker", "run", "-i", "--rm", "chess-lens-mcp"]
    }
  }
}
```

Then call a tool with a position:

```json
{
  "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
  "candidate_count": 2
}
```

See the [MCP Tools](#mcp-tools) table for the full list and [`docs/tools.md`](docs/tools.md) for inputs, outputs, and JSON samples.

## Requirements

- **Python 3.11 or newer.**
- **Stockfish 16.0 on x86-64 Linux.** CI and Docker use the official `sf_16` release asset and the shared installer, which verifies its SHA-256 before installation. For local use, run the same installer from `chess-lens-mcp/`; it installs the executable under `/opt/stockfish-16.0` and exposes it at `/usr/games/stockfish`. Move ranking runs Stockfish with NNUE enabled; HCE feature evidence runs the same engine with `Use NNUE false` (the classical `eval` table). Both paths need the binary.
- **Docker** — optional, only for the container workflow.

## Installation

### Docker (recommended)

```bash
# Production image: runs the server over stdio.
docker build -t chess-lens-mcp chess-lens-mcp

# Test image: runs lint, format, type checks, and the test suite during the build.
docker build --target test -t chess-lens-mcp:test chess-lens-mcp
```

The runtime image installs the pinned Stockfish 16.0 release and the `chess-lens-mcp` package into `/app/.venv`, then starts the `chess-lens-mcp` console script. No `PYTHONPATH` or source path is needed.

### Local

Run from the `chess-lens-mcp` directory:

```bash
cd chess-lens-mcp
sudo python3 install_stockfish.py
uv sync --all-extras           # install every dependency group
uv run python -m chess_lens_mcp   # or: uv run chess-lens-mcp
```

`pip install -e ".[test,dev]"` works too.

## Configuration

The server is configured through its callers rather than environment variables:

- **Engine path** — the Stockfish binary path defaults to `/usr/games/stockfish` (the `StockfishEngine` default in `src/chess_lens_mcp/engine.py`).
- **Search depth** — selectable per call for `analyze_game` and `identify_key_moves` via `depth` (default `12`).
- **MCP connection** — register the server in your client;

## MCP Tools

The server exposes exactly five tools. Each takes a single JSON object and returns a JSON object (or array).

| Tool | Purpose | Key input |
| --- | --- | --- |
| `analyze_position` | Baseline HCE plus ranked legal candidate moves | FEN (or PGN + `move_number` + `color`); `candidate_count` |
| `analyze_move` | Score one specific legal move from a position | FEN + `move_uci` (or PGN selector) |
| `analyze_game` | Chronological per-ply timeline for a whole game | PGN; `depth` |
| `identify_key_moves` | Select the commentary-worthy turning points | PGN; `depth`, `include_hce` |
| `fen_from_pgn` | Resolve the position immediately before a given move | PGN; `move_number`, `color` |

Full contract, including JSON samples and error rules: [`docs/tools.md`](docs/tools.md).

## Skills

- **`skills/comment-game/`** — turns one legal game into a commented PGN. It calls `analyze_game` once for the per-ply skeleton and `identify_key_moves` once for turning-point classification, then uses `fen_from_pgn` with `analyze_position`/`analyze_move` only for plies that need a continuation. It writes exactly one short comment (at most 35 words) after every main-line ply, applies standard quality glyphs from centipawn-loss bands, and returns the commented PGN in chat (saving to a file only when asked).
- **`skills/evaluate-position/`** — evaluates one validated FEN and returns `Evaluation`, `White Plans`, `Black Plans`, and `Next Move`, all grounded in returned HCE and NNUE evidence.

## License

Released under the [MIT License](LICENSE).

## Acknowledgements

Built on [Stockfish](https://stockfishchess.org/), [python-chess](https://python-chess.readthedocs.io/), and [FastMCP](https://gofastmcp.com/). Developed with [uv](https://docs.astral.sh/uv/), [ruff](https://docs.astral.sh/ruff/), [mypy](https://mypy.readthedocs.io/), [pytest](https://docs.pytest.org/), and [pyscn](https://github.com/ludo-technologies/pyscn).
