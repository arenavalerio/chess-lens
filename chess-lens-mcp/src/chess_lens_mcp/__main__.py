"""Run the chess-lens MCP server over stdio.

This is the package entrypoint behind both ``python -m chess_lens_mcp`` and the
installed ``chess-lens-mcp`` console script.
"""

from chess_lens_mcp.tools import mcp


def main() -> None:
    """Serve the FastMCP tools over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
