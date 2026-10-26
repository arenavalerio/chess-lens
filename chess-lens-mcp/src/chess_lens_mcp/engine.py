"""Stockfish process management for the chess analysis tools."""

import asyncio
import contextlib
import logging

logger = logging.getLogger(__name__)


class StockfishEngine:
    """Run batched classical evaluations through a Stockfish process."""

    def __init__(self, path: str = "/usr/games/stockfish") -> None:
        self.path = path

    async def run_hce_batch(self, position_commands: list[str]) -> list[str]:
        """Evaluate positions sequentially in one Stockfish process.

        Each entry in ``position_commands`` is a full ``position ...`` UCI
        command. An ``isready`` barrier associates each eval output with its
        request while reusing the process.
        """
        proc = await asyncio.create_subprocess_exec(
            self.path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # ``create_subprocess_exec`` returns populated pipes for the PIPE fds.
        assert proc.stdin is not None
        assert proc.stdout is not None
        try:
            proc.stdin.write(b"setoption name Use NNUE value false\n")
            await proc.stdin.drain()

            results: list[str] = []
            for position_command in position_commands:
                proc.stdin.write(f"{position_command}\n".encode())
                proc.stdin.write(b"eval\n")
                proc.stdin.write(b"isready\n")
                await proc.stdin.drain()

                chunk: list[str] = []
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8").strip()
                    if text == "readyok":
                        break
                    chunk.append(text)
                results.append("\n".join(chunk))
            return results
        finally:
            with contextlib.suppress(
                BrokenPipeError, ConnectionResetError, AttributeError
            ):
                proc.stdin.write(b"quit\n")
                await proc.stdin.drain()
            try:
                await proc.wait()
            except Exception:
                # Cleanup is best-effort; never mask the original failure.
                logger.debug("Stockfish process did not exit cleanly", exc_info=True)


sf = StockfishEngine()
