"""Small HTTP skill-registry test server helpers."""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass(frozen=True)
class HttpRouteResponse:
    """A deterministic response returned by the test HTTP registry server."""

    status: int = 200
    body: dict[str, object] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    honor_conditional_etag: bool = True


def _coerce_response(payload: dict[str, object] | HttpRouteResponse) -> HttpRouteResponse:
    if isinstance(payload, HttpRouteResponse):
        return payload
    return HttpRouteResponse(body=payload)


@contextmanager
def serve_skill_http(
    payloads: dict[str, dict[str, object] | HttpRouteResponse | list[dict[str, object] | HttpRouteResponse]],
    *,
    expected_auth: str = "",
    etags: dict[str, str] | None = None,
    request_log: list[dict[str, object]] | None = None,
    post_routes: dict[str, dict[str, object] | HttpRouteResponse] | None = None,
):
    """Serve deterministic JSON responses for HTTP-backed skill registry tests."""

    etag_map = etags or {}
    post_map = post_routes or {}
    requests = request_log if request_log is not None else []
    route_counters: dict[str, int] = defaultdict(int)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b""
            requests.append(
                {
                    "path": self.path,
                    "method": "POST",
                    "headers": {key: value for key, value in self.headers.items()},
                    "body": json.loads(raw_body) if raw_body else {},
                }
            )
            route = post_map.get(self.path)
            if route is None:
                self.send_response(404)
                self.end_headers()
                return
            response = _coerce_response(route)
            self.send_response(response.status)
            body = b""
            if response.body is not None:
                body = json.dumps(response.body, ensure_ascii=False).encode("utf-8")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            requests.append(
                {
                    "path": self.path,
                    "method": "GET",
                    "headers": {key: value for key, value in self.headers.items()},
                }
            )
            if expected_auth and self.headers.get("Authorization", "") != expected_auth:
                self.send_response(401)
                self.end_headers()
                return
            route = payloads.get(self.path)
            if route is None:
                self.send_response(404)
                self.end_headers()
                return
            response = self._resolve_route_response(route)
            header_map = dict(response.headers)
            if self.path in etag_map and "ETag" not in header_map:
                header_map["ETag"] = etag_map[self.path]
            etag = header_map.get("ETag", "")
            if (
                response.honor_conditional_etag
                and response.status == 200
                and etag
                and self.headers.get("If-None-Match", "") == etag
            ):
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            self.send_response(response.status)
            body = b""
            if response.body is not None:
                body = json.dumps(response.body, ensure_ascii=False).encode("utf-8")
                header_map.setdefault("Content-Type", "application/json; charset=utf-8")
                header_map.setdefault("Content-Length", str(len(body)))
            for key, value in header_map.items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _resolve_route_response(
            self,
            route: dict[str, object] | HttpRouteResponse | list[dict[str, object] | HttpRouteResponse],
        ) -> HttpRouteResponse:
            if isinstance(route, list):
                index = route_counters[self.path]
                route_counters[self.path] = index + 1
                item = route[min(index, len(route) - 1)]
                return _coerce_response(item)
            return _coerce_response(route)

        def log_message(self, format: str, *args: Any):  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
