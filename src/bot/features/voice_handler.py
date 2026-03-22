"""Voice message transcription (feature-flagged).

Supports OpenAI Whisper and Mistral Voxtral as backends.
Both SDKs are optional — a RuntimeError is raised if the
selected backend's package is not installed.
"""

from dataclasses import dataclass
from typing import Any, Optional

import structlog
from telegram import Voice

from src.config.settings import Settings

logger = structlog.get_logger(__name__)

# 25 MB default voice upload limit
_DEFAULT_MAX_BYTES = 25 * 1024 * 1024


@dataclass
class ProcessedVoice:
    """Result of voice-message processing."""

    prompt: str
    transcription: str
    duration: int  # seconds


class VoiceHandler:
    """Download and transcribe Telegram voice messages."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self._max_bytes: int = getattr(
            config, "voice_max_file_size_bytes", _DEFAULT_MAX_BYTES
        )
        self._provider: str = getattr(config, "voice_provider", "openai")
        self._mistral_client: Optional[Any] = None
        self._openai_client: Optional[Any] = None

    async def process_voice_message(
        self,
        voice: Voice,
        caption: Optional[str] = None,
    ) -> ProcessedVoice:
        """Download *voice* message, transcribe, and return a structured result."""
        initial_size: Optional[int] = getattr(voice, "file_size", None)
        self._check_size(initial_size)

        tg_file = await voice.get_file()
        resolved_size: Optional[int] = getattr(tg_file, "file_size", None)
        self._check_size(resolved_size)

        if initial_size is None and resolved_size is None:
            raise ValueError(
                "Cannot determine voice message size before download. "
                "Please retry with a shorter message."
            )

        audio_bytes = bytes(await tg_file.download_as_bytearray())
        self._check_size(len(audio_bytes))

        logger.info(
            "Transcribing voice message",
            provider=self._provider,
            duration=voice.duration,
            size=len(audio_bytes),
        )

        if self._provider == "mistral":
            transcription = await self._transcribe_mistral(audio_bytes)
        else:
            transcription = await self._transcribe_openai(audio_bytes)

        logger.info("Transcription complete", length=len(transcription))

        label = caption if caption else "Voice message transcription:"
        prompt = f"{label}\n\n{transcription}"
        duration = getattr(voice, "duration", 0)
        if hasattr(duration, "total_seconds"):
            duration = int(duration.total_seconds())

        return ProcessedVoice(
            prompt=prompt, transcription=transcription, duration=int(duration)
        )

    # ── Size guard ────────────────────────────────────────────────────────────

    def _check_size(self, size: Optional[int]) -> None:
        if isinstance(size, int) and size > self._max_bytes:
            mb = size / 1024 / 1024
            limit_mb = self._max_bytes / 1024 / 1024
            raise ValueError(
                f"Voice message too large ({mb:.1f} MB). Max allowed: {limit_mb:.0f} MB."
            )

    # ── Transcription backends ────────────────────────────────────────────────

    async def _transcribe_openai(self, audio: bytes) -> str:
        client = self._get_openai_client()
        try:
            response = await client.audio.transcriptions.create(
                model=getattr(self.config, "resolved_voice_model", "whisper-1"),
                file=("voice.ogg", audio),
            )
        except Exception as exc:
            logger.warning("OpenAI transcription failed", error=str(exc))
            raise RuntimeError("OpenAI transcription request failed.") from exc
        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise ValueError("OpenAI transcription returned empty response.")
        return text

    async def _transcribe_mistral(self, audio: bytes) -> str:
        client = self._get_mistral_client()
        try:
            response = await client.audio.transcriptions.complete_async(
                model=getattr(self.config, "resolved_voice_model", "voxtral-mini"),
                file={"content": audio, "file_name": "voice.ogg"},
            )
        except Exception as exc:
            logger.warning("Mistral transcription failed", error=str(exc))
            raise RuntimeError("Mistral transcription request failed.") from exc
        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise ValueError("Mistral transcription returned empty response.")
        return text

    # ── Client factories ──────────────────────────────────────────────────────

    def _get_openai_client(self) -> Any:
        if self._openai_client is not None:
            return self._openai_client
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Optional dependency 'openai' is missing. "
                'Install: pip install "claude-remote-bot[voice]"'
            ) from exc
        api_key = getattr(self.config, "openai_api_key_str", None)
        if not api_key:
            raise RuntimeError("OpenAI API key is not configured.")
        self._openai_client = AsyncOpenAI(api_key=api_key)
        return self._openai_client

    def _get_mistral_client(self) -> Any:
        if self._mistral_client is not None:
            return self._mistral_client
        try:
            from mistralai import Mistral
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Optional dependency 'mistralai' is missing. "
                'Install: pip install "claude-remote-bot[voice]"'
            ) from exc
        api_key = getattr(self.config, "mistral_api_key_str", None)
        if not api_key:
            raise RuntimeError("Mistral API key is not configured.")
        self._mistral_client = Mistral(api_key=api_key)
        return self._mistral_client
