"""Tests for the Stockfish HCE MCP replacement tools.

Run:
    pytest -v
    pytest -m "not integration"
"""

import asyncio
import json
import pathlib
import shutil
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import chess
import chess.engine
import chess.pgn
import pytest

from chess_lens_mcp import engine
from chess_lens_mcp.engine import StockfishEngine
from chess_lens_mcp.evaluation import (
    blend_and_prune_hce,
    compute_hce_deltas,
    compute_material_phase,
    parse_eval_output,
)
from chess_lens_mcp.key_moves import (
    _build_key_move_candidate,
    _evidence_eval_centipawns,
    _format_analyze_game_element,
    _key_move_mover_win_probability,
    _key_move_phase,
    _key_move_selection_budget,
    _key_move_trigger_candidates,
    _key_move_white_win_probability,
    _score_search_info,
    _select_key_move_candidates,
    _select_key_move_primary_trigger,
    build_key_move_evidence,
)
from chess_lens_mcp.parsing import parse_pgn_game
from chess_lens_mcp.tools import mcp

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
E4_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
SAMPLE_PGN = '[Event "Test"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n'
KEY_MOVE_PGN = '[Result "*"]\n\n1. e4 e5 *'
STOCKFISH_BIN = next(
    (
        path
        for path in (
            "/usr/games/stockfish",
            "/usr/local/bin/stockfish",
            shutil.which("stockfish"),
        )
        if path and pathlib.Path(path).exists()
    ),
    None,
)
needs_stockfish = pytest.mark.skipif(
    STOCKFISH_BIN is None, reason="Stockfish binary not found"
)


def _try_json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _extract_tool_data(result):
    if isinstance(result, dict):
        return result
    for attribute in ("structuredContent", "data", "structured_content"):
        value = getattr(result, attribute, None)
        if value is not None:
            return value
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if text:
            data = _try_json(text)
            if data is not None:
                return data
    return result


def _make_mock_process(lines):
    process = MagicMock()
    process.stdin = MagicMock()
    process.stdin.write = MagicMock()
    process.stdin.drain = AsyncMock()
    process.wait = AsyncMock(return_value=0)
    process.stdout = MagicMock()
    process.stdout.readline = AsyncMock(
        side_effect=[(line + "\n").encode() for line in lines] + [b""]
    )
    return process


def _nnue_line(score_cp=None, mate_in=None, uci="e2e4"):
    score = (
        chess.engine.PovScore(chess.engine.Mate(mate_in), chess.WHITE)
        if mate_in is not None
        else chess.engine.PovScore(chess.engine.Cp(score_cp), chess.WHITE)
    )
    return {"score": score, "pv": [chess.Move.from_uci(uci)]}


def _mock_engine(lines_by_fen=None, constant_lines=None):
    """Build a fake UCI engine whose analysis is keyed by FEN or constant."""
    engine = MagicMock()
    engine.configure = AsyncMock()
    engine.quit = AsyncMock()

    async def analyse(board, limit, multipv=None, root_moves=None):
        lines = (
            constant_lines if constant_lines is not None else lines_by_fen[board.fen()]
        )
        return lines[:multipv] if multipv else lines

    engine.analyse = AsyncMock(side_effect=analyse)
    return engine


def _qualifying_engine(pgn):
    """Engine stub whose White-relative swing selects exactly one ply."""
    parsed = parse_pgn_game(pgn)
    start_fen = parsed["plies"][0]["before_fen"]
    after_fen = parsed["plies"][0]["after_fen"]
    return _mock_engine(
        lines_by_fen={
            start_fen: [
                _nnue_line(score_cp=100, uci="e2e4"),
                _nnue_line(score_cp=0, uci="d2d4"),
            ],
            after_fen: [
                _nnue_line(score_cp=-100, uci="d7d5"),
                _nnue_line(score_cp=-120, uci="g8f6"),
            ],
        }
    )


class TestSharedHelpers:
    def test_pgn_parser_records_pre_and_post_move_fens(self):
        parsed = parse_pgn_game(SAMPLE_PGN)
        assert parsed["plies"][0]["before_fen"] == START_FEN
        assert parsed["plies"][0]["after_fen"] == E4_FEN
        assert parsed["plies"][1]["mover"] == "black"

    @pytest.mark.parametrize("pgn", ["", "not a PGN", SAMPLE_PGN + SAMPLE_PGN])
    def test_pgn_parser_rejects_missing_or_multiple_games(self, pgn):
        with pytest.raises(ValueError):
            parse_pgn_game(pgn)

    def test_pgn_parser_rejects_illegal_moves(self):
        with pytest.raises(ValueError, match="Invalid PGN"):
            parse_pgn_game('[Event "Illegal"]\n\n1. e5 *\n')

    def test_hce_output_parser_reads_score_and_features(self):
        parsed = parse_eval_output(
            "|      Pawns |  0.15 -0.02 |  0.15 -0.02 |  0.00  0.00 |\n"
            "Final evaluation +0.63 (white side)\n"
        )
        assert parsed["total_evaluation"]["score"] == 0.63
        assert parsed["features"]["Pawns"]["Total"] == {"MG": 0.0, "EG": 0.0}

    def test_hce_output_parser_handles_winning_chances_and_dashes(self):
        parsed = parse_eval_output(
            "|   Material |  ----  ---- |  ----  ---- |  0.00  0.00 |\n"
            "Winning chances: 52.5%\n"
        )
        assert parsed["features"]["Material"]["White"] == {"MG": None, "EG": None}
        assert parsed["total_evaluation"]["winning_chances"] == "52.5%"

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("Total Evaluation: 1.23", 1.23),
            ("Total evaluation: -0.44", -0.44),
            ("Classical evaluation: 0.00", 0.0),
        ],
    )
    def test_hce_output_parser_accepts_score_label_variants(self, line, expected):
        assert parse_eval_output(line)["total_evaluation"]["score"] == expected

    def test_hce_output_parser_ignores_headers_and_malformed_rows(self):
        parsed = parse_eval_output(
            "|    Term    |    White    |    Black    |    Total    |\n"
            "+------------+-------------+-------------+-------------+\n"
            "| Broken | 0.1 | 0.2 | nonsense |\n"
        )
        assert not parsed["features"]

    def test_search_score_preserves_mate_and_perspective(self):
        info: chess.engine.InfoDict = {
            "score": chess.engine.PovScore(chess.engine.Mate(3), chess.WHITE),
            "pv": [chess.Move.from_uci("e2e4")],
        }
        scored = _score_search_info(info, chess.WHITE)
        assert scored["centipawns"] == 9700
        assert scored["mate_in"] == 3
        assert scored["pv"] == ["e2e4"]

    def test_compute_material_phase(self):
        assert compute_material_phase(chess.Board()) == 24
        pawn_endgame = chess.Board("8/4k3/8/4P3/8/8/4K3/8 w - - 0 1")
        assert compute_material_phase(pawn_endgame) == 0
        assert (
            compute_material_phase(chess.Board("8/4k3/8/3q4/3Q4/8/4K3/8 w - - 0 1"))
            == 8
        )

    def test_blend_and_prune_hce(self):
        board = chess.Board(START_FEN)
        hce_data = {
            "total_evaluation": {"score": 0.50},
            "features": {
                "Material": {"Total": {"MG": 0.20, "EG": 0.40}},
                "King safety": {"Total": {"MG": 0.10, "EG": -0.10}},
                "Queens": {"Total": {"MG": 0.00, "EG": 0.00}},
                "Winnable": {"Total": {"MG": None, "EG": 0.50}},
                "Total": {"Total": {"MG": 0.24, "EG": 0.00}},
            },
        }
        blended = blend_and_prune_hce(hce_data, board)
        assert blended["total"] == 0.24
        assert blended["Material"] == 0.20
        assert blended["King_safety"] == 0.10
        assert "Queens" not in blended
        assert "Winnable" not in blended

    def test_blend_and_prune_hce_keeps_zero_total(self):
        board = chess.Board()
        hce_data = {
            "total_evaluation": {"score": 0.00},
            "features": {
                "Total": {"Total": {"MG": 0.00, "EG": 0.00}},
            },
        }
        blended = blend_and_prune_hce(hce_data, board)
        assert blended["total"] == 0.0

    def test_compute_hce_deltas(self):
        baseline = {"total": 0.10, "Material": 0.20, "Mobility": -0.10, "Threats": 0.05}
        candidate = {"total": 0.12, "Material": 0.20, "Mobility": 0.00, "Threats": 0.12}
        deltas = compute_hce_deltas(baseline, candidate)
        assert deltas["total"] == 0.02
        assert "Material" not in deltas
        assert deltas["Mobility"] == 0.10
        assert deltas["Threats"] == 0.07


class TestAnalyzeGameElementFormatting:
    def _ply(self, uci="e2e4", san="e4", mover="white", ply=1):
        return {"ply": ply, "uci": uci, "san": san, "mover": mover}

    def test_folds_mate_into_signed_centipawns(self):
        assert _evidence_eval_centipawns({"mate_in": 3}) == 9700
        assert _evidence_eval_centipawns({"mate_in": -1}) == -9900
        assert _evidence_eval_centipawns({"score_cp": -45}) == -45

    def test_omits_best_cpl_and_missed_when_played_is_best(self):
        record = {
            "best_move": "e2e4",
            "best_eval": {"score_cp": 30},
            "played_eval": {"score_cp": 30},
        }
        element = _format_analyze_game_element(
            self._ply(), record, {"total": 0.0}, None
        )
        assert element == {"ply": 1, "move": "e4", "hce": {"total": 0.0}}
        assert "best" not in element
        assert "cpl" not in element
        assert "missed" not in element

    def test_includes_best_missed_and_positive_white_loss(self):
        record = {
            "best_move": "d2d4",
            "best_eval": {"score_cp": 50},
            "played_eval": {"score_cp": 10},
        }
        element = _format_analyze_game_element(
            self._ply(), record, {"total": 0.1}, {"total": -0.2}
        )
        assert element["best"] == "d2d4"
        assert element["cpl"] == 40
        assert element["hce"] == {"total": 0.1}
        assert element["missed"] == {"total": -0.2}

    def test_black_loss_is_mover_relative(self):
        record = {
            "best_move": "c7c5",
            "best_eval": {"score_cp": -100},
            "played_eval": {"score_cp": -50},
        }
        element = _format_analyze_game_element(
            self._ply(uci="e7e5", san="e5", mover="black", ply=2),
            record,
            {"total": 0.0},
            {"total": 0.0},
        )
        assert element["best"] == "c7c5"
        assert element["cpl"] == 50

    def test_omits_cpl_and_empty_missed_mapping(self):
        record = {
            "best_move": "d2d4",
            "best_eval": {"score_cp": 0},
            "played_eval": {"score_cp": 0},
        }
        element = _format_analyze_game_element(self._ply(), record, {"total": 0.0}, {})
        assert element["best"] == "d2d4"
        assert "cpl" not in element
        assert "missed" not in element
        assert element["hce"] == {"total": 0.0}


class TestKeyMoveEvidenceBuilder:
    @pytest.mark.asyncio
    async def test_derives_best_second_and_played_from_position_sequence(self):
        parsed = parse_pgn_game(KEY_MOVE_PGN)
        start_fen = parsed["plies"][0]["before_fen"]
        after_e4_fen = parsed["plies"][1]["before_fen"]
        after_e5_fen = parsed["plies"][1]["after_fen"]
        engine = _mock_engine(
            lines_by_fen={
                start_fen: [
                    _nnue_line(score_cp=30, uci="e2e4"),
                    _nnue_line(score_cp=20, uci="d2d4"),
                ],
                after_e4_fen: [
                    _nnue_line(score_cp=25, uci="e7e5"),
                    _nnue_line(score_cp=10, uci="c7c5"),
                ],
                after_e5_fen: [
                    _nnue_line(score_cp=20, uci="g1f3"),
                    _nnue_line(score_cp=15, uci="b1c3"),
                ],
            }
        )
        with patch(
            "chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))
        ):
            parsed_out, records = await build_key_move_evidence(KEY_MOVE_PGN, 12)

        assert parsed_out["plies"] == parsed["plies"]
        assert records[0] == {
            "ply": 1,
            "best_move": "e2e4",
            "best_eval": {"score_cp": 30},
            "second_eval": {"score_cp": 20},
            "played_eval": {"score_cp": 25},
        }
        assert records[1] == {
            "ply": 2,
            "best_move": "e7e5",
            "best_eval": {"score_cp": 25},
            "second_eval": {"score_cp": 10},
            "played_eval": {"score_cp": 20},
        }
        engine.configure.assert_awaited_once_with({"Use NNUE": True})
        engine.quit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_forces_single_legal_move_multipv_and_omits_second_eval(self):
        pgn = '[Result "*"]\n\n1. e4 e5 2. Qh5 Nc6 3. Qxf7+ Kxf7 *'
        engine = _mock_engine(
            constant_lines=[
                _nnue_line(score_cp=30, uci="e2e4"),
                _nnue_line(score_cp=20, uci="d2d4"),
            ]
        )
        with patch(
            "chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))
        ):
            _, records = await build_key_move_evidence(pgn, 12)

        # The position before ply 6 (Kxf7) is a check with exactly one legal move.
        forced_ply = records[5]
        assert forced_ply["ply"] == 6
        assert forced_ply["best_move"] == "e2e4"
        assert "second_eval" not in forced_ply
        # A position with two or more legal moves keeps its second-best line.
        assert records[4]["second_eval"] == {"score_cp": 20}
        multipv_args = [
            call.kwargs["multipv"] for call in engine.analyse.call_args_list
        ]
        assert min(multipv_args) == 1

    @pytest.mark.asyncio
    async def test_synthesizes_terminal_played_eval_for_game_ending_move(self):
        pgn = '[Result "0-1"]\n\n1. f3 e5 2. g4 Qh4# 0-1\n'
        engine = _mock_engine(
            constant_lines=[
                _nnue_line(score_cp=30, uci="e2e4"),
                _nnue_line(score_cp=20, uci="d2d4"),
            ]
        )
        with patch(
            "chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))
        ):
            _, records = await build_key_move_evidence(pgn, 12)

        # Black's Qh4# ends the game, so the played evaluation is a mate for Black.
        terminal_ply = records[3]
        assert terminal_ply["ply"] == 4
        assert terminal_ply["played_eval"] == {"mate_in": -1}
        # Terminal positions are not searched, so only four positions are analysed.
        assert engine.analyse.await_count == 4

    @pytest.mark.asyncio
    async def test_rejects_invalid_pgn_before_running_the_engine(self):
        with patch("chess.engine.popen_uci") as popen, pytest.raises(ValueError):
            await build_key_move_evidence("not a PGN", 12)
        popen.assert_not_called()


class TestKeyMoveHceEvidence:
    @pytest.mark.asyncio
    async def test_batches_selected_ply_comparisons_and_computes_deltas(self):
        from fastmcp.client import Client

        pgn = '[Result "*"]\n\n1. d4 *'
        parsed = parse_pgn_game(pgn)
        start_fen = parsed["plies"][0]["before_fen"]
        after_fen = parsed["plies"][0]["after_fen"]
        engine = _mock_engine(
            lines_by_fen={
                start_fen: [
                    _nnue_line(score_cp=100, uci="e2e4"),
                    _nnue_line(score_cp=0, uci="d2d4"),
                ],
                after_fen: [
                    _nnue_line(score_cp=-100, uci="d7d5"),
                    _nnue_line(score_cp=-120, uci="g8f6"),
                ],
            }
        )
        captured = {}

        async def hce_batch(commands):
            captured["commands"] = commands
            return [
                "Final evaluation +0.10 (white side)",
                "Final evaluation +0.30 (white side)",
                "Final evaluation +0.20 (white side)",
            ]

        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock(side_effect=hce_batch)
            ),
        ):
            async with Client(mcp) as client:
                result = await client.call_tool("identify_key_moves", {"pgn": pgn})

        data = _extract_tool_data(result)
        assert captured["commands"] == [
            f"position fen {start_fen}",
            f"position fen {after_fen}",
            f"position fen {start_fen} moves e2e4",
        ]
        assert len(data) == 1
        assert data[0]["ply"] == 1
        assert data[0]["type"] == "major_blunder_turning_point"
        assert data[0]["hce_deltas"] == {"total": 0.2}
        assert data[0]["missed_hce_deltas"] == {"total": 0.1}


class TestKeyMoveProbabilities:
    @pytest.mark.parametrize("score_cp", [-100, 0, 100])
    def test_centipawn_probability_uses_bounded_logistic_mapping(self, score_cp):
        expected = 1.0 / (1.0 + 10 ** (-(score_cp / 100.0) / 4.0))
        probability = _key_move_white_win_probability({"score_cp": score_cp})
        assert 0.0 <= probability <= 1.0
        assert probability == pytest.approx(expected)

    def test_extreme_centipawn_scores_remain_bounded(self):
        assert _key_move_white_win_probability({"score_cp": 10_000_000}) == 1.0
        assert _key_move_white_win_probability({"score_cp": -10_000_000}) == 0.0

    @pytest.mark.parametrize(
        "evaluation,expected",
        [({"mate_in": 3}, 1.0), ({"mate_in": -3}, 0.0), ({"score_cp": 0}, 0.5)],
    )
    def test_mates_and_centipawns_are_white_relative(self, evaluation, expected):
        assert _key_move_white_win_probability(evaluation) == expected

    def test_mover_probability_is_complemented_for_black(self):
        evaluation = {"score_cp": 100}
        white_probability = _key_move_mover_win_probability(evaluation, "white")
        black_probability = _key_move_mover_win_probability(evaluation, "black")
        assert white_probability == pytest.approx(1.0 / (1.0 + 10**-0.25))
        assert black_probability == pytest.approx(1.0 - white_probability)


class TestKeyMoveMetrics:
    def test_black_metrics_use_mover_perspective_and_swing_uses_white(self):
        plies = parse_pgn_game(KEY_MOVE_PGN)["plies"]
        previous = {"played_eval": {"score_cp": -100}}
        record = {
            "best_move": "b8c6",
            "best_eval": {"score_cp": -100},
            "second_eval": {"score_cp": 0},
            "played_eval": {"score_cp": 100},
        }
        candidate = _build_key_move_candidate(plies[1], record, previous)
        assert candidate is not None
        assert candidate["delta_w"] == pytest.approx(0.28, abs=0.001)
        assert candidate["sharpness"] == pytest.approx(0.14, abs=0.001)
        assert candidate["swing"] == pytest.approx(0.28, abs=0.001)
        assert candidate["nnue_eval_change_cp"] == 200

    def test_exact_trigger_thresholds_do_not_qualify(self):
        assert [
            item["type"] for item in _key_move_trigger_candidates(0.25, None, None)
        ] == ["missed_opportunity"]
        assert not _key_move_trigger_candidates(0.12, None, None)
        assert not _key_move_trigger_candidates(0.019, 0.30, None)
        assert not _key_move_trigger_candidates(0.0, None, 0.20)
        assert (
            _key_move_trigger_candidates(0.019, 0.301, None)[0]["type"]
            == "brilliant_only_move"
        )

    def test_overlapping_triggers_choose_highest_priority_and_tie_order(self):
        candidates = _key_move_trigger_candidates(0.30, 0.0, 0.50)
        assert candidates == [
            {"type": "major_blunder_turning_point", "priority": 0.6},
            {"type": "missed_opportunity", "priority": 0.3},
            {"type": "evaluation_swing", "priority": 0.6},
        ]
        plies = parse_pgn_game(KEY_MOVE_PGN)["plies"]
        record = {
            "best_move": "e2e4",
            "best_eval": {"score_cp": 100},
            "played_eval": {"score_cp": -100},
        }
        previous = {"played_eval": {"score_cp": 100}}
        candidate = _build_key_move_candidate(plies[0], record, previous)
        assert candidate is not None
        assert candidate["type"] == "major_blunder_turning_point"
        tied = [
            {"type": "evaluation_swing", "priority": 0.6},
            {"type": "major_blunder_turning_point", "priority": 0.6},
        ]
        assert _select_key_move_primary_trigger(tied) == tied[1]

    def test_candidate_contains_tags_nnue_and_optional_hce_deltas(self):
        ply = parse_pgn_game(KEY_MOVE_PGN)["plies"][0]
        record = {
            "best_move": "e2e4",
            "best_eval": {"score_cp": 100},
            "played_eval": {"score_cp": -100},
            "hce": {
                "baseline": {"total": 0.1, "Pawns": 0.0},
                "played": {"total": 0.3, "Pawns": 0.2},
                "best": {"total": 0.2, "Pawns": 0.4},
            },
        }
        candidate = _build_key_move_candidate(ply, record, None)
        assert candidate is not None
        assert candidate["move"] == "e4"
        assert candidate["analysis_move"] == "e2e4"
        assert candidate["tags"] == ["positional_shift"]
        assert candidate["hce_deltas"] == {"total": 0.2, "Pawns": 0.2}
        assert candidate["missed_hce_deltas"] == {"total": 0.1, "Pawns": -0.2}
        assert candidate["nnue_eval_change_cp"] == 200
        assert "swing" not in candidate

    def test_mate_evaluations_omit_centipawn_change(self):
        ply = parse_pgn_game(KEY_MOVE_PGN)["plies"][0]
        record = {
            "best_move": "e2e4",
            "best_eval": {"mate_in": 3},
            "played_eval": {"mate_in": -2},
        }
        candidate = _build_key_move_candidate(ply, record, None)
        assert candidate is not None
        assert candidate["best_eval"] == {"mate_in": 3}
        assert "nnue_eval_change_cp" not in candidate


class TestKeyMoveSelection:
    def _candidate(self, ply, priority, phase, positional=False):
        candidate = {"ply": ply, "_priority": priority, "_phase": phase}
        if positional:
            candidate["tags"] = ["positional_shift"]
        return candidate

    def test_budget_scales_with_full_moves_and_has_one_moment_minimum(self):
        assert _key_move_selection_budget(0) == 0
        assert _key_move_selection_budget(1) == 1
        assert _key_move_selection_budget(40) == 8
        assert _key_move_selection_budget(80) == 15

    def test_classifies_opening_middlegame_and_endgame(self):
        assert _key_move_phase(chess.Board()) == "opening"
        queen_position = chess.Board("8/4k3/8/3q4/3Q4/8/4K3/8 w - - 0 1")
        assert _key_move_phase(queen_position) == "middlegame"
        pawn_endgame = chess.Board("8/4k3/8/4P3/8/8/4K3/8 w - - 0 1")
        assert _key_move_phase(pawn_endgame) == "endgame"

    def test_reserves_best_positional_candidate_per_phase_with_budget_limit(self):
        candidates = [
            self._candidate(5, 4.0, "opening", positional=True),
            self._candidate(2, 3.0, "middlegame", positional=True),
            self._candidate(1, 2.0, "endgame", positional=True),
            self._candidate(3, 10.0, "opening"),
        ]
        selected = _select_key_move_candidates(candidates, ply_count=6)
        assert [candidate["ply"] for candidate in selected] == [2, 5]
        assert all(
            "_priority" not in candidate and "_phase" not in candidate
            for candidate in selected
        )

    def test_priority_fill_is_chronological_and_sparse_candidates_stay_sparse(self):
        candidates = [
            self._candidate(5, 3.0, "opening", positional=True),
            self._candidate(1, 10.0, "opening"),
            self._candidate(8, 7.0, "opening"),
        ]
        selected = _select_key_move_candidates(candidates, ply_count=6)
        assert [candidate["ply"] for candidate in selected] == [1, 5]
        assert len(_select_key_move_candidates(candidates[:1], ply_count=80)) == 1


class TestStockfishHceBatch:
    @pytest.mark.asyncio
    async def test_hce_batch_disables_nnue_and_preserves_order(self):
        process = _make_mock_process(
            [
                "Final evaluation +0.10 (white side)",
                "readyok",
                "Final evaluation -0.20 (white side)",
                "readyok",
            ]
        )
        with patch("asyncio.create_subprocess_exec", return_value=process):
            engine = StockfishEngine(path="/fake/stockfish")
            results = await engine.run_hce_batch(
                [
                    f"position fen {START_FEN}",
                    f"position fen {E4_FEN}",
                ]
            )
        scores = [
            parse_eval_output(result)["total_evaluation"]["score"] for result in results
        ]
        assert scores == [0.10, -0.20]
        written = b"".join(call.args[0] for call in process.stdin.write.call_args_list)
        assert b"setoption name Use NNUE value false\n" in written
        assert written.count(b"eval\n") == 2


class TestReplacementTools:
    @pytest.mark.asyncio
    async def test_analyze_position_ranks_and_associates_hce(self):
        from fastmcp.client import Client

        moves = [chess.Move.from_uci("b8c6"), chess.Move.from_uci("g8f6")]
        long_pv = [
            chess.Move.from_uci(u)
            for u in ["b8c6", "g1f3", "g8f6", "b1c3", "e7e6", "d2d4", "f8b4", "f1d3"]
        ]
        engine = MagicMock()
        engine.configure = AsyncMock()
        engine.quit = AsyncMock()
        engine.analyse = AsyncMock(
            return_value=[
                {
                    "score": chess.engine.PovScore(chess.engine.Cp(35), chess.BLACK),
                    "pv": long_pv,
                },
                {
                    "score": chess.engine.PovScore(chess.engine.Cp(10), chess.BLACK),
                    "pv": [moves[1]],
                },
            ]
        )

        async def hce_batch(commands):
            assert commands == [
                f"position fen {E4_FEN}",
                f"position fen {E4_FEN} moves b8c6",
                f"position fen {E4_FEN} moves g8f6",
            ]
            return [
                "Final evaluation +0.10",
                "Final evaluation +0.20",
                "Final evaluation +0.30",
            ]

        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock(side_effect=hce_batch)
            ),
        ):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "analyze_position", {"fen": E4_FEN, "candidate_count": 2}
                )
        data = _extract_tool_data(result)
        assert [item["move"] for item in data["candidates"]] == ["b8c6", "g8f6"]
        assert [item["score_cp"] for item in data["candidates"]] == [35, 10]
        assert len(data["candidates"][0]["pv"]) == 6
        assert data["baseline_hce"]["total"] == 0.10
        assert [item["hce_deltas"]["total"] for item in data["candidates"]] == [
            0.10,
            0.20,
        ]
        assert data["score_perspective"] == "side to move"
        engine.configure.assert_awaited_once_with({"Use NNUE": True})
        engine.quit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_analyze_position_defaults_to_one_and_handles_terminal_position(self):
        from fastmcp.client import Client

        engine = MagicMock()
        engine.configure = AsyncMock()
        engine.quit = AsyncMock()
        engine.analyse = AsyncMock(
            return_value=[
                {
                    "score": chess.engine.PovScore(chess.engine.Cp(25), chess.WHITE),
                    "pv": [chess.Move.from_uci("e2e4")],
                }
            ]
        )
        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock(return_value=["", ""])
            ) as hce,
        ):
            async with Client(mcp) as client:
                result = await client.call_tool("analyze_position", {"fen": START_FEN})
        assert len(_extract_tool_data(result)["candidates"]) == 1
        assert engine.analyse.await_args.kwargs["multipv"] == 1
        hce.assert_awaited_once()

        terminal_fen = "7k/6Q1/5K2/8/8/8/8/8 b - - 0 1"
        with (
            patch("chess.engine.popen_uci") as popen,
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock(return_value=[""])
            ) as hce,
        ):
            async with Client(mcp) as client:
                terminal = await client.call_tool(
                    "analyze_position", {"fen": terminal_fen}
                )
        assert _extract_tool_data(terminal)["candidates"] == []
        popen.assert_not_called()
        hce.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_analyze_position_rejects_bad_fen_and_candidate_count(self):
        from fastmcp.client import Client

        async with Client(mcp) as client:
            with pytest.raises(Exception, match="six fields"):
                await client.call_tool("analyze_position", {"fen": "not a FEN"})
            with pytest.raises(Exception, match="positive integer"):
                await client.call_tool(
                    "analyze_position", {"fen": START_FEN, "candidate_count": 0}
                )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fen,move,perspective,score",
        [(START_FEN, "e2e4", chess.WHITE, 45), (E4_FEN, "e7e5", chess.BLACK, -30)],
    )
    async def test_analyze_move_forces_root_and_preserves_perspective(
        self, fen, move, perspective, score
    ):
        from fastmcp.client import Client

        move_object = chess.Move.from_uci(move)
        long_pv = [chess.Move.from_uci(move)] * 8
        engine = MagicMock()
        engine.configure = AsyncMock()
        engine.quit = AsyncMock()
        engine.analyse = AsyncMock(
            return_value={
                "score": chess.engine.PovScore(chess.engine.Cp(score), perspective),
                "pv": long_pv,
            }
        )

        async def hce_batch(commands):
            assert commands == [
                f"position fen {fen}",
                f"position fen {fen} moves {move}",
            ]
            return [
                "Final evaluation +0.10 (white side)",
                "Final evaluation +0.15 (white side)",
            ]

        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock(side_effect=hce_batch)
            ),
        ):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "analyze_move", {"fen": fen, "move_uci": move}
                )
        data = _extract_tool_data(result)
        assert data["score_cp"] == score
        assert len(data["pv"]) == 6
        assert data["score_perspective"] == "side to move before the move"
        assert data["baseline_hce"]["total"] == 0.10
        assert data["hce_deltas"]["total"] == 0.05
        assert engine.analyse.await_args.kwargs["root_moves"] == [move_object]
        engine.configure.assert_awaited_once_with({"Use NNUE": True})

    @pytest.mark.asyncio
    async def test_analyze_move_rejects_invalid_and_illegal_moves(self):
        from fastmcp.client import Client

        with patch("chess.engine.popen_uci") as popen:
            async with Client(mcp) as client:
                with pytest.raises(Exception, match="Invalid UCI"):
                    await client.call_tool(
                        "analyze_move", {"fen": START_FEN, "move_uci": "not-uci"}
                    )
                with pytest.raises(Exception, match="Illegal move"):
                    await client.call_tool(
                        "analyze_move", {"fen": START_FEN, "move_uci": "e2e5"}
                    )
        popen.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "number,color,ply_index",
        [(1, "white", 0), (1, "black", 1)],
    )
    async def test_analyze_position_selects_pgn_pre_move_position(
        self, number, color, ply_index
    ):
        from fastmcp.client import Client

        ply = parse_pgn_game(SAMPLE_PGN)["plies"][ply_index]
        engine = _mock_engine(constant_lines=[_nnue_line(score_cp=25, uci=ply["uci"])])
        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine,
                "run_hce_batch",
                new=AsyncMock(return_value=["Final evaluation +0.10"] * 2),
            ),
        ):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "analyze_position",
                    {
                        "pgn": SAMPLE_PGN,
                        "move_number": number,
                        "color": color,
                    },
                )

        data = _extract_tool_data(result)
        assert data["fen"] == ply["before_fen"]
        assert data["candidates"][0]["move"] == ply["uci"]
        assert engine.analyse.await_args.args[0].fen() == ply["before_fen"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "number,color,ply_index",
        [(1, "white", 0), (1, "black", 1)],
    )
    async def test_analyze_move_infers_pgn_move_and_pre_move_fen(
        self, number, color, ply_index
    ):
        from fastmcp.client import Client

        ply = parse_pgn_game(SAMPLE_PGN)["plies"][ply_index]
        engine = MagicMock()
        engine.configure = AsyncMock()
        engine.quit = AsyncMock()
        engine.analyse = AsyncMock(return_value=_nnue_line(score_cp=25, uci=ply["uci"]))

        async def hce_batch(commands):
            assert commands == [
                f"position fen {ply['before_fen']}",
                f"position fen {ply['before_fen']} moves {ply['uci']}",
            ]
            return ["Final evaluation +0.10", "Final evaluation +0.15"]

        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock(side_effect=hce_batch)
            ),
        ):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "analyze_move",
                    {
                        "pgn": SAMPLE_PGN,
                        "move_number": number,
                        "color": color,
                    },
                )

        data = _extract_tool_data(result)
        assert data["fen"] == ply["before_fen"]
        assert data["move"] == ply["uci"]
        assert engine.analyse.await_args.args[0].fen() == ply["before_fen"]
        assert engine.analyse.await_args.kwargs["root_moves"] == [
            chess.Move.from_uci(ply["uci"])
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", ["analyze_position", "analyze_move"])
    async def test_analysis_tools_reject_out_of_range_pgn_selection_before_engine(
        self, tool_name
    ):
        from fastmcp.client import Client

        with (
            patch("chess.engine.popen_uci") as popen,
            patch.object(StockfishEngine, "run_hce_batch", new=AsyncMock()) as hce,
        ):
            async with Client(mcp) as client:
                with pytest.raises(Exception, match="No white move 3"):
                    await client.call_tool(
                        tool_name,
                        {
                            "pgn": SAMPLE_PGN,
                            "move_number": 3,
                            "color": "white",
                        },
                    )
        popen.assert_not_called()
        hce.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "number,color,expected",
        [
            (1, "white", START_FEN),
            (1, "black", E4_FEN),
            (
                2,
                "white",
                "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
            ),
        ],
    )
    async def test_fen_from_pgn_uses_pre_move_convention(self, number, color, expected):
        from fastmcp.client import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                "fen_from_pgn",
                {"pgn": SAMPLE_PGN, "move_number": number, "color": color},
            )
        assert _extract_tool_data(result)["fen"] == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "pgn,number,color,message",
        [
            ("not a PGN", 1, "white", "does not contain any moves"),
            ('[Event "Illegal"]\n\n1. e5 *', 1, "white", "Invalid PGN"),
            (SAMPLE_PGN + SAMPLE_PGN, 1, "white", "exactly one game"),
            (SAMPLE_PGN, 3, "white", "No white move 3"),
        ],
    )
    async def test_fen_from_pgn_rejects_invalid_selection(
        self, pgn, number, color, message
    ):
        from fastmcp.client import Client

        async with Client(mcp) as client:
            with pytest.raises(Exception, match=message):
                await client.call_tool(
                    "fen_from_pgn", {"pgn": pgn, "move_number": number, "color": color}
                )


class TestAnalyzeGame:
    @pytest.mark.asyncio
    async def test_returns_chronological_array_omitting_best_fields(self):
        from fastmcp.client import Client

        pgn = '[Result "*"]\n\n1. e4 e5 *'
        parsed = parse_pgn_game(pgn)
        start_fen = parsed["plies"][0]["before_fen"]
        after_e4_fen = parsed["plies"][0]["after_fen"]
        after_e5_fen = parsed["plies"][1]["after_fen"]
        engine = _mock_engine(
            lines_by_fen={
                start_fen: [
                    _nnue_line(score_cp=0, uci="e2e4"),
                    _nnue_line(score_cp=-10, uci="d2d4"),
                ],
                after_e4_fen: [
                    _nnue_line(score_cp=0, uci="e7e5"),
                    _nnue_line(score_cp=-10, uci="c7c5"),
                ],
                after_e5_fen: [
                    _nnue_line(score_cp=0, uci="g1f3"),
                    _nnue_line(score_cp=-10, uci="b1c3"),
                ],
            }
        )

        async def hce_batch(commands):
            assert commands == [
                f"position fen {start_fen}",
                f"position fen {after_e4_fen}",
                f"position fen {after_e5_fen}",
            ]
            return [
                "Final evaluation +0.10 (white side)",
                "Final evaluation +0.30 (white side)",
                "Final evaluation +0.00 (white side)",
            ]

        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock(side_effect=hce_batch)
            ),
        ):
            async with Client(mcp) as client:
                result = await client.call_tool("analyze_game", {"pgn": pgn})

        data = _extract_tool_data(result)
        assert [element["ply"] for element in data] == [1, 2]
        assert [element["move"] for element in data] == ["e4", "e5"]
        assert data[0]["hce"] == {"total": 0.2}
        assert data[1]["hce"] == {"total": -0.3}
        for element in data:
            assert "best" not in element
            assert "cpl" not in element
            assert "missed" not in element

    @pytest.mark.asyncio
    async def test_black_ply_reports_positive_mover_relative_loss(self):
        from fastmcp.client import Client

        pgn = '[Result "*"]\n\n1. e4 e5 *'
        parsed = parse_pgn_game(pgn)
        start_fen = parsed["plies"][0]["before_fen"]
        after_e4_fen = parsed["plies"][0]["after_fen"]
        after_e5_fen = parsed["plies"][1]["after_fen"]
        engine = _mock_engine(
            lines_by_fen={
                start_fen: [
                    _nnue_line(score_cp=30, uci="e2e4"),
                    _nnue_line(score_cp=20, uci="d2d4"),
                ],
                after_e4_fen: [
                    _nnue_line(score_cp=-100, uci="c7c5"),
                    _nnue_line(score_cp=-50, uci="e7e5"),
                ],
                after_e5_fen: [
                    _nnue_line(score_cp=-50, uci="g1f3"),
                    _nnue_line(score_cp=-60, uci="b1c3"),
                ],
            }
        )
        best_board = chess.Board(after_e4_fen)
        best_board.push(chess.Move.from_uci("c7c5"))
        best_black_fen = best_board.fen()
        captured = {}

        async def hce_batch(commands):
            captured["commands"] = commands
            return ["Final evaluation +0.10 (white side)"] * len(commands)

        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock(side_effect=hce_batch)
            ),
        ):
            async with Client(mcp) as client:
                result = await client.call_tool("analyze_game", {"pgn": pgn})

        data = _extract_tool_data(result)
        assert data[1]["best"] == "c7c5"
        assert data[1]["cpl"] == 50
        assert "missed" in data[1]
        assert f"position fen {best_black_fen}" in captured["commands"]

    @pytest.mark.asyncio
    async def test_scores_game_ending_move_from_terminal_result(self):
        from fastmcp.client import Client

        pgn = '[Result "0-1"]\n\n1. f3 e5 2. g4 Qh4# 0-1\n'
        parsed = parse_pgn_game(pgn)
        after_f3 = parsed["plies"][0]["after_fen"]
        after_e5 = parsed["plies"][1]["after_fen"]
        after_g4 = parsed["plies"][2]["after_fen"]
        engine = _mock_engine(
            lines_by_fen={
                parsed["plies"][0]["before_fen"]: [
                    _nnue_line(score_cp=0, uci="f2f3"),
                    _nnue_line(score_cp=-5, uci="e2e4"),
                ],
                after_f3: [
                    _nnue_line(score_cp=0, uci="e7e5"),
                    _nnue_line(score_cp=-5, uci="e7e6"),
                ],
                after_e5: [
                    _nnue_line(score_cp=0, uci="g2g4"),
                    _nnue_line(score_cp=-5, uci="g1f3"),
                ],
                after_g4: [
                    _nnue_line(mate_in=-1, uci="d8h4"),
                    _nnue_line(score_cp=-800, uci="a7a6"),
                ],
            }
        )

        async def hce_batch(commands):
            return ["Final evaluation +0.10 (white side)"] * len(commands)

        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock(side_effect=hce_batch)
            ),
        ):
            async with Client(mcp) as client:
                result = await client.call_tool("analyze_game", {"pgn": pgn})

        data = _extract_tool_data(result)
        assert len(data) == 4
        last = data[3]
        assert last["ply"] == 4
        assert last["move"] == "Qh4#"
        assert "hce" in last
        assert "best" not in last
        assert "cpl" not in last
        assert "missed" not in last

    @pytest.mark.asyncio
    async def test_default_and_custom_depth_reach_every_analysis(self):
        from fastmcp.client import Client

        pgn = '[Result "*"]\n\n1. d4 *'
        for requested, expected in ((None, 12), (4, 4)):
            engine = _qualifying_engine(pgn)
            arguments: dict[str, object] = {"pgn": pgn}
            if requested is not None:
                arguments["depth"] = requested
            with (
                patch(
                    "chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))
                ),
                patch.object(
                    StockfishEngine,
                    "run_hce_batch",
                    new=AsyncMock(return_value=[""] * 3),
                ),
            ):
                async with Client(mcp) as client:
                    await client.call_tool("analyze_game", arguments)
            depths = [call.args[1].depth for call in engine.analyse.call_args_list]
            assert depths and set(depths) == {expected}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("depth", [0, -3, True])
    async def test_rejects_invalid_depth_before_any_engine_call(self, depth):
        from fastmcp.client import Client
        from fastmcp.exceptions import ToolError

        pgn = '[Result "*"]\n\n1. d4 *'
        with (
            patch("chess.engine.popen_uci") as popen,
            patch.object(StockfishEngine, "run_hce_batch", new=AsyncMock()) as hce,
        ):
            async with Client(mcp) as client:
                with pytest.raises(ToolError):
                    await client.call_tool("analyze_game", {"pgn": pgn, "depth": depth})
        popen.assert_not_called()
        hce.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "pgn",
        [
            "",
            "not a PGN",
            SAMPLE_PGN + SAMPLE_PGN,
            '[Event "Illegal"]\n\n1. e5 *\n',
        ],
    )
    async def test_rejects_invalid_pgn_without_running_the_engine(self, pgn):
        from fastmcp.client import Client
        from fastmcp.exceptions import ToolError

        with (
            patch("chess.engine.popen_uci") as popen,
            patch.object(StockfishEngine, "run_hce_batch", new=AsyncMock()) as hce,
        ):
            async with Client(mcp) as client:
                with pytest.raises(ToolError):
                    await client.call_tool("analyze_game", {"pgn": pgn})
        popen.assert_not_called()
        hce.assert_not_awaited()


class TestMcpToolContracts:
    @pytest.mark.asyncio
    async def test_mcp_exposes_exact_five_tools_and_rejects_removed_names(self):
        from fastmcp.client import Client
        from fastmcp.exceptions import ToolError

        expected = {
            "analyze_position",
            "analyze_move",
            "analyze_game",
            "identify_key_moves",
            "fen_from_pgn",
        }
        removed = {
            "get_position_features",
            "compare_candidate_moves",
            "batch_evaluate_positions",
            "annotate_game",
            "annotate_game_pgn",
            "search_best_lines",
            "analyze_game_timeline",
        }
        async with Client(mcp) as client:
            assert {tool.name for tool in await client.list_tools()} == expected
            for name in removed:
                with pytest.raises(ToolError):
                    await client.call_tool(name, {})

    @pytest.mark.asyncio
    async def test_replacement_tool_schemas(self):
        from fastmcp.client import Client

        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        requirements = {
            "analyze_position": set(),
            "analyze_move": set(),
            "analyze_game": {"pgn"},
            "identify_key_moves": {"pgn"},
            "fen_from_pgn": {"pgn", "move_number", "color"},
        }
        for name, required in requirements.items():
            tool = tools[name]
            schema = (
                tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
            )
            assert required <= set(schema.get("required", []))
        position_schema = (
            tools["analyze_position"].input_schema
            if hasattr(tools["analyze_position"], "input_schema")
            else tools["analyze_position"].inputSchema
        )
        move_schema = (
            tools["analyze_move"].input_schema
            if hasattr(tools["analyze_move"], "input_schema")
            else tools["analyze_move"].inputSchema
        )
        assert set(position_schema["properties"]) >= {
            "fen",
            "candidate_count",
            "pgn",
            "move_number",
            "color",
        }
        assert set(move_schema["properties"]) >= {
            "fen",
            "move_uci",
            "pgn",
            "move_number",
            "color",
        }
        assert position_schema.get("required", []) == []
        assert move_schema.get("required", []) == []
        key_move_schema = (
            tools["identify_key_moves"].input_schema
            if hasattr(tools["identify_key_moves"], "input_schema")
            else tools["identify_key_moves"].inputSchema
        )
        assert set(key_move_schema["required"]) == {"pgn"}
        assert "multipv" not in key_move_schema["properties"]
        assert "depth" in key_move_schema["properties"]
        assert key_move_schema["properties"]["depth"]["type"] in {"integer", "number"}
        assert key_move_schema["properties"]["include_hce"]["type"] == "boolean"
        game_schema = (
            tools["analyze_game"].input_schema
            if hasattr(tools["analyze_game"], "input_schema")
            else tools["analyze_game"].inputSchema
        )
        assert set(game_schema["required"]) == {"pgn"}
        assert game_schema["properties"]["depth"]["type"] in {"integer", "number"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name,payload,message",
        [
            ("analyze_position", {}, "Provide either a FEN or a PGN"),
            (
                "analyze_position",
                {"pgn": SAMPLE_PGN},
                "requires both move_number and color",
            ),
            (
                "analyze_position",
                {
                    "fen": START_FEN,
                    "pgn": SAMPLE_PGN,
                    "move_number": 1,
                    "color": "white",
                },
                "cannot be combined",
            ),
            ("analyze_move", {}, "Provide either a FEN or a PGN"),
            ("analyze_move", {"fen": START_FEN}, "requires move_uci"),
            (
                "analyze_move",
                {"pgn": SAMPLE_PGN},
                "requires both move_number and color",
            ),
            (
                "analyze_move",
                {"fen": START_FEN, "move_uci": "e2e4", "pgn": SAMPLE_PGN},
                "cannot be combined",
            ),
            (
                "analyze_move",
                {
                    "pgn": SAMPLE_PGN,
                    "move_number": 1,
                    "color": "white",
                    "move_uci": "e2e4",
                },
                "move_uci with PGN input",
            ),
        ],
    )
    async def test_analysis_tools_reject_incomplete_or_mixed_inputs_without_engine(
        self, tool_name, payload, message
    ):
        from fastmcp.client import Client

        with (
            patch("chess.engine.popen_uci") as popen,
            patch.object(StockfishEngine, "run_hce_batch", new=AsyncMock()) as hce,
        ):
            async with Client(mcp) as client:
                with pytest.raises(Exception, match=message):
                    await client.call_tool(tool_name, payload)
        popen.assert_not_called()
        hce.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_array_and_selects_with_computed_evidence(self):
        from fastmcp.client import Client

        pgn = '[Result "*"]\n\n1. d4 *'
        engine = _qualifying_engine(pgn)
        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock(return_value=[""] * 3)
            ),
        ):
            async with Client(mcp) as client:
                result = await client.call_tool("identify_key_moves", {"pgn": pgn})
        data = _extract_tool_data(result)
        assert isinstance(data, list)
        assert len(data) == 1
        assert {
            "ply",
            "move",
            "analysis_move",
            "type",
            "priority",
            "delta_w",
            "best_eval",
            "played_eval",
        } <= set(data[0])
        assert data[0]["move"] == "d4"
        assert data[0]["analysis_move"] == "e2e4"
        assert data[0]["type"] == "major_blunder_turning_point"

    @pytest.mark.asyncio
    async def test_include_hce_false_skips_hce_and_omits_both_delta_fields(self):
        from fastmcp.client import Client

        pgn = '[Result "*"]\n\n1. d4 *'
        engine = _qualifying_engine(pgn)
        with (
            patch("chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))),
            patch.object(
                StockfishEngine, "run_hce_batch", new=AsyncMock()
            ) as hce_batch,
        ):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "identify_key_moves", {"pgn": pgn, "include_hce": False}
                )
        data = _extract_tool_data(result)
        assert len(data) == 1
        assert "hce_deltas" not in data[0]
        assert "missed_hce_deltas" not in data[0]
        hce_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_and_custom_depth_reach_every_analysis(self):
        from fastmcp.client import Client

        pgn = '[Result "*"]\n\n1. d4 *'
        for requested, expected in ((None, 12), (4, 4)):
            engine = _qualifying_engine(pgn)
            arguments: dict[str, object] = {"pgn": pgn}
            if requested is not None:
                arguments["depth"] = requested
            with (
                patch(
                    "chess.engine.popen_uci", new=AsyncMock(return_value=(None, engine))
                ),
                patch.object(
                    StockfishEngine,
                    "run_hce_batch",
                    new=AsyncMock(return_value=[""] * 3),
                ),
            ):
                async with Client(mcp) as client:
                    await client.call_tool("identify_key_moves", arguments)
            depths = [call.args[1].depth for call in engine.analyse.call_args_list]
            assert depths and set(depths) == {expected}

    @pytest.mark.asyncio
    async def test_invalid_pgn_raises_without_running_stockfish(self):
        from fastmcp.client import Client
        from fastmcp.exceptions import ToolError

        with patch("chess.engine.popen_uci") as popen:
            async with Client(mcp) as client:
                with pytest.raises(ToolError):
                    await client.call_tool("identify_key_moves", {"pgn": ""})
        popen.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "arguments",
        [
            {"pgn": KEY_MOVE_PGN, "depth": 0},
            {"pgn": KEY_MOVE_PGN, "depth": -3},
            {"pgn": KEY_MOVE_PGN, "depth": True},
            {"pgn": KEY_MOVE_PGN, "include_hce": "yes"},
        ],
    )
    async def test_rejects_invalid_controls_before_any_engine_call(self, arguments):
        from fastmcp.client import Client
        from fastmcp.exceptions import ToolError

        with patch("chess.engine.popen_uci") as popen:
            async with Client(mcp) as client:
                with pytest.raises(ToolError):
                    await client.call_tool("identify_key_moves", arguments)
        popen.assert_not_called()


class TestMcpIntegration:
    @needs_stockfish
    @pytest.mark.asyncio
    async def test_replacement_tools_with_real_stockfish(self):
        from fastmcp.client import Client

        assert STOCKFISH_BIN is not None
        original_path = engine.sf.path
        engine.sf.path = STOCKFISH_BIN
        try:
            async with Client(mcp) as client:
                position = _extract_tool_data(
                    await client.call_tool(
                        "analyze_position", {"fen": START_FEN, "candidate_count": 2}
                    )
                )
                move = _extract_tool_data(
                    await client.call_tool(
                        "analyze_move", {"fen": START_FEN, "move_uci": "e2e4"}
                    )
                )
            assert len(position["candidates"]) == 2
            assert "total" in position["baseline_hce"]
            assert "total" in position["candidates"][0]["hce_deltas"]
            assert "score_cp" in position["candidates"][0]
            assert len(position["candidates"][0]["pv"]) <= 6
            assert "total" in move["baseline_hce"]
            assert "total" in move["hce_deltas"]
            assert set(move["hce_deltas"]) - {"total"}
            assert "score_cp" in move
            assert len(move["pv"]) <= 6
        finally:
            engine.sf.path = original_path

    @needs_stockfish
    @pytest.mark.asyncio
    async def test_identify_key_moves_computes_evidence_with_real_stockfish(self):
        from fastmcp.client import Client

        pgn = '[Result "*"]\n\n1. e4 e5 2. Qh5 Nc6 3. Qxf7+ Kxf7 *'
        assert STOCKFISH_BIN is not None
        original_path = engine.sf.path
        engine.sf.path = STOCKFISH_BIN
        try:
            async with Client(mcp) as client:
                selected = _extract_tool_data(
                    await client.call_tool("identify_key_moves", {"pgn": pgn})
                )
        finally:
            engine.sf.path = original_path

        assert isinstance(selected, list)
        assert selected, "expected at least one selected key moment"
        assert any(item["ply"] == 5 for item in selected)
        for item in selected:
            assert {
                "ply",
                "move",
                "analysis_move",
                "type",
                "priority",
                "delta_w",
                "best_eval",
                "played_eval",
            } <= set(item)
            assert "hce_deltas" in item
            assert "missed_hce_deltas" in item


class TestMcpStdio:
    @pytest.mark.asyncio
    async def test_tools_list_is_exact_and_removed_names_fail(self):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "chess_lens_mcp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        try:
            init = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
            process.stdin.write(json.dumps(init).encode() + b"\n")
            await process.stdin.drain()
            assert json.loads(await asyncio.wait_for(process.stdout.readline(), 10))[
                "result"
            ]
            process.stdin.write(
                json.dumps(
                    {"jsonrpc": "2.0", "method": "notifications/initialized"}
                ).encode()
                + b"\n"
            )
            request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            process.stdin.write(json.dumps(request).encode() + b"\n")
            await process.stdin.drain()
            listed = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
            names = {tool["name"] for tool in listed["result"]["tools"]}
            assert names == {
                "analyze_position",
                "analyze_move",
                "analyze_game",
                "identify_key_moves",
                "fen_from_pgn",
            }

            for request_id, name in enumerate(
                (
                    "get_position_features",
                    "compare_candidate_moves",
                    "batch_evaluate_positions",
                    "annotate_game",
                    "annotate_game_pgn",
                    "search_best_lines",
                    "analyze_game_timeline",
                ),
                start=3,
            ):
                call = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {}},
                }
                process.stdin.write(json.dumps(call).encode() + b"\n")
                await process.stdin.drain()
                response = json.loads(
                    await asyncio.wait_for(process.stdout.readline(), 10)
                )
                assert (
                    "error" in response
                    or response.get("result", {}).get("isError") is True
                )
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
