from __future__ import annotations

from tools.ops.audit_generation_quality import (
    _claim_contradiction,
    _sample_size_passes,
)


def test_audit_rejects_revision_leg_without_revision_field() -> None:
    row = {"economic_rationale": "A revision leg confirms the signal."}

    assert _claim_contradiction(row, {"fundamental_quality"}, {"ts_rank"}) is True


def test_audit_requires_requested_fresh_sample_size() -> None:
    assert _sample_size_passes(9, 10) is False
    assert _sample_size_passes(10, 10) is True
