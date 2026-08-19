"""AChat memory subsystem — file-native pipeline + PG-backed Preference.

File-native pipeline: auto_memory → auto_index → auto_dream → proactive
PG-backed Preference: structured key-value pairs (preserved)
"""

from app.memory.memory_service import MemoryService
from app.memory.memory_writer_compat import (
    _IMPORTANCE_BY_CATEGORY,
    _PREFERENCE_MERGE_PROMPT,
    classify_memory_content,
    extract_preferences_compat,
)
from app.memory.preference import Preference

__all__ = [
    "MemoryService",
    "Preference",
    "classify_memory_content",
    "extract_preferences_compat",
    "_IMPORTANCE_BY_CATEGORY",
    "_PREFERENCE_MERGE_PROMPT",
]
