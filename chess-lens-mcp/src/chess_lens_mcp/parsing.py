"""Parsing and validation helpers for chess positions and PGN games."""

import io
from typing import Any

import chess
import chess.pgn


def _read_single_pgn_game(pgn_text: str) -> chess.pgn.Game:
    if not pgn_text.strip():
        raise ValueError("PGN input is empty.")

    stream = io.StringIO(pgn_text)
    try:
        game = chess.pgn.read_game(stream)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Unable to parse PGN: {exc}") from exc

    if game is None:
        raise ValueError("PGN input does not contain a game.")
    if game.errors:
        raise ValueError(f"Invalid PGN: {game.errors[0]}")
    if not any(game.mainline_moves()):
        raise ValueError("PGN input does not contain any moves.")
    if chess.pgn.read_game(stream) is not None:
        raise ValueError("PGN input must contain exactly one game.")
    return game


def parse_pgn_game(pgn_text: str) -> dict[str, Any]:
    """Parse exactly one legal PGN game into chronological ply records."""
    game = _read_single_pgn_game(pgn_text)

    board = game.board()
    plies: list[dict[str, Any]] = []
    for move in game.mainline_moves():
        before_fen = board.fen()
        plies.append(
            {
                "ply": len(plies) + 1,
                "move_number": board.fullmove_number,
                "mover": "white" if board.turn == chess.WHITE else "black",
                "san": board.san(move),
                "uci": move.uci(),
                "before_fen": before_fen,
                "after_fen": None,
            }
        )
        board.push(move)
        plies[-1]["after_fen"] = board.fen()

    return {
        "headers": dict(game.headers),
        "result": game.headers.get("Result", "*"),
        "plies": plies,
    }


def _validated_board(fen: str) -> chess.Board:
    if not isinstance(fen, str) or len(fen.split()) != 6:
        raise ValueError("FEN must contain exactly six fields.")
    try:
        board = chess.Board(fen)
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Invalid FEN: {exc}") from exc
    if not board.is_valid():
        raise ValueError("Invalid FEN: position is not legal.")
    return board
