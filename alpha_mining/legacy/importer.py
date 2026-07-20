"""Streaming importer for legacy CSV and embedded platform JSON."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from alpha_mining.common import to_float
from alpha_mining.platform.check_parser import parse_gate_observations
from alpha_mining.platform.gates import GateRegistry
from .features import ExpressionFeatures, extract_features

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "browser_cookie",
        "password",
        "passwd",
        "wq_password",
        "token",
        "api_key",
        "apikey",
        "username",
        "wq_username",
        "email",
        "user_id",
        "userid",
    }
)
_EMAIL_VALUE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_AUTH_VALUE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")


def _raise_csv_field_limit() -> None:
    """Allow large embedded JSON cells while respecting the platform C long size."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if str(key).lower().replace("-", "_") in SENSITIVE_KEYS
            else sanitize_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _AUTH_VALUE.sub(
            "[REDACTED_AUTH]", _EMAIL_VALUE.sub("[REDACTED_EMAIL]", value)
        )
    return value


def json_object(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return sanitize_payload(value)
    try:
        parsed = json.loads(str(value or ""))
    except Exception:
        return default
    return sanitize_payload(parsed)


def _normalized_row(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(key or "").strip().casefold(): str(value or "").strip()
        for key, value in row.items()
    }


def _get(row: Mapping[str, str], *names: str) -> str:
    return next(
        (row[name.casefold()] for name in names if row.get(name.casefold(), "") != ""),
        "",
    )


@dataclass
class ImportSummary:
    rows_scanned: int = 0
    canonical_records: int = 0
    lineage_records: int = 0
    chunks_committed: int = 0
    checks_imported: int = 0
    gates_observed: int = 0


class LegacyImporter:
    def __init__(self, database: str | Path, *, chunk_size: int = 2000) -> None:
        self.database = Path(database)
        self.chunk_size = max(1, int(chunk_size))

    def import_sources(self, sources: Iterable[str | Path]) -> ImportSummary:
        _raise_csv_field_limit()
        summary = ImportSummary()
        for source in sources:
            path = Path(source)
            with path.open(
                "r", encoding="utf-8-sig", errors="ignore", newline=""
            ) as handle:
                reader = csv.DictReader(handle)
                chunk: list[tuple[int, dict[str, str]]] = []
                for source_row, raw in enumerate(reader, start=2):
                    summary.rows_scanned += 1
                    chunk.append((source_row, _normalized_row(raw)))
                    if len(chunk) >= self.chunk_size:
                        self._commit_chunk(path, chunk, summary)
                        chunk = []
                if chunk:
                    self._commit_chunk(path, chunk, summary)
        return summary

    def _commit_chunk(
        self,
        source: Path,
        rows: list[tuple[int, dict[str, str]]],
        summary: ImportSummary,
    ) -> None:
        now = utc_now()
        gate_observations = []
        with sqlite3.connect(self.database) as con:
            for source_row, row in rows:
                expression = _get(row, "expression", "regular", "formula")
                alpha_id = _get(row, "alpha_id", "alphaid", "id")
                if not expression and not alpha_id:
                    continue
                features = (
                    extract_features(expression)
                    if expression
                    else extract_features(alpha_id)
                )
                observed_at = (
                    _get(row, "utc_iso", "observed_at", "date_created") or None
                )
                legacy_id = (
                    "legacy_"
                    + hashlib.sha256(
                        f"{source.resolve()}\0{source_row}\0{alpha_id}\0{features.exact_hash}".encode()
                    ).hexdigest()[:24]
                )
                existing = con.execute(
                    "SELECT canonical_id FROM legacy_alphas WHERE exact_hash=? AND is_canonical=1 ORDER BY observed_at,source_row LIMIT 1",
                    (features.exact_hash,),
                ).fetchone()
                canonical_id = str(existing[0]) if existing else legacy_id
                is_canonical = int(existing is None)
                settings = {
                    key: _get(row, key)
                    for key in (
                        "region",
                        "universe",
                        "neutralization",
                        "decay",
                        "truncation",
                        "delay",
                        "type",
                        "alpha_type",
                        "theme_id",
                        "pyramid_id",
                    )
                    if _get(row, key)
                }
                metrics = {
                    key: to_float(_get(row, key))
                    for key in (
                        "sharpe",
                        "fitness",
                        "turnover",
                        "returns",
                        "drawdown",
                        "margin",
                    )
                    if _get(row, key)
                }
                checks = json_object(
                    _get(row, "platform_check_json", "checks_json"), []
                )
                simulation = json_object(
                    _get(row, "platform_simulation_json", "simulation_json"), {}
                )
                con.execute(
                    """INSERT OR IGNORE INTO legacy_alphas
                    (legacy_id,canonical_id,is_canonical,exact_hash,normalized_expression,expression,alpha_id,source,source_row,observed_at,family,settings_json,metrics_json,checks_json,simulation_json,parse_valid,imported_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        legacy_id,
                        canonical_id,
                        is_canonical,
                        features.exact_hash,
                        features.normalized_expression,
                        expression,
                        alpha_id,
                        source.name,
                        source_row,
                        observed_at,
                        _get(row, "family"),
                        json.dumps(settings),
                        json.dumps(metrics),
                        json.dumps(checks, ensure_ascii=False),
                        json.dumps(simulation, ensure_ascii=False),
                        int(features.parse_valid),
                        now,
                    ),
                )
                lineage_id = hashlib.sha256(
                    f"{canonical_id}\0{legacy_id}".encode()
                ).hexdigest()
                changed = con.execute(
                    "INSERT OR IGNORE INTO alpha_lineage(lineage_id,canonical_id,legacy_id,alpha_id,source,relationship,created_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        lineage_id,
                        canonical_id,
                        legacy_id,
                        alpha_id,
                        source.name,
                        "canonical" if is_canonical else "exact_duplicate",
                        now,
                    ),
                ).rowcount
                summary.lineage_records += changed
                if is_canonical:
                    summary.canonical_records += 1
                    self._store_features(con, canonical_id, features)
                payload = checks if isinstance(checks, dict) else {"checks": checks}
                if isinstance(payload, dict):
                    merged = dict(payload)
                    merged.setdefault("id", alpha_id)
                    merged.setdefault("settings", settings)
                    parsed = parse_gate_observations(
                        merged, observed_at=observed_at, source=source.name
                    )
                    gate_observations.extend(parsed)
                    for index, obs in enumerate(parsed):
                        event_id = hashlib.sha256(
                            f"{legacy_id}\0{index}\0{obs.gate_name}".encode()
                        ).hexdigest()
                        summary.checks_imported += con.execute(
                            "INSERT OR IGNORE INTO alpha_check_events(event_id,legacy_id,name,result,limit_value,observed_value,raw_json,observed_at) VALUES (?,?,?,?,?,?,?,?)",
                            (
                                event_id,
                                legacy_id,
                                obs.gate_name,
                                obs.result,
                                obs.limit,
                                obs.value,
                                json.dumps(
                                    sanitize_payload(payload), ensure_ascii=False
                                ),
                                observed_at,
                            ),
                        ).rowcount
            con.commit()
        if gate_observations:
            summary.gates_observed += GateRegistry(self.database).record_many(
                gate_observations
            )
        summary.chunks_committed += 1

    @staticmethod
    def _store_features(
        con: sqlite3.Connection, canonical_id: str, f: ExpressionFeatures
    ) -> None:
        con.execute(
            """INSERT OR REPLACE INTO alpha_expression_features
            (canonical_id,ast_json,structure_signature,behavior_signature,operators_json,topology,fields_json,field_categories_json,windows_json,grouping_json,normalizers_json,conditions_json,nesting_depth,operator_count,unit_warnings_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                canonical_id,
                f.ast_json,
                f.structure_signature,
                f.behavior_signature,
                json.dumps(f.operators),
                f.topology,
                json.dumps(f.fields),
                json.dumps(f.field_categories),
                json.dumps(f.windows),
                json.dumps(f.grouping),
                json.dumps(f.normalizers),
                json.dumps(f.conditions),
                f.nesting_depth,
                f.operator_count,
                json.dumps(f.unit_warnings),
            ),
        )
