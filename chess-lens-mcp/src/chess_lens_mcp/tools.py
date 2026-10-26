"""FastMCP tool registrations for chess analysis."""

from typing import Annotated, Any

import chess
import chess.engine
from fastmcp import FastMCP
from pydantic import Field

from .engine import sf
from .evaluation import blend_and_prune_hce, compute_hce_deltas, parse_eval_output
from .key_moves import (
    _attach_analyze_game_hce,
    _attach_key_move_hce,
    _build_key_move_candidate,
    _format_analyze_game_element,
    _score_search_info,
    _select_key_move_candidates,
    build_key_move_evidence,
)
from .parsing import _validated_board, parse_pgn_game

mcp = FastMCP("chess-lens-mcp")


def _validate_analysis_input_form(
    fen: str | None,
    pgn: str | None,
    move_number: int | None,
    color: str | None,
    move_uci: str | None = None,
) -> bool:
    """Validate mutually exclusive FEN and PGN inputs; return whether PGN was given."""
    if fen is not None:
        if pgn is not None or move_number is not None or color is not None:
            raise ValueError("FEN and PGN inputs cannot be combined.")
        return False

    if pgn is None:
        raise ValueError("Provide either a FEN or a PGN with move number and color.")
    if move_number is None or color is None:
        raise ValueError("PGN input requires both move_number and color.")
    if move_uci is not None:
        raise ValueError(
            "Do not provide move_uci with PGN input; the move is inferred."
        )
    return True


def _resolve_pgn_ply(
    pgn: str | None,
    move_number: int | None,
    color: str | None,
) -> dict[str, Any]:
    """Return the parsed main-line ply selected by move number and mover."""
    if (
        not isinstance(move_number, int)
        or isinstance(move_number, bool)
        or move_number < 1
    ):
        raise ValueError("Move number must be a positive integer.")
    if not isinstance(color, str) or color.strip().lower() not in ("white", "black"):
        raise ValueError("Color must be 'white' or 'black'.")
    if not isinstance(pgn, str):
        raise ValueError("PGN input must be a string.")

    selected_color = color.strip().lower()
    parsed = parse_pgn_game(pgn)
    plies: list[dict[str, Any]] = parsed["plies"]
    for ply in plies:
        if ply["move_number"] == move_number and ply["mover"] == selected_color:
            return ply
    raise ValueError(f"No {selected_color} move {move_number} exists in the PGN.")


@mcp.tool
async def analyze_position(
    fen: str | None = None,
    candidate_count: int = 1,
    pgn: str | None = None,
    move_number: int | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    """Rank legal moves with NNUE and return compact HCE features and deltas."""
    use_pgn = _validate_analysis_input_form(fen, pgn, move_number, color)
    if (
        not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or candidate_count < 1
    ):
        raise ValueError("Candidate count must be a positive integer.")

    if use_pgn:
        ply = _resolve_pgn_ply(pgn, move_number, color)
        analysis_fen = ply["before_fen"]
    else:
        if fen is None:
            raise ValueError("FEN input is required when PGN input is not used.")
        analysis_fen = fen
    board = _validated_board(analysis_fen)

    legal_move_count = board.legal_moves.count()
    infos: list[chess.engine.InfoDict] = []
    if legal_move_count:
        _, engine = await chess.engine.popen_uci(sf.path)
        try:
            await engine.configure({"Use NNUE": True})
            infos = await engine.analyse(
                board,
                chess.engine.Limit(depth=12),
                multipv=min(candidate_count, legal_move_count),
            )
        finally:
            await engine.quit()

    candidates = []
    candidate_moves = []
    hce_jobs = [f"position fen {analysis_fen}"]
    for info in infos:
        scored = _score_search_info(info, board.turn)
        if not scored["pv"]:
            continue
        move = chess.Move.from_uci(scored["pv"][0])
        if not board.is_legal(move):
            continue
        candidate = {
            "move": move.uci(),
            "score_cp": scored["centipawns"],
            "pv": scored["pv"][:6],
        }
        if scored["mate_in"] is not None:
            candidate["mate_in"] = scored["mate_in"]
        candidates.append(candidate)
        candidate_moves.append(move)
        hce_jobs.append(f"position fen {analysis_fen} moves {move.uci()}")

    hce_outputs = await sf.run_hce_batch(hce_jobs)
    if len(hce_outputs) != len(hce_jobs):
        raise RuntimeError("HCE evaluation returned an unexpected number of results.")

    baseline_raw = parse_eval_output(hce_outputs[0])
    baseline_hce = blend_and_prune_hce(baseline_raw, board)

    for candidate, move, output in zip(
        candidates, candidate_moves, hce_outputs[1:], strict=True
    ):
        post_board = board.copy()
        post_board.push(move)
        cand_raw = parse_eval_output(output)
        cand_hce = blend_and_prune_hce(cand_raw, post_board)
        candidate["hce_deltas"] = compute_hce_deltas(baseline_hce, cand_hce)

    return {
        "fen": analysis_fen,
        "score_perspective": "side to move",
        "baseline_hce": baseline_hce,
        "candidates": candidates,
    }


@mcp.tool
async def analyze_move(
    fen: str | None = None,
    move_uci: str | None = None,
    pgn: str | None = None,
    move_number: int | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    """Score one legal UCI move with NNUE and return compact HCE features."""
    use_pgn = _validate_analysis_input_form(fen, pgn, move_number, color, move_uci)
    if use_pgn:
        ply = _resolve_pgn_ply(pgn, move_number, color)
        analysis_fen = ply["before_fen"]
        move_uci = ply["uci"]
    else:
        if move_uci is None:
            raise ValueError("FEN input requires move_uci.")
        if fen is None:
            raise ValueError("FEN input is required when PGN input is not used.")
        analysis_fen = fen

    board = _validated_board(analysis_fen)
    if not isinstance(move_uci, str):
        raise ValueError("Move must be a legal UCI string.")
    try:
        move = chess.Move.from_uci(move_uci)
    except (ValueError, IndexError) as exc:
        raise ValueError("Invalid UCI move string.") from exc
    if not board.is_legal(move):
        raise ValueError("Illegal move for this position.")

    _, engine = await chess.engine.popen_uci(sf.path)
    try:
        await engine.configure({"Use NNUE": True})
        info = await engine.analyse(
            board,
            chess.engine.Limit(depth=12),
            root_moves=[move],
        )
    finally:
        await engine.quit()

    scored = _score_search_info(info, board.turn)
    hce_jobs = [
        f"position fen {analysis_fen}",
        f"position fen {analysis_fen} moves {move.uci()}",
    ]
    hce_outputs = await sf.run_hce_batch(hce_jobs)
    if len(hce_outputs) != 2:
        raise RuntimeError("HCE evaluation returned an unexpected number of results.")

    baseline_raw = parse_eval_output(hce_outputs[0])
    baseline_hce = blend_and_prune_hce(baseline_raw, board)

    post_board = board.copy()
    post_board.push(move)
    cand_raw = parse_eval_output(hce_outputs[1])
    cand_hce = blend_and_prune_hce(cand_raw, post_board)
    deltas = compute_hce_deltas(baseline_hce, cand_hce)

    result = {
        "fen": analysis_fen,
        "move": move.uci(),
        "score_perspective": "side to move before the move",
        "score_cp": scored["centipawns"],
        "pv": scored["pv"][:6],
        "baseline_hce": baseline_hce,
        "hce_deltas": deltas,
    }
    if scored["mate_in"] is not None:
        result["mate_in"] = scored["mate_in"]

    return result


@mcp.tool
async def fen_from_pgn(pgn: str, move_number: int, color: str) -> dict[str, Any]:
    """Return the position immediately before a selected PGN move."""
    ply = _resolve_pgn_ply(pgn, move_number, color)
    return {"fen": ply["before_fen"]}


@mcp.tool
async def identify_key_moves(
    pgn: str,
    depth: Annotated[int, Field(strict=True, ge=1)] = 12,
    include_hce: Annotated[bool, Field(strict=True)] = True,
) -> list[dict[str, Any]]:
    """Select commentary-worthy plies by computing NNUE and HCE evidence from a PGN."""
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
        raise ValueError("depth must be a positive integer.")
    if not isinstance(include_hce, bool):
        raise ValueError("include_hce must be a boolean.")

    parsed, records = await build_key_move_evidence(pgn, depth)
    candidates = []
    for index, (ply, record) in enumerate(zip(parsed["plies"], records, strict=True)):
        previous_record = records[index - 1] if index > 0 else None
        candidate = _build_key_move_candidate(ply, record, previous_record)
        if candidate is not None:
            candidates.append(candidate)
    selected = _select_key_move_candidates(candidates, len(parsed["plies"]))
    if include_hce and selected:
        await _attach_key_move_hce(parsed, records, selected)
    return selected


@mcp.tool
async def analyze_game(
    pgn: str,
    depth: Annotated[int, Field(strict=True, ge=1)] = 12,
) -> list[dict[str, Any]]:
    """Analyze one legal PGN into a chronological per-ply move-evidence array."""
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
        raise ValueError("depth must be a positive integer.")

    parsed, records = await build_key_move_evidence(pgn, depth)
    hce_by_ply = await _attach_analyze_game_hce(parsed, records)

    elements: list[dict[str, Any]] = []
    for ply, record in zip(parsed["plies"], records, strict=True):
        maps = hce_by_ply.get(ply["ply"], {})
        if "baseline" not in maps or "played" not in maps:
            raise RuntimeError(f"Missing HCE evidence for ply {ply['ply']}.")
        hce = compute_hce_deltas(maps["baseline"], maps["played"])
        missed = None
        if "best" in maps:
            missed = compute_hce_deltas(maps["best"], maps["played"])
        elements.append(_format_analyze_game_element(ply, record, hce, missed))
    return elements
