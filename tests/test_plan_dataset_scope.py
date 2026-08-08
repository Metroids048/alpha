"""Plan scope checks: dataset grouping in the prompt and honest issue codes.

PLAN_CROSS_DATASET must mean "fields from two datasets" and nothing else. It
previously also fired on an empty dataset set, so plans whose fields did not
resolve at all were counted as cross-dataset mixes and the rejection histogram
used for diagnosis was wrong.
"""

import json
from pathlib import Path

from alpha_mining.generation.high_quality import HighQualityGenerator
from alpha_mining.generation.snapshots import CandidateInventory, LocalSnapshots
from alpha_mining.generation.snapshots import FeedbackSummary
from alpha_mining.offline.metadata import (
    DatasetMetadata,
    FieldMetadata,
    MetadataCache,
    OperatorMetadata,
)


def _field(field_id: str, dataset_id: str) -> FieldMetadata:
    return FieldMetadata(
        field_id=field_id,
        dataset_id=dataset_id,
        field_type="MATRIX",
        category=dataset_id,
        description=f"{field_id} description",
    )


def _snapshots() -> LocalSnapshots:
    catalog = MetadataCache(
        cache_dir=Path("."),
        operators={
            name: OperatorMetadata(name=name, signature=f"{name}(x)", arity=1, description=name)
            for name in ("rank", "ts_delta", "ts_zscore", "divide")
        },
        fields={
            "alpha_one": _field("alpha_one", "ds_alpha"),
            "alpha_two": _field("alpha_two", "ds_alpha"),
            "beta_one": _field("beta_one", "ds_beta"),
        },
        datasets={
            "ds_alpha": DatasetMetadata(dataset_id="ds_alpha", name="ds_alpha", category="a"),
            "ds_beta": DatasetMetadata(dataset_id="ds_beta", name="ds_beta", category="b"),
        },
        info={"region": "USA", "universe": "TOP3000", "source": "test"},
    )
    return LocalSnapshots(
        catalog=catalog,
        catalog_dir=Path("."),
        catalog_source="test",
        catalog_age_hours=0.0,
        feedback=FeedbackSummary(
            records=(),
            positive=(),
            near_pass=(),
            failures=(),
            self_corr_risk=(),
            failure_counts={},
        ),
        inventory=CandidateInventory(records=()),
    )


_ALLOWED = {"alpha_one", "alpha_two", "beta_one"}
_REFS = {"ref-1"}


def _plan(**overrides: object) -> dict[str, object]:
    plan = {
        "research_direction": "quality",
        "hypothesis": "quality persists",
        "economic_mechanism": "slow diffusion",
        "expected_horizon": "medium",
        "anti_correlation_plan": "distinct field",
        "expected_turnover_behavior": "low",
        "fields_to_use": ["alpha_one", "alpha_two"],
        "operators_to_use": ["rank"],
        "knowledge_refs": ["ref-1"],
    }
    plan.update(overrides)
    return plan


def test_single_dataset_plan_has_no_issue() -> None:
    issues = HighQualityGenerator._plan_issues(_plan(), _snapshots(), _ALLOWED, _REFS)

    assert issues == ()


def test_two_datasets_are_reported_as_cross_dataset() -> None:
    issues = HighQualityGenerator._plan_issues(
        _plan(fields_to_use=["alpha_one", "beta_one"]), _snapshots(), _ALLOWED, _REFS
    )

    assert "PLAN_CROSS_DATASET" in issues


def test_unresolvable_fields_are_not_reported_as_cross_dataset() -> None:
    """An empty dataset set is an unknown-field fault, not a dataset mix."""

    issues = HighQualityGenerator._plan_issues(
        _plan(fields_to_use=["no_such_field"]), _snapshots(), _ALLOWED, _REFS
    )

    assert "PLAN_UNKNOWN_FIELD" in issues
    assert "PLAN_CROSS_DATASET" not in issues


def test_empty_fields_are_not_reported_as_cross_dataset() -> None:
    issues = HighQualityGenerator._plan_issues(
        _plan(fields_to_use=[]), _snapshots(), _ALLOWED, _REFS
    )

    assert "PLAN_UNKNOWN_FIELD" in issues
    assert "PLAN_CROSS_DATASET" not in issues


def test_research_prompt_groups_fields_by_dataset() -> None:
    """The one-dataset rule must be visible in the payload's shape."""

    payload = json.loads(
        HighQualityGenerator._research_prompt(_snapshots(), [], _Knowledge(), "cycle-1")
    )
    catalog = payload["catalog"]

    assert "fields" not in catalog, "a flat field list hides the dataset choice"
    grouped = catalog["fields_by_dataset"]
    assert set(grouped) == {"ds_alpha", "ds_beta"}
    assert [entry["id"] for entry in grouped["ds_alpha"]] == ["alpha_one", "alpha_two"]
    assert [entry["id"] for entry in grouped["ds_beta"]] == ["beta_one"]
    # The dataset lives in the key, so it must not be repeated per record.
    assert all("dataset" not in entry for entries in grouped.values() for entry in entries)
    # Advertised datasets must be exactly those with a selectable field.
    assert catalog["datasets"] == sorted(grouped)


def test_research_prompt_names_the_grouped_structure_in_requirements() -> None:
    payload = json.loads(
        HighQualityGenerator._research_prompt(_snapshots(), [], _Knowledge(), "cycle-1")
    )

    assert any("fields_by_dataset" in item for item in payload["plan_requirements"])


def test_candidate_prompt_does_not_offer_grouping_when_whitelist_lacks_one() -> None:
    """Offering peer-group neutralization the whitelist cannot express is a trap."""

    plan = _plan(operators_to_use=["rank", "ts_delta"])
    payload = json.loads(
        HighQualityGenerator._candidate_prompt(_snapshots(), [], _Knowledge(), plan)
    )
    requirements = " ".join(payload["candidate_requirements"])

    assert "peer-group neutralization is unavailable" in requirements
    assert "or a peer-group neutralization." not in requirements


class _Snippet:
    def __init__(self, ref_id: str) -> None:
        self.ref_id = ref_id
        self.text = "knowledge text"


class _Knowledge:
    snippets = (_Snippet("ref-1"),)
