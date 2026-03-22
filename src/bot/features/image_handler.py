"""Image upload handler with Claude vision support.

Downloads Telegram photos, validates size/format, and builds
a structured prompt for Claude vision analysis.
"""

import base64
from dataclasses import dataclass, field
from typing import Dict, Optional

from telegram import PhotoSize

from src.config.settings import Settings

# 10 MB upload limit
_MAX_IMAGE_BYTES = 10 * 1024 * 1024

_SUPPORTED_FORMATS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})


@dataclass
class ProcessedImage:
    """Result of processing an uploaded image."""

    prompt: str
    image_type: str
    base64_data: str
    size: int
    metadata: Dict[str, object] = field(default_factory=dict)


class ImageHandler:
    """Download and prepare images for Claude vision analysis."""

    def __init__(self, config: Settings) -> None:
        self.config = config

    async def process_image(
        self,
        photo: PhotoSize,
        caption: Optional[str] = None,
    ) -> ProcessedImage:
        """Download *photo* and return a Claude-ready ProcessedImage."""
        tg_file = await photo.get_file()
        image_bytes = bytes(await tg_file.download_as_bytearray())

        valid, reason = self._validate(image_bytes)
        if not valid:
            raise ValueError(reason or "Invalid image")

        img_type = self._classify(image_bytes)
        prompt = self._build_prompt(img_type, caption)
        fmt = self._detect_format(image_bytes)

        return ProcessedImage(
            prompt=prompt,
            image_type=img_type,
            base64_data=base64.b64encode(image_bytes).decode(),
            size=len(image_bytes),
            metadata={"format": fmt, "has_caption": caption is not None},
        )

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate(self, data: bytes) -> tuple[bool, Optional[str]]:
        if len(data) > _MAX_IMAGE_BYTES:
            return False, "Image too large (max 10 MB)"
        if len(data) < 100:
            return False, "Invalid image data"
        if self._detect_format(data) == "unknown":
            return False, "Unsupported image format"
        return True, None

    def supports_format(self, filename: str) -> bool:
        """Return True if *filename* has a supported extension."""
        if not filename:
            return False
        parts = filename.lower().rsplit(".", 1)
        if len(parts) < 2:
            return False
        return f".{parts[-1]}" in _SUPPORTED_FORMATS

    # ── Classification ────────────────────────────────────────────────────────

    def _classify(self, _data: bytes) -> str:
        """Heuristic image type classification (screenshot by default)."""
        return "screenshot"

    def _detect_format(self, data: bytes) -> str:
        if data[:4] == b"\x89PNG":
            return "png"
        if data[:3] == b"\xff\xd8\xff":
            return "jpeg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "gif"
        if data[:4] == b"RIFF" and b"WEBP" in data[:12]:
            return "webp"
        return "unknown"

    # ── Prompt builders ───────────────────────────────────────────────────────

    def _build_prompt(self, img_type: str, caption: Optional[str]) -> str:
        builders = {
            "screenshot": self._screenshot_prompt,
            "diagram": self._diagram_prompt,
            "ui_mockup": self._ui_prompt,
        }
        return builders.get(img_type, self._generic_prompt)(caption)

    def _screenshot_prompt(self, caption: Optional[str]) -> str:
        base = (
            "I'm sharing a screenshot. Please analyze it:\n\n"
            "1. Identify the application or website\n"
            "2. Describe UI elements and their purpose\n"
            "3. Note any issues or improvements\n"
            "4. Answer any specific questions\n\n"
        )
        return base + (f"Specific request: {caption}" if caption else "")

    def _diagram_prompt(self, caption: Optional[str]) -> str:
        base = (
            "I'm sharing a diagram. Please help me:\n\n"
            "1. Understand components and relationships\n"
            "2. Identify the diagram type\n"
            "3. Explain technical concepts\n"
            "4. Suggest improvements\n\n"
        )
        return base + (f"Specific request: {caption}" if caption else "")

    def _ui_prompt(self, caption: Optional[str]) -> str:
        base = (
            "I'm sharing a UI mockup. Please analyze:\n\n"
            "1. Layout and visual hierarchy\n"
            "2. UX considerations\n"
            "3. Accessibility aspects\n"
            "4. Implementation suggestions\n\n"
        )
        return base + (f"Specific request: {caption}" if caption else "")

    def _generic_prompt(self, caption: Optional[str]) -> str:
        base = "I'm sharing an image. Please analyze it and provide relevant insights.\n\n"
        return base + (f"Context: {caption}" if caption else "")
