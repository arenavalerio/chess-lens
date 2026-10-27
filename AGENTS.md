# AGENTS.md

Stockfish-based chess analysis: a FastMCP server (`chess-lens-mcp/`) plus agent skills (`skills/`). There is no API or frontend anymore; `frontend/` references in `.gitignore` and `.github/workflows/frontend.yml` are stale.

## Commands (run from `chess-lens-mcp/`)

All tool config lives in `chess-lens-mcp/pyproject.toml`, so every command runs from that directory. `chess-lens-mcp` is an installable package (`src/chess_lens_mcp/`, built with hatchling); `uv sync` installs it editable, and the MCP server is the `python -m chess_lens_mcp` module / `chess-lens-mcp` console script. `pip install -e` works too.

```bash
uv sync --all-extras           # install test + dev groups
uv run ruff check .            # lint
uv run ruff format --check .   # formatting
uv run mypy src tests          # type check (strict on src, relaxed in tests)
uv run pyscn check .           # complexity/dead-code/clones/cycles
uv run pytest --cov=src        # tests; coverage must stay >= 80
```

CI (`.github/workflows/python.yml`) runs ruff check → ruff format → mypy → pyscn → pytest in that order, using `uv sync --locked`. `uv.lock` is committed: re-run `uv lock` after changing dependencies or CI fails on the stale lockfile.

Single test: `uv run pytest tests/test_mcp_server.py::TestMcpIntegration -v`.

## Markdown linting

Top-level docs (`README.md`, `AGENTS.md`, `docs/`) are linted with markdownlint-cli2 from the repository root; `.github/workflows/markdown.yml` runs the same check.

```bash
npx markdownlint-cli2
```

Rules live in `.markdownlint-cli2.jsonc`: the default set minus `MD013` (line length) and `MD040` (fenced-code language), which the existing docs do not follow. Scope is intentionally limited to the top-level docs, so `openspec/**` and `skills/**` are not linted.

## Stockfish dependency

- The engine path defaults to `/usr/games/stockfish` (`chess_lens_mcp/engine.py`); install the pinned Stockfish 16.0 x86-64 Linux release with `python3 install_stockfish.py` from `chess-lens-mcp/`. Missing binary means runtime failures.
- HCE feature evidence runs Stockfish with `Use NNUE false` (classical `eval` table); move ranking runs `Use NNUE true`. Keep the two paths separate.
- Real-engine tests use `@needs_stockfish` and skip when the binary is absent. There is no registered `integration` marker despite the module docstring's `pytest -m "not integration"` advice.
- Unit tests mock `chess.engine.popen_uci` and `StockfishEngine.run_hce_batch`; follow that pattern instead of invoking the engine.

## MCP contract (source of truth: `openspec/specs/`, docs: `README.md`, `docs/tools.md`)

- Exposes exactly five tools: `analyze_position`, `analyze_move`, `analyze_game`, `identify_key_moves`, `fen_from_pgn`. Do not rename or add aliases.
- HCE is White-relative and tapered by non-pawn material phase in `[0, 24]`; zero/null features are omitted; `hce`/`hce_deltas`/`missed` always keep `total` and prune other features at `|shift| <= 0.05`.
- `analyze_game` returns a per-ply array, not the old single object with `metadata`/`strategic_snapshots`/`key_moments`.

## OpenSpec workflow

Non-trivial behavior changes go through OpenSpec: specs in `openspec/specs/`, changes in `openspec/changes/` (currently only `archive/`). The CLI is installed (`openspec --version`); the `opsx-*` skills under `.opencode/skills/` and `.agents/skills/` drive propose/apply/archive. Update the matching spec when behavior changes.

## Skills

- `skills/comment-game/SKILL.md`
- `skills/evaluate-position/SKILL.md`

## Docker

- `docker build -t chess-lens-mcp chess-lens-mcp` builds the runtime target; add `--target test` to run the full quality gate in-image.
- `opencode.json` connects via `docker run -i --rm chess-lens-mcp`; rebuild the image after source changes or the MCP tools serve stale code.

## Stale file

`.opencode/agents/chess-hce-analyzer.md` describes a removed `chess_hce_agent` / `mcp-stockfish-server` setup and does not match this repo.
