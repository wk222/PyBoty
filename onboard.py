"""Thin launcher for the onboarding wizard."""

from __future__ import annotations

import sys

from rich.console import Console

from core.entrypoints import ensure_utf8_stdio
from core.onboarding import OnboardingWizard

ensure_utf8_stdio()
console = Console()


def main() -> None:
    OnboardingWizard(console=console).run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[yellow]向导已取消。[/yellow]")
        sys.exit(0)
