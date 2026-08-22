# extractors.base — ABC + normalize_extraction_result
from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import ChunkRef, Entity, ExtractResult, Relation

_VALID_ENTITY_TYPES = {
    "Person", "Organization", "Location", "Concept", "Event", "Product", "Unknown",
}
_VALID_REL_TYPES = {
    "RELATES_TO", "PART_OF", "CAUSES", "DESCRIBES", "MENTIONS", "WORKS_FOR", "LOCATED_IN",
}


def _normalize_name(text: str) -> str:
    """Normalize entity name: strip + lowercase + collapse whitespace."""
    if not text:
        return ""
    return " ".join(text.strip().lower().split())


def _valid_entity_type(label: str) -> str:
    if label in _VALID_ENTITY_TYPES:
        return label
    return "Unknown"


def _valid_rel_type(rel_type: str) -> str:
    if rel_type in _VALID_REL_TYPES:
        return rel_type
    return "RELATES_TO"


def _parse_entity(obj: dict) -> Entity | None:
    """Parse a dict into an Entity, return None if invalid."""
    if not isinstance(obj, dict):
        return None
    name = str(obj.get("text") or obj.get("name") or "").strip()
    if not name:
        return None
    label = _valid_entity_type(str(obj.get("label") or obj.get("type") or "Unknown"))
    attrs = obj.get("attributes") or []
    attributes = []
    for attr in attrs:
        if isinstance(attr, dict) and attr.get("text"):
            attributes.append({"text": str(attr["text"]), "label": str(attr.get("label", ""))})
    return Entity(name=name, type=label, attributes=attributes)


class GraphExtractor(ABC):
    """Abstract base class for graph extractors."""

    @abstractmethod
    def extract(self, text: str, options: dict | None = None) -> ExtractResult:
        """Extract entities and relations from a single text chunk."""

    @abstractmethod
    async def extract_batch(
        self,
        chunks: list[ChunkRef],
        options: dict | None = None,
    ) -> list[ExtractResult]:
        """Extract from multiple chunks concurrently."""

    @staticmethod
    def normalize_extraction_result(result: ExtractResult) -> ExtractResult:
        """Normalize extraction result: deduplicate entities, merge attributes, resolve references.

        Steps:
        1. Collect entities from both result.entities and relation source/target dicts
        2. Deduplicate by (normalized_name, label)
        3. Merge attributes by union (deduplicate by (text, label))
        4. Resolve relation source/target: if string, match against entity names; if dict, parse directly
        5. Discard relations whose source/target cannot be resolved
        """
        if not result.relations and not result.entities:
            return result

        # Step 1: Collect all entities into a dict keyed by (normalized_name, label)
        entity_map: dict[tuple[str, str], Entity] = {}

        # Collect from existing entities (if any were pre-populated)
        for ent in result.entities:
            norm = _normalize_name(ent.name)
            if not norm:
                continue
            key = (norm, str(ent.type))
            if key in entity_map:
                existing = entity_map[key]
                for attr in ent.attributes:
                    attr_key = (attr.get("text", ""), attr.get("label", ""))
                    if attr_key not in {(a.get("text", ""), a.get("label", "")) for a in existing.attributes}:
                        existing.attributes.append(attr)
            else:
                entity_map[key] = Entity(
                    name=ent.name,
                    type=ent.type,
                    attributes=list(ent.attributes),
                )

        # Step 2: Extract entities from relation source/target and resolve references
        resolved_relations: list[Relation] = []

        for rel in result.relations:
            source_name = rel.from_name if isinstance(rel.from_name, str) else ""
            target_name = rel.to_name if isinstance(rel.to_name, str) else ""

            source_norm = _normalize_name(source_name)
            target_norm = _normalize_name(target_name)

            if not source_norm or not target_norm:
                continue

            # Find or create source entity
            source_key = None
            for sk, _ent in entity_map.items():
                if sk[0] == source_norm:
                    source_key = sk
                    break

            if source_key is None:
                source_key = (source_norm, "Unknown")
                if source_key not in entity_map:
                    entity_map[source_key] = Entity(name=source_name, type="Unknown", attributes=[])

            # Find or create target entity
            target_key = None
            for tk, _ent in entity_map.items():
                if tk[0] == target_norm:
                    target_key = tk
                    break

            if target_key is None:
                target_key = (target_norm, "Unknown")
                if target_key not in entity_map:
                    entity_map[target_key] = Entity(name=target_name, type="Unknown", attributes=[])

            resolved_relations.append(Relation(
                from_name=source_name,
                to_name=target_name,
                rel_type=_valid_rel_type(rel.rel_type),
            ))

        normalized = ExtractResult()
        normalized.entities = list(entity_map.values())
        normalized.relations = resolved_relations
        return normalized

    @staticmethod
    def normalize_raw_dict(parsed: dict) -> ExtractResult:
        """Normalize a raw parsed dict (from LLM output) into an ExtractResult.

        Handles the relations-embedded format:
        {
            "relations": [
                {
                    "source": {"text": "...", "label": "...", "attributes": [...]},
                    "target": {"text": "...", "label": "...", "attributes": [...]},
                    "text": "...",
                    "label": "..."
                }
            ]
        }

        Also supports legacy format with separate entities/relations arrays.
        """
        if not isinstance(parsed, dict):
            return ExtractResult()

        result = ExtractResult()

        # Collect entities from relations-embedded format
        raw_relations = parsed.get("relations") or parsed.get("triples") or []

        if isinstance(raw_relations, list):
            for rel_raw in raw_relations:
                if not isinstance(rel_raw, dict):
                    continue

                # Get source and target — could be dict or string
                source = rel_raw.get("source") or rel_raw.get("from") or rel_raw.get("subject")
                target = rel_raw.get("target") or rel_raw.get("to") or rel_raw.get("object")

                source_ent = None
                target_ent = None

                if isinstance(source, dict):
                    source_ent = _parse_entity(source)
                elif isinstance(source, str) and source.strip():
                    source_ent = Entity(name=source.strip(), type="Unknown", attributes=[])

                if isinstance(target, dict):
                    target_ent = _parse_entity(target)
                elif isinstance(target, str) and target.strip():
                    target_ent = Entity(name=target.strip(), type="Unknown", attributes=[])

                if not source_ent or not target_ent:
                    continue

                # Get relation label
                rel_label = str(
                    rel_raw.get("label") or rel_raw.get("rel_type") or rel_raw.get("type") or ""
                ).strip()

                result.entities.append(source_ent)
                result.entities.append(target_ent)
                result.relations.append(Relation(
                    from_name=source_ent.name,
                    to_name=target_ent.name,
                    rel_type=_valid_rel_type(rel_label),
                ))

        # Also handle legacy format with separate entities array
        legacy_entities = parsed.get("entities") or []
        if isinstance(legacy_entities, list):
            for ent_raw in legacy_entities:
                ent = _parse_entity(ent_raw) if isinstance(ent_raw, dict) else None
                if ent:
                    result.entities.append(ent)

        # Legacy relations with string from/to
        if isinstance(raw_relations, list):
            for rel_raw in raw_relations:
                if not isinstance(rel_raw, dict):
                    continue
                # If source/target were dicts, they were already handled above
                source = rel_raw.get("source") or rel_raw.get("from") or rel_raw.get("subject")
                target = rel_raw.get("target") or rel_raw.get("to") or rel_raw.get("object")
                if isinstance(source, str) and isinstance(target, str):
                    # Already handled in the loop above, skip
                    pass

        return GraphExtractor.normalize_extraction_result(result)
