"""FastAPI dependencies shared across routers."""

from __future__ import annotations

from fastapi import Request

from .state import WebServices


def get_services(request: Request) -> WebServices:
    return request.app.state.services
