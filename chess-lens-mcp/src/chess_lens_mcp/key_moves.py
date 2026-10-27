"""PGN-driven key-move evidence generation and selection."""

import math
from typing import Any

import chess
import chess.engine

from .engine import sf
from .evaluation import (
    blend_and_prune_hce,
    compute_hce_deltas,
    compute_material_phase,
    parse_eval_output,
)
from .parsing import parse_pgn_game


def _key_move_eval_from_scored(scored: dict[str, Any]) -> dict[str, Any]:
    """Serialize a White-relative scored search result into a record evaluation."""
    if scored["mate_in"] is not None:
        return {"mate_in": scored["mate_in"]}
    return {"score_cp": scored["centipawns"]}


def _terminal_key_move_played_eval(board: chess.Board, mover: str) -> dict[str, Any]:
    """Synthesize the played evaluation for a move that ends the game."""
    if board.is_checkmate():
        return {"mate_in": 1 if mover == "white" else -1}
    return {"score_cp": 0}


async def _analyse_key_move_positions(
    boards: list[chess.Board], depth: int
) -> list[list[chess.engine.InfoDict]]:
    """Run one NNUE MultiPV analysis per position in a single engine process."""
    analyses: list[list[chess.engine.InfoDict]] = []
    _, engine = await chess.engine.popen_uci(sf.path)
    try:
        await engine.configure({"Use NNUE": True})
        for board in boards:
            legal_move_count = board.legal_moves.count()
            if not legal_move_count:
                analyses.append([])
                continue
            infos = await engine.analyse(
                board,
                chess.engine.Limit(depth=depth),
                multipv=min(2, legal_move_count),
            )
            analyses.append(infos)
    finally:
        await engine.quit()
    return analyses


async def build_key_move_evidence(
    pgn: str, depth: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Derive White-relative per-ply NNUE evidence from one legal PGN."""
    if not isinstance(pgn, str):
        raise ValueError("PGN input must be a string.")
    parsed = parse_pgn_game(pgn)
    plies: list[dict[str, Any]] = parsed["plies"]

    boards = [chess.Board(plies[0]["before_fen"])]
    for ply in plies:
        boards.append(chess.Board(ply["after_fen"]))
    analyses = await _analyse_key_move_positions(boards, depth)

    records: list[dict[str, Any]] = []
    for index, ply in enumerate(plies):
        before_analysis = analyses[index]
        if not before_analysis:
            raise RuntimeError(
                f"No NNUE analysis available for the position before ply {ply['ply']}."
            )
        best_scored = _score_search_info(before_analysis[0], chess.WHITE)
        if not best_scored["pv"]:
            raise RuntimeError(
                f"No best move available for the position before ply {ply['ply']}."
            )
        record: dict[str, Any] = {
            "ply": ply["ply"],
            "best_move": best_scored["pv"][0],
            "best_eval": _key_move_eval_from_scored(best_scored),
        }
        if len(before_analysis) >= 2:
            record["second_eval"] = _key_move_eval_from_scored(
                _score_search_info(before_analysis[1], chess.WHITE)
            )
        after_analysis = analyses[index + 1]
        if after_analysis:
            record["played_eval"] = _key_move_eval_from_scored(
                _score_search_info(after_analysis[0], chess.WHITE)
            )
        else:
            record["played_eval"] = _terminal_key_move_played_eval(
                boards[index + 1], ply["mover"]
            )
        records.append(record)
    return parsed, records


def _key_move_white_win_probability(evaluation: dict[str, Any]) -> float:
    """Convert a White-relative evaluation to a stable probability in [0, 1]."""
    if "mate_in" in evaluation:
        return 1.0 if evaluation["mate_in"] > 0 else 0.0
    pawns = evaluation["score_cp"] / 100.0
    log_odds = pawns * (math.log(10.0) / 4.0)
    if log_odds >= 0.0:
        return 1.0 / (1.0 + math.exp(-log_odds))
    exp_log_odds = math.exp(log_odds)
    return exp_log_odds / (1.0 + exp_log_odds)


def _key_move_mover_win_probability(evaluation: dict[str, Any], mover: str) -> float:
    white_probability = _key_move_white_win_probability(evaluation)
    if mover == "white":
        return white_probability
    if mover == "black":
        return 1.0 - white_probability
    raise ValueError("mover must be 'white' or 'black'.")


_KEY_MOVE_TRIGGER_ORDER = (
    "major_blunder_turning_point",
    "brilliant_only_move",
    "missed_opportunity",
    "evaluation_swing",
)


def _key_move_trigger_candidates(
    delta_w: float, sharpness: Any, swing: Any
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if delta_w > 0.25:
        candidates.append(
            {
                "type": "major_blunder_turning_point",
                "priority": delta_w * 2.0,
            }
        )
    if sharpness is not None and delta_w < 0.02 and sharpness > 0.30:
        candidates.append({"type": "brilliant_only_move", "priority": sharpness * 1.5})
    if delta_w > 0.12:
        candidates.append({"type": "missed_opportunity", "priority": delta_w})
    if swing is not None and swing > 0.20:
        candidates.append({"type": "evaluation_swing", "priority": swing * 1.2})
    return candidates


def _select_key_move_primary_trigger(
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not candidates:
        return None
    trigger_order = {name: index for index, name in enumerate(_KEY_MOVE_TRIGGER_ORDER)}
    return min(
        candidates,
        key=lambda candidate: (
            -candidate["priority"],
            trigger_order[candidate["type"]],
        ),
    )


def _key_move_phase(board: chess.Board) -> str:
    phase = compute_material_phase(board)
    if phase >= 20:
        return "opening"
    if phase >= 8:
        return "middlegame"
    return "endgame"


def _key_move_material_is_equal(board: chess.Board) -> bool:
    values = {
        chess.PAWN: 1,
        chess.KNIGHT: 3,
        chess.BISHOP: 3,
        chess.ROOK: 5,
        chess.QUEEN: 9,
    }
    material = {
        color: sum(
            value * len(board.pieces(piece_type, color))
            for piece_type, value in values.items()
        )
        for color in chess.COLORS
    }
    return material[chess.WHITE] == material[chess.BLACK]


def _build_key_move_candidate(
    ply: dict[str, Any],
    record: dict[str, Any],
    previous_record: dict[str, Any] | None,
) -> dict[str, Any] | None:
    mover = ply["mover"]
    best_eval = record["best_eval"]
    played_eval = record["played_eval"]
    best_mover_w = _key_move_mover_win_probability(best_eval, mover)
    played_mover_w = _key_move_mover_win_probability(played_eval, mover)
    delta_w = best_mover_w - played_mover_w

    second_eval = record.get("second_eval")
    sharpness = None
    if second_eval is not None:
        sharpness = best_mover_w - _key_move_mover_win_probability(second_eval, mover)

    swing = None
    if previous_record is not None:
        swing = abs(
            _key_move_white_win_probability(played_eval)
            - _key_move_white_win_probability(previous_record["played_eval"])
        )

    triggers = _key_move_trigger_candidates(delta_w, sharpness, swing)
    primary = _select_key_move_primary_trigger(triggers)
    if primary is None:
        return None
    after_board = chess.Board(ply["after_fen"])
    candidate: dict[str, Any] = {
        "ply": ply["ply"],
        "move": ply["san"],
        "analysis_move": record["best_move"],
        "type": primary["type"],
        "priority": round(primary["priority"], 4),
        "delta_w": round(delta_w, 4),
        "best_eval": dict(best_eval),
        "played_eval": dict(played_eval),
        "_phase": _key_move_phase(after_board),
        "_priority": primary["priority"],
    }
    if sharpness is not None:
        candidate["sharpness"] = round(sharpness, 4)
    if swing is not None:
        candidate["swing"] = round(swing, 4)

    if "score_cp" in best_eval and "score_cp" in played_eval:
        cp_change = best_eval["score_cp"] - played_eval["score_cp"]
        if mover == "black":
            cp_change = -cp_change
        candidate["nnue_eval_change_cp"] = round(cp_change, 2)

    if _key_move_material_is_equal(after_board) and delta_w > 0.15:
        candidate["tags"] = ["positional_shift"]

    hce = record.get("hce", {})
    if "baseline" in hce and "played" in hce:
        candidate["hce_deltas"] = compute_hce_deltas(hce["baseline"], hce["played"])
    if "played" in hce and "best" in hce:
        candidate["missed_hce_deltas"] = compute_hce_deltas(hce["best"], hce["played"])
    return candidate


def _key_move_selection_budget(ply_count: int) -> int:
    if ply_count <= 0:
        return 0
    full_moves = math.ceil(ply_count / 2)
    return max(1, math.ceil(15 * full_moves / 40))


def _select_key_move_candidates(
    candidates: list[dict[str, Any]], ply_count: int
) -> list[dict[str, Any]]:
    """Reserve positional phase representatives, priority-fill, and order by ply."""
    budget = _key_move_selection_budget(ply_count)
    if budget == 0 or not candidates:
        return []
    ranked = sorted(candidates, key=lambda item: (-item["_priority"], item["ply"]))

    representatives: dict[str, dict[str, Any]] = {}
    for candidate in ranked:
        if "positional_shift" in candidate.get("tags", []):
            representatives.setdefault(candidate["_phase"], candidate)
    phase_representatives = sorted(
        representatives.values(), key=lambda item: (-item["_priority"], item["ply"])
    )
    selected = phase_representatives[:budget]
    selected_plies = {candidate["ply"] for candidate in selected}
    for candidate in ranked:
        if len(selected) >= budget:
            break
        if candidate["ply"] not in selected_plies:
            selected.append(candidate)
            selected_plies.add(candidate["ply"])

    result = []
    for candidate in sorted(selected, key=lambda item: item["ply"]):
        result.append(
            {
                key: value
                for key, value in candidate.items()
                if key not in {"_phase", "_priority"}
            }
        )
    return result


async def _attach_key_move_hce(
    parsed: dict[str, Any],
    records: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> None:
    """Compute baseline/played/best HCE comparisons for selected plies in one batch."""
    plies_by_number = {ply["ply"]: ply for ply in parsed["plies"]}
    records_by_number = {record["ply"]: record for record in records}

    jobs: list[str] = []
    layout: list[tuple[dict[str, Any], str, chess.Board]] = []
    for candidate in selected:
        ply = plies_by_number[candidate["ply"]]
        baseline_board = chess.Board(ply["before_fen"])
        played_board = chess.Board(ply["after_fen"])
        jobs.append(f"position fen {ply['before_fen']}")
        layout.append((candidate, "baseline", baseline_board))
        jobs.append(f"position fen {ply['after_fen']}")
        layout.append((candidate, "played", played_board))
        best_move = records_by_number[candidate["ply"]].get("best_move")
        if best_move:
            best_board = chess.Board(ply["before_fen"])
            best_board.push(chess.Move.from_uci(best_move))
            jobs.append(f"position fen {ply['before_fen']} moves {best_move}")
            layout.append((candidate, "best", best_board))

    if not jobs:
        return
    outputs = await sf.run_hce_batch(jobs)
    if len(outputs) != len(jobs):
        raise RuntimeError("HCE evaluation returned an unexpected number of results.")

    maps_by_ply: dict[int, dict[str, dict[str, float]]] = {}
    for (candidate, comparison, board), output in zip(layout, outputs, strict=True):
        raw = parse_eval_output(output)
        maps_by_ply.setdefault(candidate["ply"], {})[comparison] = blend_and_prune_hce(
            raw, board
        )

    for candidate in selected:
        hce = maps_by_ply.get(candidate["ply"], {})
        if "baseline" in hce and "played" in hce:
            candidate["hce_deltas"] = compute_hce_deltas(hce["baseline"], hce["played"])
        if "played" in hce and "best" in hce:
            candidate["missed_hce_deltas"] = compute_hce_deltas(
                hce["best"], hce["played"]
            )


def _evidence_eval_centipawns(evaluation: dict[str, Any]) -> int:
    """Fold a White-relative evaluation record into centipawns.

    Mate evaluations use the existing signed convention
    ``sign * (10000 - 100 * |mate_in|)`` so no separate mate field is needed.
    """
    mate_in = evaluation.get("mate_in")
    if mate_in is not None:
        return int((1 if mate_in >= 0 else -1) * (10000 - 100 * abs(mate_in)))
    return int(evaluation["score_cp"])


async def _attach_analyze_game_hce(
    parsed: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[int, dict[str, dict[str, float]]]:
    """Batch baseline/played/best HCE maps for every ply in one engine pass.

    The job list is deduplicated by canonical ``chess.Board.fen()`` across all
    plies. The best-move result position is added only when the played move
    differs from the engine best, so a best ply has no ``best`` comparison.
    """
    records_by_number = {record["ply"]: record for record in records}

    jobs: list[str] = []
    job_index_by_fen: dict[str, int] = {}
    layout: list[tuple[int, str, chess.Board, int]] = []

    def _enqueue(ply_number: int, comparison: str, board: chess.Board) -> None:
        fen = board.fen()
        index = job_index_by_fen.get(fen)
        if index is None:
            index = len(jobs)
            job_index_by_fen[fen] = index
            jobs.append(f"position fen {fen}")
        layout.append((ply_number, comparison, board, index))

    for ply in parsed["plies"]:
        _enqueue(ply["ply"], "baseline", chess.Board(ply["before_fen"]))
        _enqueue(ply["ply"], "played", chess.Board(ply["after_fen"]))
        best_move = records_by_number[ply["ply"]]["best_move"]
        if best_move != ply["uci"]:
            best_board = chess.Board(ply["before_fen"])
            best_board.push(chess.Move.from_uci(best_move))
            _enqueue(ply["ply"], "best", best_board)

    if not jobs:
        return {}
    outputs = await sf.run_hce_batch(jobs)
    if len(outputs) != len(jobs):
        raise RuntimeError("HCE evaluation returned an unexpected number of results.")

    maps_by_ply: dict[int, dict[str, dict[str, float]]] = {}
    for ply_number, comparison, board, index in layout:
        raw = parse_eval_output(outputs[index])
        maps_by_ply.setdefault(ply_number, {})[comparison] = blend_and_prune_hce(
            raw, board
        )
    return maps_by_ply


def _format_analyze_game_element(
    ply: dict[str, Any],
    record: dict[str, Any],
    hce: dict[str, float],
    missed: dict[str, float] | None,
) -> dict[str, Any]:
    """Build one delta-only per-ply element applying the omit rules."""
    element: dict[str, Any] = {
        "ply": ply["ply"],
        "move": ply["san"],
    }
    if ply["uci"] != record["best_move"]:
        element["best"] = record["best_move"]

    loss = _evidence_eval_centipawns(record["best_eval"]) - _evidence_eval_centipawns(
        record["played_eval"]
    )
    if ply["mover"] == "black":
        loss = -loss
    if loss != 0:
        element["cpl"] = loss

    element["hce"] = hce
    if missed:
        element["missed"] = missed
    return element


def _score_search_info(
    info: chess.engine.InfoDict, perspective: chess.Color
) -> dict[str, Any]:
    score = info["score"].pov(perspective)
    mate_in = score.mate()
    centipawns = (
        (1 if mate_in >= 0 else -1) * (10000 - 100 * abs(mate_in))
        if mate_in is not None
        else score.score()
    )
    return {
        "centipawns": centipawns,
        "mate_in": mate_in,
        "pv": [move.uci() for move in info.get("pv", [])],
    }
