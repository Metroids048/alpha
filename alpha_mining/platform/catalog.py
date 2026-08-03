"""Read-only platform catalog synchronization used by the factory gate."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from alpha_mining.generation.validation import ExpressionCatalog, LocalExpressionValidator
from alpha_mining.offline.metadata import MetadataCache


class CatalogClient(Protocol):
    def list_datasets(self, params: dict[str, object]) -> dict[str, Any]: ...
    def list_data_fields(self, params: dict[str, object]) -> dict[str, Any]: ...
    def list_operators(self, params: dict[str, object]) -> dict[str, Any]: ...


class ReadOnlyExpressionCatalog(LocalExpressionValidator):
    """Production catalog adapter backed only by a verified local snapshot."""

    def __init__(self, metadata: MetadataCache, *, max_age_hours: float = 168) -> None:
        super().__init__(metadata, max_age_hours=max_age_hours, allow_stale_catalog=False)


def _utc_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


class PlatformCatalogSynchronizer:
    def __init__(self, cache_dir: str | Path, *, page_size: int = 50) -> None:
        self.cache_dir = Path(cache_dir)
        # The platform currently rejects catalog pagination above 50.
        self.page_size = max(1, min(50, int(page_size)))

    def _all_pages(self, fetch: Any, params: dict[str, object]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        expected: int | None = None
        while True:
            payload = fetch({**params, "limit": self.page_size, "offset": offset})
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("catalog response is not a paged object")
            count = int(payload.get("count") or 0)
            if expected is None:
                expected = count
            elif count != expected:
                raise ValueError("catalog count changed during pagination")
            batch = [item for item in payload["results"] if isinstance(item, dict)]
            rows.extend(batch)
            if len(rows) >= count:
                return rows[:count]
            if not batch:
                raise ValueError("catalog returned an empty incomplete page")
            offset += len(batch)

    def sync(self, client: CatalogClient, *, region: str, universe: str, delay: int) -> dict[str, int]:
        base = {"instrumentType": "EQUITY", "region": region, "universe": universe, "delay": int(delay)}
        datasets = self._all_pages(client.list_datasets, base)
        dataset_ids = [str(item.get("id") or "").strip() for item in datasets]
        dataset_ids = list(dict.fromkeys(item for item in dataset_ids if item))
        if not dataset_ids:
            raise ValueError("platform returned no dataset IDs")
        fields: list[dict[str, Any]] = []
        for dataset_id in dataset_ids:
            rows = self._all_pages(client.list_data_fields, {**base, "dataset.id": dataset_id})
            for row in rows:
                if str(row.get("id") or "").strip():
                    fields.append({**row, "_ds": dataset_id})
        if not fields:
            raise ValueError("platform returned no data fields")
        operators = self._all_pages(client.list_operators, base)
        operator_records = [_normalise_operator_record(item) for item in operators]
        if any(record is None for record in operator_records):
            raise ValueError("platform operator metadata has no verifiable arity")
        normalised_operators = [record for record in operator_records if record is not None]
        names = [record["name"] for record in normalised_operators]
        names = list(dict.fromkeys(item for item in names if item))
        if not names:
            raise ValueError("platform returned no operator metadata")
        now = _utc_timestamp()
        context = {"cached_at": now, "region": region, "universe": universe, "delay": int(delay), "source": "platform_catalog"}
        payloads = {
            ".alpha_datasets_cache.json": {**context, "dataset_ids": dataset_ids, "records": datasets},
            ".alpha_datafields_cache.json": {**context, "rows": fields},
            ".alpha_operators_cache.json": {**context, "operators": names, "records": normalised_operators},
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for filename, payload in payloads.items():
            target = self.cache_dir / filename
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            temporary.replace(target)
        return {"datasets": len(dataset_ids), "data_fields": len(fields), "operators": len(names)}


def _normalise_operator_record(row: dict[str, Any]) -> dict[str, Any] | None:
    name = str(row.get("name") or row.get("id") or "").strip().lower()
    signature = str(row.get("signature") or row.get("definition") or "").strip()
    if not name:
        return None
    try:
        arity = int(row["arity"])
    except (KeyError, TypeError, ValueError):
        arity = _arity_from_signature(signature)
    if arity is None or arity < 0:
        return None
    return {
        "name": name,
        "signature": signature or f"{name}({','.join('x' for _ in range(arity))})",
        "arity": arity,
        "description": str(row.get("description") or ""),
    }


def _arity_from_signature(signature: str) -> int | None:
    match = re.fullmatch(r"[^()]+\(([^()]*)\)", signature.strip())
    if not match:
        return None
    arguments = match.group(1).strip()
    if not arguments:
        return 0
    if any(token in arguments for token in ("...", "*", "[", "]")):
        return None
    return len([part for part in arguments.split(",") if part.strip()])
