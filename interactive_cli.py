"""Thin launcher for the shared interactive CLI app."""

from __future__ import annotations

from rich.console import Console

from core.cli_support import InteractiveCliApp
from core.entrypoints import ensure_utf8_stdio

ensure_utf8_stdio()
console = Console()


def main() -> None:
    InteractiveCliApp(console=console).run()


if __name__ == "__main__":
    main()
