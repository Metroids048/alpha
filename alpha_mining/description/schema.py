"""Evidence-backed, dynamic platform Description Schema registry."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class DescriptionSchema:
    schema_id: str
    alpha_type: str
    payload_path: tuple[str, ...]
    min_length: int
    max_length: int | None
    required_sections: tuple[str, ...]
    source: str
    source_version: str
    schema_hash: str
    raw_schema: dict[str, Any]


class DescriptionSchemaRegistry:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def observe(
        self,
        *,
        alpha_type: str,
        source: str,
        raw_schema: Mapping[str, Any],
        source_version: str = "",
    ) -> DescriptionSchema:
        raw = dict(raw_schema)
        path = raw.get("payloadPath")
        if not isinstance(path, list) or not path or not all(isinstance(item, str) and item for item in path):
            raise ValueError("schema has no evidence-backed payloadPath")
        kind = str(alpha_type or "").upper().strip()
        if not kind:
            raise ValueError("alpha_type is required")
        canonical = _canonical(raw)
        schema_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        schema_id = hashlib.sha256(
            f"{kind}\0{source}\0{source_version}\0{schema_hash}".encode("utf-8")
        ).hexdigest()
        required = raw.get("requiredSections")
        required_sections = tuple(str(item) for item in required) if isinstance(required, list) else ()
        observed_at = _utc_now()
        with sqlite3.connect(self.database) as con:
            con.execute(
                """INSERT OR IGNORE INTO description_schema_observations
                (schema_id,alpha_type,source,source_version,schema_hash,raw_schema_json,
                 payload_path_json,min_length,max_length,required_sections_json,observed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    schema_id,
                    kind,
                    str(source),
                    str(source_version),
                    schema_hash,
                    canonical,
                    _canonical(path),
                    int(raw.get("minLength") or 0),
                    int(raw["maxLength"]) if raw.get("maxLength") is not None else None,
                    _canonical(required_sections),
                    observed_at,
                ),
            )
        return DescriptionSchema(
            schema_id,
            kind,
            tuple(path),
            int(raw.get("minLength") or 0),
            int(raw["maxLength"]) if raw.get("maxLength") is not None else None,
            required_sections,
            str(source),
            str(source_version),
            schema_hash,
            raw,
        )

    def observe_from_payload(
        self,
        *,
        alpha_type: str,
        source: str,
        payload: Mapping[str, Any],
        source_version: str = "",
    ) -> DescriptionSchema | None:
        """Find an explicit schema object without inferring a type-specific slot."""

        def find(value: object) -> dict[str, Any] | None:
            if isinstance(value, dict):
                for key in ("requiredDescriptionSchema", "descriptionSchema"):
                    candidate = value.get(key)
                    if isinstance(candidate, dict) and candidate.get("payloadPath"):
                        return dict(candidate)
                for child in value.values():
                    found = find(child)
                    if found is not None:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = find(child)
                    if found is not None:
                        return found
            return None

        raw_schema = find(dict(payload))
        if raw_schema is None:
            return None
        return self.observe(
            alpha_type=alpha_type,
            source=source,
            raw_schema=raw_schema,
            source_version=source_version,
        )

    def resolve(self, alpha_type: str) -> DescriptionSchema | None:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                """SELECT schema_id,alpha_type,payload_path_json,min_length,max_length,
                          required_sections_json,source,source_version,schema_hash,raw_schema_json
                   FROM description_schema_observations WHERE alpha_type=?
                   ORDER BY observed_at DESC,schema_id DESC LIMIT 1""",
                (str(alpha_type or "").upper(),),
            ).fetchone()
        if row is None:
            return None
        return DescriptionSchema(
            str(row[0]),
            str(row[1]),
            tuple(json.loads(row[2])),
            int(row[3]),
            int(row[4]) if row[4] is not None else None,
            tuple(json.loads(row[5])),
            str(row[6]),
            str(row[7]),
            str(row[8]),
            dict(json.loads(row[9])),
        )
