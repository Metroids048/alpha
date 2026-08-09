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


# ---------------------------------------------------------------------------
# LOCAL_CANONICAL_ARITY
#
# OperatorMetadata carries a single ``arity: int`` and LocalExpressionValidator
# tests ``len(node.children) == operator.arity`` exactly, so the model cannot
# express optional, keyword or variadic argument ranges.  Canonical arity is
# therefore defined as the smallest certain purely-positional call form.
#
# Measured live 2026-08-09 (USA/TOP3000/delay=1): /operators returns 82 records
# with no ``arity`` key at all, and 11 definitions defeat a strict
# ``name(args)`` match.  Under the old parser every one of them returned None
# and sync aborted at "platform operator metadata has no verifiable arity" --
# discarding a complete 297-dataset catalog over operator documentation prose.
# ---------------------------------------------------------------------------

# Verbatim live definitions.
LIVE_ADD = "add(x, y, filter = false), x + y"
LIVE_MULTIPLY = "multiply(x ,y, ... , filter=false), x * y"
LIVE_BUCKET = (
    'bucket(rank(x), range=“0, 1, 0.1”, skipBoth=False, NaNGroup=False)\r\n'
    'or\r\nbucket(rank(x), buckets = “2,5,6,7,10”, skipBoth=False, NaNGroup=False)'
)
LIVE_LESS_EQUAL = "input1 <= input2"


def test_canonical_arity_ignores_defaulted_parameter_and_trailing_prose() -> None:
    """add(x, y, filter = false), x + y -> 2, never 3.

    Counting ``filter = false`` would make the legal ``add(x, y)`` fail the
    exact-equality INVALID_ARITY check, i.e. a false positive rejection.
    """
    from alpha_mining.platform.catalog import _arity_from_signature

    assert _arity_from_signature(LIVE_ADD) == 2


def test_canonical_arity_of_variadic_definition_is_the_binary_form() -> None:
    """multiply(x ,y, ... , filter=false) -> 2.

    The platform may accept N-ary multiply; locally only the binary form is
    admitted. Rejecting a 3-arg multiply is a safe false negative.
    """
    from alpha_mining.platform.catalog import _arity_from_signature

    assert _arity_from_signature(LIVE_MULTIPLY) == 2


def test_canonical_arity_survives_nested_calls_and_quoted_commas() -> None:
    """bucket(rank(x), range=“0, 1, 0.1”, ...) has one positional argument.

    Raw comma counting would report seven: three inside the quoted range, one
    inside rank(x), and the top-level separators.
    """
    from alpha_mining.platform.catalog import _arity_from_signature

    assert _arity_from_signature(LIVE_BUCKET) == 1


def test_infix_only_definition_is_not_guessed() -> None:
    """input1 <= input2 has no verifiable function-call shape."""
    from alpha_mining.platform.catalog import _arity_from_signature, _normalise_operator_record

    assert _arity_from_signature(LIVE_LESS_EQUAL) is None
    assert _normalise_operator_record({"name": "less_equal", "definition": LIVE_LESS_EQUAL}) is None


@pytest.mark.parametrize(
    "definition",
    ["", "   ", "no parens and no operator", "(x, y)", "f(x, y"],
)
def test_unverifiable_definitions_still_fail_closed(definition) -> None:
    from alpha_mining.platform.catalog import _arity_from_signature

    assert _arity_from_signature(definition) is None


def test_existing_definitions_keep_their_canonical_arity() -> None:
    """Live definitions that already parsed must not shift meaning."""
    from alpha_mining.platform.catalog import _arity_from_signature

    assert _arity_from_signature("rank(x)") == 1
    assert _arity_from_signature("ts_delta(x, d)") == 2
    assert _arity_from_signature("signed_power(x, y)") == 2
    assert _arity_from_signature("group_neutralize(x, group)") == 2
    assert _arity_from_signature("ts_zscore(x, d)") == 2
    # Defaulted tails drop out: these are the smallest certain positional forms.
    assert _arity_from_signature("rank(x, rate=2)") == 1
    assert _arity_from_signature("ts_rank(x, d, constant = 0)") == 2
    assert _arity_from_signature("winsorize(x, std=4)") == 1
    assert _arity_from_signature("scale(x, scale=1, longscale=1, shortscale=1)") == 1
    assert _arity_from_signature("ts_regression(y, x, d, lag = 0, rettype = 0)") == 3


def test_unrepresentable_operator_is_excluded_without_failing_the_sync(tmp_path: Path) -> None:
    """A complete 297-dataset catalog must not die over one infix operator."""
    from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

    class LiveShapedOperators(_MixedProtocolClient):
        def list_operators(self, params):  # noqa: ARG002
            return [
                {"name": "add", "definition": LIVE_ADD},
                {"name": "multiply", "definition": LIVE_MULTIPLY},
                {"name": "less_equal", "definition": LIVE_LESS_EQUAL},
                {"name": "rank", "definition": "rank(x)"},
            ]

    result = PlatformCatalogSynchronizer(tmp_path, page_size=1).sync(
        LiveShapedOperators(), region="USA", universe="TOP3000", delay=1
    )

    assert result["operators"] == 3
    operators = json.loads((tmp_path / ".alpha_operators_cache.json").read_text(encoding="utf-8"))
    # Excluded from the usable set, so the validator treats it as UNKNOWN_OPERATOR.
    assert operators["operators"] == ["add", "multiply", "rank"]
    assert "less_equal" not in operators["operators"]
    by_name = {record["name"]: record for record in operators["records"]}
    assert sorted(by_name) == ["add", "multiply", "rank"]
    assert by_name["add"]["arity"] == 2
    assert by_name["multiply"]["arity"] == 2
    # Transparency: the exclusion is recorded, not silently swallowed.
    assert operators["excluded_unrepresentable"] == ["less_equal"]


def test_sync_fails_closed_when_no_operator_is_representable(tmp_path: Path) -> None:
    """Excluding unrepresentable operators must never yield an empty set."""
    from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

    class AllInfix(_MixedProtocolClient):
        def list_operators(self, params):  # noqa: ARG002
            return [
                {"name": "less_equal", "definition": "input1 <= input2"},
                {"name": "greater", "definition": "input1 > input2"},
            ]

    with pytest.raises(ValueError):
        PlatformCatalogSynchronizer(tmp_path, page_size=1).sync(
            AllInfix(), region="USA", universe="TOP3000", delay=1
        )

    assert not (tmp_path / ".alpha_operators_cache.json").exists()
