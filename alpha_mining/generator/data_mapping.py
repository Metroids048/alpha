"""L3 mapping of hypotheses to verified FieldCatalog data fields."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from alpha_mining.generator.hypothesis import StructuredLLM
from alpha_mining.domain.field_catalog import (
    FieldCatalog,
    field_quality_score,
    is_bad_field_name,
    is_weak_fundamental_field,
)


class HypothesisNotFoundError(LookupError):
    """The requested active hypothesis does not exist."""


class InsufficientFieldPool(RuntimeError):
    """Catalog filtering left fewer fields than the L3 contract requires."""


class InvalidDataMappingOutput(ValueError):
    """The LLM selected invalid, duplicate, or hallucinated data fields."""


@dataclass(frozen=True)
class DataMapping:
    mapping_id: str
    hypothesis_id: str
    data_field: str
    dataset_id: str | None
    rationale: str
    field_quality_score: float
    selected_by: str = "llm"


def _mapping_id(hypothesis_id: str, data_field: str) -> str:
    digest = hashlib.sha256(
        f"{hypothesis_id}\0{data_field}".encode("utf-8")
    ).hexdigest()
    return f"map_{digest}"


def _category_pool(catalog: FieldCatalog, data_category: str) -> list[str]:
    if data_category == "fundamental":
        return list(catalog.fund)
    if data_category == "analyst":
        return list(dict.fromkeys([*catalog.analyst, *catalog.model]))
    if data_category == "sentiment":
        return list(catalog.sent)
    if data_category == "price":
        return list(catalog.pv)
    if data_category == "options":
        option_fields: list[str] = []
        for dataset_id, fields in catalog.by_ds.items():
            if "option" in dataset_id:
                option_fields.extend(fields)
        return list(dict.fromkeys(option_fields))
    return list(
        dict.fromkeys(
            [
                *catalog.fund,
                *catalog.analyst,
                *catalog.model,
                *catalog.sent,
                *catalog.pv,
                *catalog.other,
            ]
        )
    )


def _filtered_candidates(
    catalog: FieldCatalog,
    data_category: str,
    *,
    limit: int,
) -> list[str]:
    fundamental_ids = set(catalog.fund)
    candidates = []
    for field_name in _category_pool(catalog, data_category):
        if not field_name or is_bad_field_name(field_name):
            continue
        if field_name in fundamental_ids and is_weak_fundamental_field(field_name):
            continue
        candidates.append(field_name)
    candidates = list(dict.fromkeys(candidates))
    candidates.sort(
        key=lambda field_name: (
            field_quality_score(field_name),
            catalog.field_user_count.get(field_name, 0.0),
            field_name,
        ),
        reverse=True,
    )
    return candidates[:limit]


def _mapping_schema(candidate_fields: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "mappings": {
                "type": "array",
                "minItems": 3,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "data_field": {"type": "string", "enum": candidate_fields},
                        "rationale": {"type": "string", "minLength": 1},
                    },
                    "required": ["data_field", "rationale"],
                },
            }
        },
        "required": ["mappings"],
    }


def _validated_selections(
    raw: Mapping[str, Any],
    candidate_fields: set[str],
) -> list[tuple[str, str]]:
    if set(raw) != {"mappings"} or not isinstance(raw.get("mappings"), list):
        raise InvalidDataMappingOutput("output must contain only a mappings list")
    rows = raw["mappings"]
    if not 3 <= len(rows) <= 8:
        raise InvalidDataMappingOutput("LLM must select 3 to 8 data fields")
    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"data_field", "rationale"}:
            raise InvalidDataMappingOutput(
                "each mapping must contain data_field and rationale"
            )
        field_name = row.get("data_field")
        rationale = row.get("rationale")
        if not isinstance(field_name, str) or field_name not in candidate_fields:
            raise InvalidDataMappingOutput(
                f"field was not in the filtered catalog: {field_name!r}"
            )
        if field_name in seen:
            raise InvalidDataMappingOutput(
                f"duplicate data field selected: {field_name}"
            )
        if not isinstance(rationale, str) or not rationale.strip():
            raise InvalidDataMappingOutput(
                "mapping rationale must be a non-empty string"
            )
        seen.add(field_name)
        selected.append((field_name, rationale.strip()))
    return selected


class DataMappingGenerator:
    def __init__(
        self,
        database: str | Path,
        *,
        llm: StructuredLLM,
        candidate_pool_limit: int = 200,
    ) -> None:
        if candidate_pool_limit < 8:
            raise ValueError("candidate_pool_limit must be at least 8")
        self.database = Path(database).expanduser().resolve()
        self.llm = llm
        self.candidate_pool_limit = int(candidate_pool_limit)

    def _hypothesis_context(self, hypothesis_id: str) -> tuple[str, str, str, str]:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """
                SELECT h.statement_cn, h.mechanism, h.horizon, t.data_category
                FROM hypotheses h
                JOIN research_topics t ON t.topic_id = h.topic_id
                WHERE h.hypothesis_id = ? AND h.status = 'active' AND t.active = 1
                """,
                (hypothesis_id,),
            ).fetchone()
        if row is None:
            raise HypothesisNotFoundError(
                f"active hypothesis not found: {hypothesis_id}"
            )
        return tuple(str(value or "") for value in row)  # type: ignore[return-value]

    def generate(
        self,
        hypothesis_id: str,
        catalog: FieldCatalog,
    ) -> tuple[DataMapping, ...]:
        statement, mechanism, horizon, data_category = self._hypothesis_context(
            hypothesis_id
        )
        candidate_fields = _filtered_candidates(
            catalog,
            data_category,
            limit=self.candidate_pool_limit,
        )
        if len(candidate_fields) < 3:
            raise InsufficientFieldPool(
                f"only {len(candidate_fields)} quality fields remain for {data_category}"
            )
        candidate_payload = [
            {
                "data_field": field_name,
                "dataset_id": catalog.field_dataset.get(field_name, ""),
                "quality_score": field_quality_score(field_name),
            }
            for field_name in candidate_fields
        ]
        raw = self.llm.generate_json(
            system_prompt=(
                "You map quantitative hypotheses to real catalog fields. Select only fields "
                "allowed by the supplied JSON schema and explain each selection."
            ),
            user_prompt=(
                f"Hypothesis: {statement}\nMechanism: {mechanism}\nHorizon: {horizon}\n"
                f"Data category: {data_category}\nFiltered FieldCatalog candidates:\n"
                f"{json.dumps(candidate_payload, ensure_ascii=False)}"
            ),
            json_schema=_mapping_schema(candidate_fields),
        )
        selections = _validated_selections(raw, set(candidate_fields))
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mappings = tuple(
            DataMapping(
                mapping_id=_mapping_id(hypothesis_id, field_name),
                hypothesis_id=hypothesis_id,
                data_field=field_name,
                dataset_id=catalog.field_dataset.get(field_name) or None,
                rationale=rationale,
                field_quality_score=field_quality_score(field_name),
            )
            for field_name, rationale in selections
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            for mapping in mappings:
                connection.execute(
                    """
                    INSERT INTO data_mappings (
                        mapping_id, hypothesis_id, data_field, dataset_id, rationale,
                        field_quality_score, selected_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(mapping_id) DO UPDATE SET
                        dataset_id=excluded.dataset_id,
                        rationale=excluded.rationale,
                        field_quality_score=excluded.field_quality_score,
                        selected_by=excluded.selected_by
                    """,
                    (
                        mapping.mapping_id,
                        mapping.hypothesis_id,
                        mapping.data_field,
                        mapping.dataset_id,
                        mapping.rationale,
                        mapping.field_quality_score,
                        mapping.selected_by,
                        created_at,
                    ),
                )
        return mappings
