from __future__ import annotations

import json


class _Client:
    def _page(self, rows, params):
        offset, limit = int(params["offset"]), int(params["limit"])
        return {"count": len(rows), "results": rows[offset : offset + limit]}

    def list_datasets(self, params):
        return self._page([{"id": "pv1"}], params)

    def list_data_fields(self, params):
        return self._page([{"id": "close"}], params)

    def list_operators(self, params):
        return self._page([{"name": name} for name in ("rank", "ts_rank", "ts_delta", "ts_zscore", "ts_std_dev", "ts_mean")], params)


def test_catalog_sync_writes_only_complete_platform_metadata(tmp_path) -> None:
    from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

    result = PlatformCatalogSynchronizer(tmp_path, page_size=1).sync(_Client(), region="USA", universe="TOP3000", delay=1)

    assert result == {"datasets": 1, "data_fields": 1, "operators": 6}
    fields = json.loads((tmp_path / ".alpha_datafields_cache.json").read_text(encoding="utf-8"))
    operators = json.loads((tmp_path / ".alpha_operators_cache.json").read_text(encoding="utf-8"))
    assert fields["rows"][0]["_ds"] == "pv1"
    assert operators["source"] == "platform_catalog"


def test_catalog_sync_caps_platform_page_size_at_fifty(tmp_path) -> None:
    from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

    assert PlatformCatalogSynchronizer(tmp_path, page_size=100).page_size == 50
