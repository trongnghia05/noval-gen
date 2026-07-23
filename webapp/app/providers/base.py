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
    ) -> LLMResponse:
        ...
