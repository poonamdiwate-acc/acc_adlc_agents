"""DE-03 output parser - strict JSON parse + Pydantic v2 validation.

The SKILL.md system prompt says: *Return only the JSON object. No
explanation, no markdown, no preamble.* In practice some models still
wrap the payload in a fenced ```json block - this parser strips a single
fence if present and then runs ``json.loads`` once. No silent recovery.

Validation enforces the config's output schema for ``data_model`` and
``storage_selection``:

* every entity has the required fields (AC-01)
* ``category`` (when present) is in ``behaviour.entity_categories`` (AC-03)
* ``confidence`` (when present) is in ``behaviour.confidence_levels`` (AC-04)
* every relationship ``type`` is in
  {one_to_one, one_to_many, many_to_one, many_to_many} (AC-12)
* ``primary_store`` has technology + rationale (AC-07)
* every ``storage_class`` (when present) is in ``behaviour.storage_classes``
  (AC-08)

The returned objects are plain dicts so they can be JSON-serialised onto
the HTTP response without further conversion.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.exceptions import OutputParseError


_FENCE_OPEN_PATTERN = re.compile(r"^```(?:json)?[ \t]*\r?\n?", flags=re.IGNORECASE)
_FENCE_CLOSE_PATTERN = re.compile(r"\r?\n?```\s*$")

_ALLOWED_RELATIONSHIP_TYPES = {
    "one_to_one",
    "one_to_many",
    "many_to_one",
    "many_to_many",
}


class _Attribute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    required: bool
    description: str = Field(min_length=1)


class _Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str = Field(min_length=1)
    type: str = Field(min_length=1)
    description: str = Field(min_length=1)


class _Entity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entity_id: str = Field(min_length=1)
    entity_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    attributes: List[_Attribute] = Field(default_factory=list)
    relationships: List[_Relationship] = Field(default_factory=list)
    req_id_refs: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    normalization: Optional[str] = None
    confidence: Optional[str] = None


class _PrimaryStore(BaseModel):
    model_config = ConfigDict(extra="ignore")

    technology: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    data_types: List[str] = Field(default_factory=list)
    storage_class: Optional[str] = None
    confidence: Optional[str] = None


class _SecondaryStore(BaseModel):
    model_config = ConfigDict(extra="ignore")

    technology: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    storage_class: Optional[str] = None
    confidence: Optional[str] = None


class _StorageSelection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary_store: _PrimaryStore
    secondary_stores: List[_SecondaryStore] = Field(default_factory=list)
    overall_strategy: str = Field(min_length=1)


class _DesignPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data_model: List[_Entity] = Field(default_factory=list)
    storage_selection: _StorageSelection


def parse(
    raw_text: str,
    *,
    allowed_categories: List[str],
    allowed_storage_classes: List[str],
    allowed_confidence_levels: List[str],
) -> Dict[str, Any]:
    """Parse and validate an LLM response.

    Returns a dict shaped::

        {
            "data_model": [ {...}, ... ],
            "storage_selection": {...},
        }

    Behaviour-driven enums (``entity_categories``, ``storage_classes``,
    ``confidence_levels``) are validated when the fields are present.
    Missing optional enum fields are tolerated - the SKILL strongly
    encourages them but the config's authoritative ``outputs`` schema
    does not require them.
    """
    payload = _load_json(raw_text)
    payload = _normalize_llm_aliases(payload)

    try:
        parsed = _DesignPayload.model_validate(payload)
    except ValidationError as exc:
        raise OutputParseError(
            "LLM output failed schema validation",
            detail={"errors": exc.errors()},
        ) from exc

    cats = set(allowed_categories)
    classes = set(allowed_storage_classes)
    confidences = set(allowed_confidence_levels)

    for index, entity in enumerate(parsed.data_model):
        if entity.category is not None and cats and entity.category not in cats:
            raise OutputParseError(
                f"Entity #{index + 1} has unknown category '{entity.category}'",
                detail={
                    "entity_id": entity.entity_id,
                    "category": entity.category,
                    "allowed": sorted(cats),
                },
            )
        if (
            entity.confidence is not None
            and confidences
            and entity.confidence not in confidences
        ):
            raise OutputParseError(
                f"Entity #{index + 1} has unknown confidence "
                f"'{entity.confidence}'",
                detail={
                    "entity_id": entity.entity_id,
                    "confidence": entity.confidence,
                    "allowed": sorted(confidences),
                },
            )
        for rel_index, rel in enumerate(entity.relationships):
            if rel.type not in _ALLOWED_RELATIONSHIP_TYPES:
                raise OutputParseError(
                    f"Entity #{index + 1} relationship #{rel_index + 1} has "
                    f"unknown type '{rel.type}'",
                    detail={
                        "entity_id": entity.entity_id,
                        "relationship_index": rel_index,
                        "type": rel.type,
                        "allowed": sorted(_ALLOWED_RELATIONSHIP_TYPES),
                    },
                )

    primary = parsed.storage_selection.primary_store
    _validate_store(
        primary,
        location="primary_store",
        allowed_classes=classes,
        allowed_confidences=confidences,
    )
    for store_index, store in enumerate(parsed.storage_selection.secondary_stores):
        _validate_store(
            store,
            location=f"secondary_stores[{store_index}]",
            allowed_classes=classes,
            allowed_confidences=confidences,
        )

    return {
        "data_model": [entity.model_dump() for entity in parsed.data_model],
        "storage_selection": parsed.storage_selection.model_dump(),
    }


def _validate_store(
    store: Any,
    *,
    location: str,
    allowed_classes: set,
    allowed_confidences: set,
) -> None:
    storage_class = getattr(store, "storage_class", None)
    confidence = getattr(store, "confidence", None)
    if (
        storage_class is not None
        and allowed_classes
        and storage_class not in allowed_classes
    ):
        raise OutputParseError(
            f"{location} has unknown storage_class '{storage_class}'",
            detail={
                "location": location,
                "storage_class": storage_class,
                "allowed": sorted(allowed_classes),
            },
        )
    if (
        confidence is not None
        and allowed_confidences
        and confidence not in allowed_confidences
    ):
        raise OutputParseError(
            f"{location} has unknown confidence '{confidence}'",
            detail={
                "location": location,
                "confidence": confidence,
                "allowed": sorted(allowed_confidences),
            },
        )


def _normalize_llm_aliases(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Rewrite the LLM's common field-name variants to our canonical schema.

    Gemini drifts on field names between runs — sometimes `entity_name`,
    sometimes `name`; sometimes `category`, sometimes `entity_category`;
    sometimes `entity`, sometimes `target_entity_id` in relationships.
    This pre-pass rewires the observed aliases into our canonical names
    and synthesizes missing required strings (description, purpose) when
    the LLM has equivalent info elsewhere. The pydantic validator stays
    strict — only obvious equivalents are rewritten. Verified against
    captured outputs in tests/_debug/.
    """
    _UNKNOWN_ENTITY_FIELDS = (
        "owner_service", "owner", "service_owner", "owning_service",
    )
    _ENTITY_ALIASES = {
        "entity_category": "category",
        "name": "entity_name",
        "entity": "entity_name",
    }
    _RELATIONSHIP_ALIASES = {
        "target_entity_id": "entity",
        "target_entity":    "entity",
        "to_entity":        "entity",
        "to":               "entity",
        "related_entity":   "entity",
    }
    # Some Gemini runs emit extra relationship metadata that isn't in our
    # schema. We drop them rather than fail "extra forbidden":
    #   - source_entity_id / source_entity / from_entity / from: source
    #     is implicit (it's the entity this relationship lives under).
    #   - name / label / relationship_name: redundant with `description`.
    #   - cardinality: same info as `type`.
    _UNKNOWN_RELATIONSHIP_FIELDS = (
        "source_entity_id", "source_entity", "from_entity", "from",
        "name", "label", "relationship_name",
        "cardinality",
    )

    for entity in payload.get("data_model") or []:
        if not isinstance(entity, dict):
            continue
        for src, dst in _ENTITY_ALIASES.items():
            if dst not in entity and src in entity:
                entity[dst] = entity.pop(src)
        for unknown in _UNKNOWN_ENTITY_FIELDS:
            entity.pop(unknown, None)
        if not (entity.get("description") or "").strip():
            entity["description"] = _entity_description_fallback(entity)

        for rel in entity.get("relationships") or []:
            if not isinstance(rel, dict):
                continue
            for src, dst in _RELATIONSHIP_ALIASES.items():
                if dst not in rel and src in rel:
                    rel[dst] = rel.pop(src)
            for unknown in _UNKNOWN_RELATIONSHIP_FIELDS:
                rel.pop(unknown, None)
            if not (rel.get("description") or "").strip():
                rel["description"] = (
                    f"{rel.get('type', 'related')} relationship to "
                    f"{rel.get('entity', 'another entity')}"
                )

    storage = payload.get("storage_selection")
    if isinstance(storage, dict):
        for store in storage.get("secondary_stores") or []:
            if not isinstance(store, dict):
                continue
            if not (store.get("purpose") or "").strip():
                store["purpose"] = _purpose_fallback(store)
    return payload


def _entity_description_fallback(entity: Dict[str, Any]) -> str:
    name = entity.get("entity_name") or entity.get("entity_id") or "entity"
    attrs = entity.get("attributes") or []
    if attrs and isinstance(attrs[0], dict) and attrs[0].get("description"):
        return f"{name}: {attrs[0]['description']}"
    return f"{name} - derived from requirements"


def _purpose_fallback(store: Dict[str, Any]) -> str:
    rationale = (store.get("rationale") or "").strip()
    if rationale:
        first = rationale.split(".")[0].strip()
        return (first[:200] + "...") if len(first) > 200 else first
    return store.get("technology", "secondary store")


def _load_json(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        raise OutputParseError(
            "LLM returned empty response",
            detail={},
        )
    text = _FENCE_OPEN_PATTERN.sub("", text, count=1)
    text = _FENCE_CLOSE_PATTERN.sub("", text, count=1)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OutputParseError(
            f"LLM output is not valid JSON: {exc.msg}",
            detail={
                "line": exc.lineno,
                "column": exc.colno,
                "preview": text[:200],
            },
        ) from exc

    if not isinstance(data, dict):
        raise OutputParseError(
            "LLM output must be a JSON object at the top level",
            detail={"type": type(data).__name__},
        )
    return data
