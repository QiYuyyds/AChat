# extractors.factory — Factory pattern for graph extractor registration
from __future__ import annotations

from .base import GraphExtractor
from .llm import LLMGraphExtractor


class GraphExtractorFactory:
    """Factory for creating graph extractors by type name.

    Supports registration of new extractor types and creation by name.
    Pre-registers 'llm' → LLMGraphExtractor at import time.
    """

    _registry: dict[str, type[GraphExtractor]] = {}

    @classmethod
    def register(cls, name: str, extractor_cls: type[GraphExtractor]) -> None:
        """Register an extractor class under a type name."""
        cls._registry[name] = extractor_cls

    @classmethod
    def create(cls, name: str, options: dict | None = None) -> GraphExtractor:
        """Create an extractor instance by type name.

        Raises ValueError for unknown extractor names.
        """
        if name not in cls._registry:
            raise ValueError(
                f"Unknown extractor type: '{name}'. "
                f"Available: {', '.join(cls._registry.keys()) or '(none)'}"
            )
        return cls._registry[name](options or {})


# Pre-register 'llm' → LLMGraphExtractor
GraphExtractorFactory.register("llm", LLMGraphExtractor)
