"""HTTP transport layer mixin for HttpSkillBackend."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class HttpSkillTransportMixin:
    """HTTP fetch/post, throttling, retry, and async helpers for HttpSkillBackend.

    Expects the host class to provide:
    - self.timeout, self.retry_attempts, self.retry_backoff_seconds
    - self.min_request_interval, self.max_concurrency
    - self._throttle_lock, self._last_request_at, self._async_semaphore
    - self._request_headers(root)
    - self._renegotiate_auth_if_expired(root)
    """

    def _fetch_json(
        self: Any,
        url: str,
        *,
        if_none_match: str = "",
        allow_missing: bool = False,
        root: str = "",
    ) -> tuple[dict[str, object] | None, str, int, dict[str, str], dict[str, object]]:
        last_error: Exception | None = None
        attempts = max(1, self.retry_attempts)
        backpressure_events = 0
        retry_after_seconds = 0.0
        request_count = 0
        for attempt in range(1, attempts + 1):
            try:
                self._throttle_request()
                headers = self._request_headers(root)
                if if_none_match:
                    headers["If-None-Match"] = if_none_match
                request = Request(url, headers=headers)
                request_count += 1
                with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                    response_headers = {key.lower(): value for key, value in response.headers.items()}
                    etag = response_headers.get("etag", "").strip()
                    status = getattr(response, "status", 200)
                    raw_payload = response.read().decode("utf-8")
                loaded = json.loads(raw_payload)
                if not isinstance(loaded, dict):
                    raise ValueError(f"Remote skill payload at {url!r} must be a JSON object")
                return (
                    loaded,
                    etag,
                    status,
                    response_headers,
                    {
                        "request_count": request_count,
                        "backpressure_events": backpressure_events,
                        "retry_after_seconds": retry_after_seconds,
                    },
                )
            except HTTPError as exc:  # noqa: PERF203
                if exc.code == 304:
                    response_headers = {key.lower(): value for key, value in exc.headers.items()}
                    etag = response_headers.get("etag", "").strip() or if_none_match
                    return (
                        None,
                        etag,
                        304,
                        response_headers,
                        {
                            "request_count": request_count,
                            "backpressure_events": backpressure_events,
                            "retry_after_seconds": retry_after_seconds,
                        },
                    )
                if allow_missing and exc.code == 404:
                    response_headers = {key.lower(): value for key, value in exc.headers.items()}
                    return (
                        None,
                        "",
                        404,
                        response_headers,
                        {
                            "request_count": request_count,
                            "backpressure_events": backpressure_events,
                            "retry_after_seconds": retry_after_seconds,
                        },
                    )
                if exc.code == 401 and root and attempt < attempts:
                    if self._renegotiate_auth_if_expired(root):
                        continue
                if exc.code in {429, 503} and attempt < attempts:
                    backpressure_events += 1
                    response_headers = {key.lower(): value for key, value in exc.headers.items()}
                    retry_after_seconds = max(
                        retry_after_seconds,
                        self._retry_after_seconds(response_headers),
                    )
                    self._sleep_before_retry(attempt, retry_after_seconds)
                    continue
                last_error = exc
            except Exception as exc:  # noqa: PERF203
                last_error = exc
                if attempt >= attempts:
                    break
                self._sleep_before_retry(attempt, 0.0)
        raise RuntimeError(f"Failed to fetch remote skill payload from {url!r}: {last_error}") from last_error

    def _post_json(self: Any, url: str, body: dict[str, object], *, root: str = "") -> dict[str, object]:
        """POST JSON to a registry endpoint and return the parsed response."""
        self._throttle_request()
        headers = self._request_headers(root)
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
        request = Request(url, data=data, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def _sleep_before_retry(self: Any, attempt: int, retry_after_seconds: float) -> None:
        delay = max(float(retry_after_seconds), self.retry_backoff_seconds * attempt)
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _retry_after_seconds(headers: dict[str, str]) -> float:
        raw = headers.get("retry-after", "").strip()
        if not raw:
            return 0.0
        try:
            return max(float(raw), 0.0)
        except ValueError:
            return 0.0

    def _throttle_request(self: Any) -> None:
        interval = max(float(self.min_request_interval), 0.0)
        if interval <= 0:
            return
        with self._throttle_lock:
            now = time.monotonic()
            wait = interval - (now - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last_request_at = now

    async def _run_remote_call(self: Any, func: Any, *args: Any) -> Any:
        semaphore = self._async_semaphore
        if semaphore is None:
            semaphore = asyncio.Semaphore(max(1, self.max_concurrency))
            self._async_semaphore = semaphore
        async with semaphore:
            return await asyncio.to_thread(func, *args)
