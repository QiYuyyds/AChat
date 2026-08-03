"""Consolidation data classes and utility functions.

Ported from AGI-memory:
- ``Item``, ``RecallFilter``, ``ConsolidationResult`` from ``memory.py``
- ``ConsolidationConfig`` from ``mem_stack.py``
- ``_tokenize_zh`` helper from ``memory.py``
"""

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Item:
    """A single long-term memory item."""

    content: str
    importance: float = 0.5
    embedding: list[float] | None = None
    id: int | None = None
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    category: str = ""
    tags: list[str] = field(default_factory=list)
    slot_hint: str = ""
    score: float = 0.0
    scope: str = "global"
    agent_id: str = ""
    # Multi-user isolation: owning user (None = legacy/global)
    user_id: str | None = None
    # Runtime-only decay checkpoint (not persisted). 0.0 → fall back to created_at.
    last_decay_ts: float = 0.0
    # Structured fields for dual-path retrieval (summary embedding + keyword Jaccard)
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    content_scope: str = ""


@dataclass
class RecallFilter:
    """Filtering parameters for ``LongTerm.recall_by_filter``.

    Duck-typed so it is compatible with promptctx.RecallFilter as well.
    """

    categories: list[str] = field(default_factory=list)
    require_tags: list[str] = field(default_factory=list)
    max_age_hours: int = 0
    min_score: float = 0.0
    top_k: int = 0


@dataclass
class ConsolidationResult:
    """Structured return value from ``LongTerm.consolidate``."""

    deduped: int = 0
    merged: int = 0
    expired: int = 0
    delete_from_db: list[int] = field(default_factory=list)
    update_in_db: list["Item"] = field(default_factory=list)


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
    # Case memory lifecycle parameters (longer TTL, slower decay)
    "case_ttl_days": 90,
    "case_decay_rate": 0.998,
    "case_min_importance": 0.4,
    "case_dedup_threshold": 0.90,
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
    # Case memory-specific lifecycle parameters
    case_ttl_days: int = _DEFAULTS["case_ttl_days"]
    case_decay_rate: float = _DEFAULTS["case_decay_rate"]
    case_min_importance: float = _DEFAULTS["case_min_importance"]
    case_dedup_threshold: float = _DEFAULTS["case_dedup_threshold"]

    def get_params_for_category(self, category: str) -> dict[str, Any]:
        """Return lifecycle parameters appropriate for the given category.

        Case memories (category="case") use longer TTL, slower decay,
        higher min_importance, and a more lenient dedup threshold.
        All other categories use the default parameters.
        """
        if category == "case":
            return {
                "ttl_days": self.case_ttl_days,
                "decay_rate": self.case_decay_rate,
                "min_importance": self.case_min_importance,
                "dedup_threshold": self.case_dedup_threshold,
            }
        return {
            "ttl_days": self.ttl_days,
            "decay_rate": self.decay_rate,
            "min_importance": self.min_importance,
            "dedup_threshold": self.dedup_threshold,
        }

    @classmethod
    def from_settings(cls, settings: Any) -> "ConsolidationConfig":
        """Build config from AChat ``Settings`` object."""
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


def tokenize_zh(text: str) -> list[str]:
    """Chinese-English mixed tokenizer: Chinese by character, English/numeric by word."""
    tokens: list[str] = []
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


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def tf_cosine(query_tokens: list[str] | None, content: str) -> float:
    """TF bag-of-words cosine similarity using ``tokenize_zh``."""
    if query_tokens is None:
        query_tokens = tokenize_zh("")
    item_tokens = tokenize_zh(content)
    if not query_tokens or not item_tokens:
        return 0.0
    vocab: dict[str, int] = {}
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


def keyword_score(query_tokens: list[str] | None, item_keywords: list[str] | None) -> float:
    """Jaccard similarity between query tokens and item keywords.

    Both sets are lowercased before comparison. If either set is empty,
    the score is 0.0. This is a zero-dependency keyword matcher suitable
    for 3-5 keyword matching scenarios.
    """
    if not query_tokens or not item_keywords:
        return 0.0
    query_set = {t.lower() for t in query_tokens if t}
    kw_set = {k.lower() for k in item_keywords if k}
    if not query_set or not kw_set:
        return 0.0
    intersection = query_set & kw_set
    union = query_set | kw_set
    return len(intersection) / len(union) if union else 0.0
