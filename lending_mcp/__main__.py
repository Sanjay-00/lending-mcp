import logging
import sys


def main() -> None:
    # stdout carries the MCP protocol: every log goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from lending_mcp.server import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
