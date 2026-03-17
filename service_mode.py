"""Thin launcher for the PyBot web service."""

from __future__ import annotations

from web.app import create_app, main

app = create_app()


if __name__ == "__main__":
    main()
