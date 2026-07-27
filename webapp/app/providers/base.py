from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    text: str
    raw: Any = None


class LLMProvider(ABC):
    """Provider-agnostic interface every agent calls through.

    Concrete providers (OpenRouter now, direct Anthropic later) translate
    the generic ``thinking``/``json_mode`` flags into whatever the
    underlying API actually expects.
    """

    @abstractmethod
    def generate(
        self,
        *,
        system: str,
        user_content: str,
        model: str,
        max_tokens: int = 4096,
        thinking: bool = False,
        json_mode: bool = False,
        response_schema: type | None = None,
    ) -> LLMResponse:
        ...

    def generate_image(
        self,
        *,
        prompt: str,
        model: str,
        aspect_ratio: str = "1:1",
        reference_images: list[bytes] | None = None,
    ) -> bytes:
        """Return raw image bytes (PNG/JPEG) for a text-to-image prompt.

        `reference_images` (optional) are prior images passed as visual context so
        the model can keep the same subjects/characters consistent across a set.
        Optional capability — only providers that support image generation
        override this. Callers must handle NotImplementedError."""
        raise NotImplementedError(f"{type(self).__name__} does not support image generation")

    def read_image_text(self, *, image_bytes: bytes, model: str) -> str:
        """OCR: return the text visible in an image. Optional capability."""
        raise NotImplementedError(f"{type(self).__name__} does not support image OCR")
