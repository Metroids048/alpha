"""Regression tests for the operators catalog protocol.

The platform serves /operators as an unpaged top-level JSON array, while
/data-sets and /data-fields serve paged {"count","results"} objects. Two layers
used to assume every catalog endpoint was a paged object:

* ``ReadOnlyPlatformClient._catalog_page`` rejected any non-dict payload.
* ``PlatformCatalogSynchronizer.sync`` fed all three endpoints into
  ``_all_pages``, which requires count/results.

Confirmed against the live read-only endpoint on 2026-08-08: HTTP 200,
``application/json``, top-level ``list`` of 82 objects keyed
category/definition/description/documentation/level/name/scope.

The relaxation must stay endpoint-specific: datasets and data-fields keep the
strict paged-object contract so a genuine protocol regression there still
fails closed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

OPERATOR_ROWS = [
    {
        "name": "rank",
        "definition": "rank(x)",
        "category": "Cross Sectional",
        "description": "cross-sectional rank",
        "documentation": "",
        "level": "ALL",
        "scope": ["REGULAR"],
    },
    {
        "name": "ts_delta",
        "definition": "ts_delta(x, d)",
        "category": "Time Series",
        "description": "value minus value d days ago",
        "documentation": "",
        "level": "ALL",
        "scope": ["REGULAR"],
    },
]


def _client(tmp_path: Path, payload: object, *, status: int = 200):
    """Build a ReadOnlyPlatformClient whose session returns ``payload`` once."""
    from alpha_mining.platform.client import ReadOnlyPlatformClient

    class Response:
        def __init__(self) -> None:
            self.status_code = status
            self.headers = {"Content-Type": "application/json"}
            self.content = json.dumps(payload).encode("utf-8")
            self.url = "https://example.test/operators"
            self.history: list[object] = []

        def json(self) -> object:
            return payload

    class Session:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    client = ReadOnlyPlatformClient(
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        min_interval=0,
        sleeper=lambda _seconds: None,
    )
    client.session = Session()
    # The probe under test is the catalog read, not authentication.
    client.authenticate = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    return client


CONTEXT = {"instrumentType": "EQUITY", "region": "USA", "universe": "TOP3000", "delay": 1}


def test_client_accepts_unpaged_operators_array(tmp_path: Path) -> None:
    """TEST 1: /operators returning list[dict] must be readable."""
    client = _client(tmp_path, OPERATOR_ROWS)

    rows = client.list_operators(dict(CONTEXT))

    names = [row["name"] for row in rows]
    assert names == ["rank", "ts_delta"]


def test_client_still_accepts_paged_operators_object(tmp_path: Path) -> None:
    """Backwards compatibility: the older paged operators contract still works."""
    client = _client(tmp_path, {"count": len(OPERATOR_ROWS), "results": OPERATOR_ROWS})

    rows = client.list_operators(dict(CONTEXT))

    assert [row["name"] for row in rows] == ["rank", "ts_delta"]


def test_client_rejects_operators_array_with_non_object_items(tmp_path: Path) -> None:
    """TEST 3: a malformed element must fail closed, never be silently dropped."""
    from alpha_mining.platform.client import PlatformReadError

    client = _client(tmp_path, [OPERATOR_ROWS[0], "rank", None])

    with pytest.raises(PlatformReadError):
        client.list_operators(dict(CONTEXT))


def test_client_rejects_unpaged_datasets_array(tmp_path: Path) -> None:
    """TEST 4: datasets keeps the strict paged-object contract."""
    from alpha_mining.platform.client import PlatformReadError

    client = _client(tmp_path, [{"id": "pv1"}])

    with pytest.raises(PlatformReadError):
        client.list_datasets(dict(CONTEXT))


def test_client_rejects_unpaged_data_fields_array(tmp_path: Path) -> None:
    """TEST 5: data-fields keeps the strict paged-object contract."""
    from alpha_mining.platform.client import PlatformReadError

    client = _client(tmp_path, [{"id": "close"}])

    with pytest.raises(PlatformReadError):
        client.list_data_fields(dict(CONTEXT))


class _MixedProtocolClient:
    """datasets/data-fields paged, operators unpaged — the real platform shape."""

    def _page(self, rows, params):
        offset, limit = int(params["offset"]), int(params["limit"])
        return {"count": len(rows), "results": rows[offset : offset + limit]}

    def list_datasets(self, params):
        return self._page([{"id": "pv1"}], params)

    def list_data_fields(self, params):
        return self._page([{"id": "close"}], params)

    def list_operators(self, params):  # noqa: ARG002 - unpaged: no offset/limit
        return list(OPERATOR_ROWS)


def test_sync_consumes_unpaged_operators_and_writes_all_three_caches(tmp_path: Path) -> None:
    """TEST 2: a mixed-protocol platform must produce all three cache files."""
    from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

    result = PlatformCatalogSynchronizer(tmp_path, page_size=1).sync(
        _MixedProtocolClient(), region="USA", universe="TOP3000", delay=1
    )

    assert result == {"datasets": 1, "data_fields": 1, "operators": 2}
    operators = json.loads((tmp_path / ".alpha_operators_cache.json").read_text(encoding="utf-8"))
    assert operators["operators"] == ["rank", "ts_delta"]
    assert operators["source"] == "platform_catalog"
    assert operators["region"] == "USA"
    # arity comes from `definition` because the live payload carries no arity key.
    by_name = {record["name"]: record for record in operators["records"]}
    assert by_name["rank"]["arity"] == 1
    assert by_name["ts_delta"]["arity"] == 2
    assert (tmp_path / ".alpha_datasets_cache.json").is_file()
    assert (tmp_path / ".alpha_datafields_cache.json").is_file()


def test_sync_still_accepts_paged_operators_object(tmp_path: Path) -> None:
    """Catalog-level backwards compatibility for the older paged contract."""

    class PagedOperators(_MixedProtocolClient):
        def list_operators(self, params):  # noqa: ARG002
            return {"count": len(OPERATOR_ROWS), "results": list(OPERATOR_ROWS)}

    from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

    result = PlatformCatalogSynchronizer(tmp_path, page_size=1).sync(
        PagedOperators(), region="USA", universe="TOP3000", delay=1
    )

    assert result["operators"] == 2


def test_sync_fails_closed_on_empty_operators_array(tmp_path: Path) -> None:
    """An empty operators payload must abort rather than cache zero operators."""

    class NoOperators(_MixedProtocolClient):
        def list_operators(self, params):  # noqa: ARG002
            return []

    from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

    with pytest.raises(ValueError):
        PlatformCatalogSynchronizer(tmp_path, page_size=1).sync(
            NoOperators(), region="USA", universe="TOP3000", delay=1
        )

    assert not (tmp_path / ".alpha_operators_cache.json").exists()


def test_sync_fails_closed_on_malformed_operators_element(tmp_path: Path) -> None:
    """A non-object operator element must abort the sync, leaving no cache."""

    class BadOperators(_MixedProtocolClient):
        def list_operators(self, params):  # noqa: ARG002
            return [OPERATOR_ROWS[0], "rank"]

    from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

    with pytest.raises(ValueError):
        PlatformCatalogSynchronizer(tmp_path, page_size=1).sync(
            BadOperators(), region="USA", universe="TOP3000", delay=1
        )

    assert not (tmp_path / ".alpha_operators_cache.json").exists()
