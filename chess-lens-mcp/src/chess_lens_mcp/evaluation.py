"""Stockfish eval-output parsing and HCE feature calculations."""

import re
from typing import Any

import chess


def _as_float(value: Any) -> float | None:
    """Return a float for numeric values and None for non-numeric placeholders."""
    if value in (None, "----"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_eval_output(output_text: str) -> dict[str, Any]:
    """Parse Stockfish's ASCII eval table into a structured JSON dictionary."""
    features: dict[str, Any] = {}
    lines = output_text.splitlines()

    total_eval = None
    winning_chances = None

    for line in lines:
        match = re.match(r"^\s*\|?\s*([a-zA-Z\s\.]+?)\s*\|(.*)$", line)
        if match:
            term = match.group(1).strip()
            if term.lower() in {"term", ""}:
                continue

            cols = match.group(2).split("|")
            if len(cols) < 3:
                continue

            w_vals = cols[0].split()
            b_vals = cols[1].split()
            t_vals = cols[2].split()

            if len(w_vals) == 2 and len(b_vals) == 2 and len(t_vals) == 2:
                w_mg = _as_float(w_vals[0])
                w_eg = _as_float(w_vals[1])
                b_mg = _as_float(b_vals[0])
                b_eg = _as_float(b_vals[1])
                t_mg = _as_float(t_vals[0])
                t_eg = _as_float(t_vals[1])
                if not all(value is not None for value in (t_mg, t_eg)):
                    continue
                features[term] = {
                    "White": {"MG": w_mg, "EG": w_eg},
                    "Black": {"MG": b_mg, "EG": b_eg},
                    "Total": {"MG": t_mg, "EG": t_eg},
                }

        score_match = re.search(
            r"(?:Total [Ee]valuation|Final evaluation|Classical evaluation)"
            r"\s*:?\s*([-+]?\d+\.\d+)",
            line,
        )
        if score_match:
            total_eval = float(score_match.group(1))

        wc_match = re.search(r"Winning chances\s*:\s*([\d\.]+)%", line)
        if wc_match:
            winning_chances = f"{float(wc_match.group(1))}%"

    return {
        "total_evaluation": {
            "score": total_eval,
            "winning_chances": winning_chances,
        },
        "features": features,
    }


def compute_material_phase(board: chess.Board) -> int:
    """Calculate non-pawn material phase P in [0, 24]."""
    material_weights = {
        chess.QUEEN: 4,
        chess.ROOK: 2,
        chess.BISHOP: 1,
        chess.KNIGHT: 1,
    }
    phase_units = sum(
        weight * len(board.pieces(piece_type, color))
        for piece_type, weight in material_weights.items()
        for color in chess.COLORS
    )
    return min(24, max(0, phase_units))


def _blend_tapered(midgame: float, endgame: float, phase: int) -> float:
    """Blend midgame/endgame values by material phase and round to 2 decimals."""
    return round(((midgame * phase) + (endgame * (24 - phase))) / 24.0, 2)


def _tapered_pair(value: Any) -> tuple[float, float] | None:
    """Return a validated ``(MG, EG)`` pair from a feature's ``Total`` entry."""
    if not isinstance(value, dict):
        return None
    total_pair = value.get("Total")
    if not isinstance(total_pair, dict):
        return None
    midgame = total_pair.get("MG")
    endgame = total_pair.get("EG")
    if isinstance(midgame, (int, float)) and isinstance(endgame, (int, float)):
        return float(midgame), float(endgame)
    return None


def _total_evaluation(
    hce_data: dict[str, Any], features: dict[str, Any], phase: int
) -> float:
    """Resolve the blended total, falling back to the reported total evaluation."""
    pair = _tapered_pair(features.get("Total"))
    if pair is not None:
        return _blend_tapered(*pair, phase)
    score = hce_data.get("total_evaluation", {}).get("score")
    if isinstance(score, (int, float)):
        return round(float(score), 2)
    return 0.0


def blend_and_prune_hce(
    hce_data: dict[str, Any], board: chess.Board
) -> dict[str, float]:
    """Blend tapered evaluations, normalize names, and remove empty features."""
    phase = compute_material_phase(board)
    raw_features = hce_data.get("features", {})
    features: dict[str, Any] = raw_features if isinstance(raw_features, dict) else {}

    result: dict[str, float] = {"total": _total_evaluation(hce_data, features, phase)}

    for term, term_data in features.items():
        clean_name = re.sub(r"\s+", "_", term.strip())
        if clean_name.lower() == "total":
            continue
        pair = _tapered_pair(term_data)
        if pair is None:
            continue
        blended = _blend_tapered(*pair, phase)
        if blended != 0.0:
            result[clean_name] = blended

    return result


def compute_hce_deltas(
    baseline: dict[str, float], candidate: dict[str, float]
) -> dict[str, float]:
    """Compute candidate-minus-baseline shifts, always including ``total``."""
    deltas: dict[str, float] = {}

    b_total = baseline.get("total", 0.0)
    c_total = candidate.get("total", 0.0)
    deltas["total"] = round(c_total - b_total, 2)

    all_keys = set(baseline.keys()) | set(candidate.keys())
    for key in sorted(all_keys):
        if key == "total":
            continue
        b_val = baseline.get(key, 0.0)
        c_val = candidate.get(key, 0.0)
        shift = round(c_val - b_val, 2)
        if abs(shift) > 0.05:
            deltas[key] = shift

    return deltas
