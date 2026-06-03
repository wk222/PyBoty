"""Vision Tool — multi-provider image analysis with browser screenshot integration.

Improvements over reference implementations:
- Multi-provider VLM routing (OpenAI, Anthropic, local)
- Accepts local files, URLs, and browser screenshots
- Canvas-aware detail level (focused=brief, deep=detailed)
- Token-efficient output with configurable verbosity
- Event bus tracing
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Optional, Type
from urllib.parse import urlparse

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_SUPPORTED_FORMATS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
})

_MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


class VisionInput(BaseModel):
    source: str = Field(description=(
        "Image source: a local file path, a URL (http/https), "
        "or 'screenshot' to capture current browser page."
    ))
    question: str = Field(
        default="Describe this image in detail.",
        description="What to analyze or ask about the image.",
    )
    detail: str = Field(
        default="auto",
        description="Detail level: 'low' (faster/cheaper), 'high' (detailed), or 'auto' (canvas-based).",
    )


def _encode_local_file(path: str) -> tuple[str, str]:
    """Read a local image file and return (base64_data, mime_type)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    if p.stat().st_size > _MAX_FILE_SIZE:
        raise ValueError(f"Image too large: {p.stat().st_size / 1024 / 1024:.1f} MB (max {_MAX_FILE_SIZE // 1024 // 1024} MB)")
    suffix = p.suffix.lower()
    if suffix not in _SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported image format: {suffix}")
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return data, mime


def _is_url(source: str) -> bool:
    try:
        parsed = urlparse(source)
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


class VisionTool(BaseTool):
    """Multi-provider image analysis tool for PyBot."""

    name: str = "vision"
    description: str = (
        "Analyze images using vision language models. Accepts local file paths, "
        "URLs, or 'screenshot' (captures current browser page). "
        "Ask questions about the image content, extract text (OCR), "
        "identify objects, analyze diagrams, etc."
    )
    args_schema: Type[BaseModel] = VisionInput

    _llm_factory: Any = None
    _browser_service: Any = None
    _canvas_mode: str = "balanced"
    _event_callback: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        llm_factory: Any = None,
        browser_service: Any = None,
        canvas_mode: str = "balanced",
        event_callback: Any = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._llm_factory = llm_factory
        self._browser_service = browser_service
        self._canvas_mode = canvas_mode
        self._event_callback = event_callback

    def _emit_event(self, detail: str):
        if self._event_callback:
            try:
                self._event_callback("vision_analysis", {
                    "detail": detail[:200],
                    "canvas": self._canvas_mode,
                })
            except Exception:
                pass

    def _resolve_detail(self, detail: str) -> str:
        if detail != "auto":
            return detail
        return {
            "focused": "low",
            "balanced": "high",
            "deep": "high",
        }.get(self._canvas_mode, "high")

    def _build_system_prompt(self, detail_level: str) -> str:
        if detail_level == "low":
            return (
                "You are a concise image analyst. Provide a brief, focused answer "
                "in 2-3 sentences. Prioritize the most important observations."
            )
        return (
            "You are a detailed image analyst. Provide a thorough analysis "
            "covering all relevant aspects of the image. Include spatial "
            "relationships, text content, colors, and notable details."
        )

    def _run(self, **kwargs) -> str:
        source = (kwargs.get("source") or "").strip()
        question = kwargs.get("question", "Describe this image in detail.")
        detail = kwargs.get("detail", "auto")

        if not source:
            return "Error: 'source' is required — provide a file path, URL, or 'screenshot'"

        detail_level = self._resolve_detail(detail)

        try:
            if source.lower() == "screenshot":
                return self._handle_screenshot(question, detail_level)
            elif _is_url(source):
                return self._handle_url(source, question, detail_level)
            else:
                return self._handle_local(source, question, detail_level)
        except Exception as e:
            logger.error("[Vision] analysis error: %s", e)
            return f"Vision analysis error: {e}"

    def _handle_screenshot(self, question: str, detail_level: str) -> str:
        if self._browser_service is None:
            try:
                from core.assets.tools.browser.browser_service import BrowserService, HAS_PLAYWRIGHT
                if not HAS_PLAYWRIGHT:
                    return "Browser not available — Playwright not installed"
                self._browser_service = BrowserService()
            except ImportError:
                return "Browser service not available"

        if not self._browser_service.is_active():
            return "No active browser page. Use the browser tool to navigate to a page first."

        screenshot_path = self._browser_service.screenshot(full_page=False)
        return self._analyze_local(screenshot_path, question, detail_level)

    def _handle_url(self, url: str, question: str, detail_level: str) -> str:
        self._emit_event(f"Analyzing URL image: {url[:80]}")
        return self._call_vlm_with_url(url, question, detail_level)

    def _handle_local(self, source: str, question: str, detail_level: str) -> str:
        return self._analyze_local(source, question, detail_level)

    def _analyze_local(self, path: str, question: str, detail_level: str) -> str:
        self._emit_event(f"Analyzing local image: {Path(path).name}")
        try:
            b64_data, mime = _encode_local_file(path)
        except (FileNotFoundError, ValueError) as e:
            return str(e)
        return self._call_vlm_with_base64(b64_data, mime, question, detail_level)

    def _call_vlm_with_url(self, url: str, question: str, detail_level: str) -> str:
        system_prompt = self._build_system_prompt(detail_level)
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": url, "detail": detail_level}},
                    {"type": "text", "text": question},
                ],
            },
        ]
        return self._invoke_llm(messages)

    def _call_vlm_with_base64(
        self, b64_data: str, mime: str, question: str, detail_level: str
    ) -> str:
        system_prompt = self._build_system_prompt(detail_level)
        data_url = f"data:{mime};base64,{b64_data}"
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url, "detail": detail_level}},
                    {"type": "text", "text": question},
                ],
            },
        ]
        return self._invoke_llm(messages)

    def _invoke_llm(self, messages: list[dict]) -> str:
        """Route to available VLM provider."""
        if self._llm_factory:
            try:
                llm = self._llm_factory(None, None)
                from langchain_core.messages import HumanMessage, SystemMessage
                lc_messages = []
                for m in messages:
                    if m["role"] == "system":
                        lc_messages.append(SystemMessage(content=m["content"]))
                    else:
                        lc_messages.append(HumanMessage(content=m["content"]))
                response = llm.invoke(lc_messages)
                return response.content
            except Exception as e:
                logger.warning("[Vision] LLM factory call failed: %s", e)

        try:
            import openai
            client = openai.OpenAI()
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=1500 if self._canvas_mode == "focused" else 4096,
            )
            return response.choices[0].message.content
        except ImportError:
            pass
        except Exception as e:
            logger.warning("[Vision] OpenAI direct call failed: %s", e)

        try:
            import anthropic
            client = anthropic.Anthropic()
            system_msg = ""
            user_content = []
            for m in messages:
                if m["role"] == "system":
                    system_msg = m["content"]
                else:
                    content = m["content"]
                    if isinstance(content, list):
                        for block in content:
                            if block["type"] == "text":
                                user_content.append({"type": "text", "text": block["text"]})
                            elif block["type"] == "image_url":
                                url = block["image_url"]["url"]
                                if url.startswith("data:"):
                                    media_type, _, b64 = url.partition(";base64,")
                                    media_type = media_type.replace("data:", "")
                                    user_content.append({
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": media_type,
                                            "data": b64,
                                        },
                                    })
                                else:
                                    user_content.append({
                                        "type": "image",
                                        "source": {"type": "url", "url": url},
                                    })
                    elif isinstance(content, str):
                        user_content.append({"type": "text", "text": content})

            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1500 if self._canvas_mode == "focused" else 4096,
                system=system_msg,
                messages=[{"role": "user", "content": user_content}],
            )
            return response.content[0].text
        except ImportError:
            pass
        except Exception as e:
            logger.warning("[Vision] Anthropic direct call failed: %s", e)

        return (
            "No VLM provider available. Install openai or anthropic package, "
            "or configure an LLM factory with vision support."
        )
