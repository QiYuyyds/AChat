"""Consolidation data classes and utility functions.

Ported from AGI-memory:
- ``Item``, ``RecallFilter``, ``ConsolidationResult`` from ``memory.py``
- ``ConsolidationConfig`` from ``mem_stack.py``
- ``_tokenize_zh`` helper from ``memory.py``
"""

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Item:
    """A single long-term memory item."""

    content: str
    importance: float = 0.5
    embedding: Optional[List[float]] = None
    id: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    category: str = ""
    tags: List[str] = field(default_factory=list)
    slot_hint: str = ""
    score: float = 0.0


@dataclass
class RecallFilter:
    """Filtering parameters for ``LongTerm.recall_by_filter``.

    Duck-typed so it is compatible with promptctx.RecallFilter as well.
    """

    categories: List[str] = field(default_factory=list)
    require_tags: List[str] = field(default_factory=list)
    max_age_hours: int = 0
    min_score: float = 0.0
    top_k: int = 0


@dataclass
class ConsolidationResult:
    """Structured return value from ``LongTerm.consolidate``."""

    deduped: int = 0
    merged: int = 0
    expired: int = 0
    delete_from_db: List[int] = field(default_factory=list)
    update_in_db: List["Item"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ConsolidationConfig
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "similarity_threshold": 0.80,
    "dedup_threshold": 0.95,
    "ttl_days": 30,
    "decay_rate": 0.995,
    "min_importance": 0.3,
    "trigger_interval": 5,
}


@dataclass
class ConsolidationConfig:
    """Memory consolidation configuration (aligned with AGI-memory)."""

    similarity_threshold: float = _DEFAULTS["similarity_threshold"]
    dedup_threshold: float = _DEFAULTS["dedup_threshold"]
    ttl_days: int = _DEFAULTS["ttl_days"]
    decay_rate: float = _DEFAULTS["decay_rate"]
    min_importance: float = _DEFAULTS["min_importance"]
    trigger_interval: int = _DEFAULTS["trigger_interval"]

    @classmethod
    def from_settings(cls, settings: Any) -> "ConsolidationConfig":
        """Build config from AgentHub ``Settings`` object."""
        if settings is None:
            return cls()
        return cls(
            similarity_threshold=float(
                getattr(settings, "memory_consolidation_similarity", _DEFAULTS["similarity_threshold"])
                or _DEFAULTS["similarity_threshold"]
            ),
            dedup_threshold=float(
                getattr(settings, "memory_consolidation_dedup", _DEFAULTS["dedup_threshold"])
                or _DEFAULTS["dedup_threshold"]
            ),
            ttl_days=int(
                getattr(settings, "memory_consolidation_ttl_days", _DEFAULTS["ttl_days"])
                or _DEFAULTS["ttl_days"]
            ),
            decay_rate=float(
                getattr(settings, "memory_consolidation_decay_rate", _DEFAULTS["decay_rate"])
                or _DEFAULTS["decay_rate"]
            ),
            min_importance=float(
                getattr(settings, "memory_consolidation_min_importance", _DEFAULTS["min_importance"])
                or _DEFAULTS["min_importance"]
            ),
            trigger_interval=int(
                getattr(settings, "memory_consolidation_trigger", _DEFAULTS["trigger_interval"])
                or _DEFAULTS["trigger_interval"]
            ),
        )

    @classmethod
    def default(cls) -> "ConsolidationConfig":
        return cls()


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def tokenize_zh(text: str) -> List[str]:
    """Chinese-English mixed tokenizer: Chinese by character, English/numeric by word."""
    tokens: List[str] = []
    word = ""
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF:
            if word:
                tokens.append(word.lower())
                word = ""
            tokens.append(ch)
        elif ch.isalnum():
            word += ch
        else:
            if word:
                tokens.append(word.lower())
                word = ""
    if word:
        tokens.append(word.lower())
    return tokens


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def tf_cosine(query_tokens: Optional[List[str]], content: str) -> float:
    """TF bag-of-words cosine similarity using ``tokenize_zh``."""
    if query_tokens is None:
        query_tokens = tokenize_zh("")
    item_tokens = tokenize_zh(content)
    if not query_tokens or not item_tokens:
        return 0.0
    vocab: Dict[str, int] = {}
    for t in query_tokens:
        if t not in vocab:
            vocab[t] = len(vocab)
    for t in item_tokens:
        if t not in vocab:
            vocab[t] = len(vocab)
    size = len(vocab)
    va = [0.0] * size
    vb = [0.0] * size
    for t, c in Counter(query_tokens).items():
        va[vocab[t]] = float(c)
    for t, c in Counter(item_tokens).items():
        vb[vocab[t]] = float(c)
    return cosine_similarity(va, vb)
