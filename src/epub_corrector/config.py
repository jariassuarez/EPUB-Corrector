from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ServerConfig:
    base_url: str = "http://127.0.0.1:1234/v1"
    api_key: str = "lm-studio"
    model: str = "local-model"


@dataclass
class CorrectionConfig:
    temperature: float = 0.0
    max_segments_per_request: int = 1
    max_chars_per_request: int = 6000
    similarity_threshold: float = 0.88
    max_change_ratio: float = 0.20
    max_context: int = 0
    max_context_chars: int = 3000
    max_workers: int = 1
    max_retries: int = 3
    no_thinking: bool = True
    debug: bool = False
    use_schema: bool = True
    rewrite: bool = False
    translate: bool = False
    target_language: str | None = None
    aggressive: bool = False

    def effective_similarity_threshold(self) -> float:
        if self.translate or self.aggressive:
            return 0.0
        return self.similarity_threshold

    def effective_max_change_ratio(self) -> float:
        if self.translate or self.aggressive:
            return 1.0
        return self.max_change_ratio


