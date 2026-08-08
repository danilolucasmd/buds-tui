"""Command line entry point."""

from __future__ import annotations

import argparse

from .app import BudsApp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="buds-tui", description="Terminal UI for Samsung Galaxy Buds."
    )
    parser.add_argument(
        "-a",
        "--address",
        help="Bluetooth address of the earbuds (default: the first connected pair)",
    )
    args = parser.parse_args()
    BudsApp(address=args.address).run()


if __name__ == "__main__":
    main()
