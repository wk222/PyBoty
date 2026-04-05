"""Media understanding pipeline — image description + audio transcription.

Provides a
provider-agnostic interface for converting media files into text that
agents can reason about.

Supported media types:
  - **Image**: caption / description via vision-capable LLMs
  - **Audio**: transcription via Whisper-compatible APIs

Architecture::

    media file → detect_media_type → select provider → run → text result

Usage::

    from core.systems.runtime.media_understanding import MediaPipeline, OpenAIMediaProvider

    pipeline = MediaPipeline()
    pipeline.register_provider(OpenAIMediaProvider(api_key="..."))
    result = pipeline.process("photo.jpg")
    print(result.text)
"""

from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class MediaType(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    UNKNOWN = "unknown"


@dataclass
class MediaResult:
    """Result of processing a single media file."""
    media_type: MediaType
    text: str = ""
    provider: str = ""
    file_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None


@runtime_checkable
class MediaProvider(Protocol):
    """Interface for media understanding providers."""

    @property
    def provider_name(self) -> str: ...

    @property
    def supported_types(self) -> list[MediaType]: ...

    def process(self, file_path: str, media_type: MediaType, **kwargs: Any) -> MediaResult: ...


def detect_media_type(file_path: str) -> MediaType:
    """Detect media type from file extension or MIME type."""
    mime, _ = mimetypes.guess_type(file_path)
    if mime:
        if mime.startswith("image/"):
            return MediaType.IMAGE
        if mime.startswith("audio/"):
            return MediaType.AUDIO
        if mime.startswith("video/"):
            return MediaType.VIDEO
    ext = os.path.splitext(file_path)[1].lower()
    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".tiff"}
    _AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma", ".webm"}
    _VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov", ".wmv"}
    if ext in _IMAGE_EXTS:
        return MediaType.IMAGE
    if ext in _AUDIO_EXTS:
        return MediaType.AUDIO
    if ext in _VIDEO_EXTS:
        return MediaType.VIDEO
    return MediaType.UNKNOWN


class OpenAIMediaProvider:
    """Media understanding via OpenAI APIs (GPT-4V for images, Whisper for audio)."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def supported_types(self) -> list[MediaType]:
        return [MediaType.IMAGE, MediaType.AUDIO]

    def process(self, file_path: str, media_type: MediaType, **kwargs: Any) -> MediaResult:
        if media_type == MediaType.IMAGE:
            return self._describe_image(file_path, **kwargs)
        if media_type == MediaType.AUDIO:
            return self._transcribe_audio(file_path, **kwargs)
        return MediaResult(
            media_type=media_type,
            file_path=file_path,
            success=False,
            error=f"Unsupported media type: {media_type}",
        )

    def _describe_image(self, file_path: str, **kwargs: Any) -> MediaResult:
        try:
            import base64

            import openai

            client = openai.OpenAI(api_key=self._api_key, base_url=self._base_url)
            with open(file_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()

            ext = os.path.splitext(file_path)[1].lower().lstrip(".")
            mime_map = {"jpg": "jpeg", "svg": "svg+xml"}
            mime_ext = mime_map.get(ext, ext)

            prompt = kwargs.get("prompt", "Describe this image in detail.")
            response = client.chat.completions.create(
                model=kwargs.get("model", "gpt-4o"),
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/{mime_ext};base64,{img_data}"}},
                    ],
                }],
                max_tokens=kwargs.get("max_tokens", 500),
            )
            text = response.choices[0].message.content or ""
            return MediaResult(
                media_type=MediaType.IMAGE,
                text=text,
                provider="openai",
                file_path=file_path,
                metadata={"model": kwargs.get("model", "gpt-4o")},
            )
        except Exception as exc:
            logger.error("Image description failed: %s", exc)
            return MediaResult(
                media_type=MediaType.IMAGE,
                file_path=file_path,
                success=False,
                error=str(exc),
                provider="openai",
            )

    def _transcribe_audio(self, file_path: str, **kwargs: Any) -> MediaResult:
        try:
            import openai

            client = openai.OpenAI(api_key=self._api_key, base_url=self._base_url)
            with open(file_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model=kwargs.get("model", "whisper-1"),
                    file=f,
                    language=kwargs.get("language"),
                )
            return MediaResult(
                media_type=MediaType.AUDIO,
                text=response.text,
                provider="openai",
                file_path=file_path,
                metadata={"model": kwargs.get("model", "whisper-1")},
            )
        except Exception as exc:
            logger.error("Audio transcription failed: %s", exc)
            return MediaResult(
                media_type=MediaType.AUDIO,
                file_path=file_path,
                success=False,
                error=str(exc),
                provider="openai",
            )


class LocalMediaProvider:
    """Fallback provider using file metadata when no API is available."""

    @property
    def provider_name(self) -> str:
        return "local"

    @property
    def supported_types(self) -> list[MediaType]:
        return [MediaType.IMAGE, MediaType.AUDIO, MediaType.VIDEO]

    def process(self, file_path: str, media_type: MediaType, **kwargs: Any) -> MediaResult:
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        text = f"[{media_type.value} file: {os.path.basename(file_path)}, size: {file_size} bytes]"
        return MediaResult(
            media_type=media_type,
            text=text,
            provider="local",
            file_path=file_path,
            metadata={"size": file_size, "filename": os.path.basename(file_path)},
        )


class MediaPipeline:
    """Orchestrates media understanding across multiple providers."""

    def __init__(self) -> None:
        self._providers: list[MediaProvider] = []

    def register_provider(self, provider: MediaProvider) -> None:
        self._providers.append(provider)
        logger.info("Registered media provider: %s", provider.provider_name)

    def process(self, file_path: str, **kwargs: Any) -> MediaResult:
        """Process a media file using the first capable provider."""
        media_type = detect_media_type(file_path)
        if media_type == MediaType.UNKNOWN:
            return MediaResult(
                media_type=media_type,
                file_path=file_path,
                success=False,
                error=f"Unknown media type for: {file_path}",
            )

        for provider in self._providers:
            if media_type in provider.supported_types:
                result = provider.process(file_path, media_type, **kwargs)
                if result.success:
                    return result
                logger.debug("Provider %s failed for %s: %s", provider.provider_name, file_path, result.error)

        return MediaResult(
            media_type=media_type,
            file_path=file_path,
            success=False,
            error=f"No provider supports {media_type.value}",
        )

    def process_batch(self, file_paths: list[str], **kwargs: Any) -> list[MediaResult]:
        """Process multiple files sequentially."""
        return [self.process(fp, **kwargs) for fp in file_paths]
