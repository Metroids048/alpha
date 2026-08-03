from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from alpha_mining.generation.screening import CandidateScreeningPolicy, RejectionReason
from alpha_mining.generation.validation import LocalExpressionValidator
from alpha_mining.offline.metadata import (
    DatasetMetadata,
    FieldMetadata,
    MetadataCache,
    OperatorMetadata,
)
from alpha_mining.platform.client import PlatformReadError
from alpha_mining.platform.gateway import PlatformGateway


def _metadata(*, dataset_id: str = "ds_price", region: str = "USA") -> MetadataCache:
    return MetadataCache(
        cache_dir=None,  # The validator only needs immutable metadata in this unit test.
        operators={"rank": OperatorMetadata("rank", "rank(x)", 1, "")},
        fields={"close": FieldMetadata("close", dataset_id, "MATRIX", "price", "")},
        datasets={dataset_id: DatasetMetadata(dataset_id, dataset_id, "price_volume")},
        info={
            "fetched_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "region": region,
            "universe": "TOP3000",
            "delay": 1,
        },
    )


def test_validator_rejects_field_from_another_expected_dataset() -> None:
    validator = LocalExpressionValidator(_metadata())

    issues = validator.validate("rank(close)", expected_dataset_id="ds_fundamental")

    assert {issue.code for issue in issues} == {"FIELD_DATASET_MISMATCH"}


def test_validator_rejects_stale_or_wrong_simulation_context() -> None:
    validator = LocalExpressionValidator(_metadata(region="USA"), max_age_hours=0.00001)

    stale = validator.validate("rank(close)", expected_dataset_id="ds_price")
    wrong_context = LocalExpressionValidator(_metadata()).validate(
        "rank(close)",
        expected_dataset_id="ds_price",
        region="CHN",
        universe="TOP3000",
        delay=1,
    )

    assert {issue.code for issue in stale} == {"CATALOG_STALE"}
    assert {issue.code for issue in wrong_context} == {"CATALOG_CONTEXT_MISMATCH"}


def test_screening_fails_closed_without_read_only_catalog() -> None:
    policy = CandidateScreeningPolicy()

    result = policy.screen_expression(
        "rank(close)", round_seen_hashes=set(), round_seen_skeletons=set(), expected_dataset_id="ds_price"
    )

    assert result == RejectionReason.CATALOG_UNAVAILABLE


def test_screening_uses_catalog_before_identity_or_platform_claim() -> None:
    policy = CandidateScreeningPolicy(
        catalog=LocalExpressionValidator(_metadata()),
        expected_dataset_id="ds_price",
        region="USA",
        universe="TOP3000",
        delay=1,
    )

    result = policy.screen_expression("rank(unknown_field)", round_seen_hashes=set(), round_seen_skeletons=set())

    assert result == RejectionReason.UNKNOWN_FIELD


def test_gateway_error_body_is_sanitized_and_bounded() -> None:
    gateway = object.__new__(PlatformGateway)
    gateway.client = SimpleNamespace(
        request=lambda *args, **kwargs: SimpleNamespace(
            status_code=400,
            text="bad request Cookie=secret-token " + "x" * 700,
        )
    )

    with pytest.raises(PlatformReadError) as raised:
        gateway.patch_alpha("alpha-id", {"description": "safe"})

    message = str(raised.value)
    assert "secret-token" not in message
    assert "Cookie" not in message
    assert len(message) <= 600
