"""Thin launcher for the shared interactive CLI app."""

from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console

from core.systems.runtime.cli_support import InteractiveCliApp
from core.systems.runtime.entrypoints import ensure_utf8_stdio

ensure_utf8_stdio()


def main() -> None:
    parser = argparse.ArgumentParser(description="PyBot interactive CLI")
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        default=None,
        help="Resume a previous session by ID (or 'latest')",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="One-shot prompt (skip interactive mode)",
    )
    args = parser.parse_args()

    console = Console(force_terminal=(args.output_format == "text"))
    app = InteractiveCliApp(console=console)

    if args.resume:
        thread_id = args.resume
        if thread_id == "latest":
            from core.systems.runtime.config_impl import get_config
            cfg = get_config()
            thread_id = cfg.get("agent_config", {}).get("thread_id", "default_session")
        app.thread_id = thread_id

    if args.prompt:
        prompt_text = " ".join(args.prompt)
        try:
            app.initialize_agent()
        except Exception as exc:
            if args.output_format == "json":
                print(json.dumps({"error": str(exc)}))
            else:
                console.print(f"[bold red]Error:[/bold red] {exc}")
            sys.exit(1)

        bot = app._require_bot()
        response = bot.invoke(prompt_text)
        if args.output_format == "json":
            print(json.dumps({
                "thread_id": app.thread_id,
                "prompt": prompt_text,
                "response": response,
            }, ensure_ascii=False, indent=2))
        else:
            from rich.markdown import Markdown
            console.print(Markdown(response))
        return

    app.run()


if __name__ == "__main__":
    main()
