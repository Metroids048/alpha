"""Read-only platform catalog synchronization used by the factory gate."""

from __future__ import annotations

import json
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
        names = [str(item.get("name") or item.get("id") or "").strip() for item in operators]
        names = list(dict.fromkeys(item for item in names if item))
        if not names:
            raise ValueError("platform returned no operator metadata")
        now = _utc_timestamp()
        payloads = {
            ".alpha_datasets_cache.json": {"cached_at": now, "dataset_ids": dataset_ids, "source": "platform_catalog"},
            ".alpha_datafields_cache.json": {"cached_at": now, "rows": fields, "source": "platform_catalog"},
            ".alpha_operators_cache.json": {"cached_at": now, "operators": names, "source": "platform_catalog"},
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for filename, payload in payloads.items():
            target = self.cache_dir / filename
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            temporary.replace(target)
        return {"datasets": len(dataset_ids), "data_fields": len(fields), "operators": len(names)}
