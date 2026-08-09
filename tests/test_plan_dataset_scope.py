"""Plan scope checks: dataset grouping in the prompt and honest issue codes.

PLAN_CROSS_DATASET must mean "fields from two datasets" and nothing else. It
previously also fired on an empty dataset set, so plans whose fields did not
resolve at all were counted as cross-dataset mixes and the rejection histogram
used for diagnosis was wrong.
"""

import json
from pathlib import Path

from alpha_mining.generation.high_quality import HighQualityGenerator
from alpha_mining.generation.snapshots import CandidateInventory, InventoryRecord, LocalSnapshots
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


def test_plan_rejects_the_most_occupied_dataset_when_three_are_available() -> None:
    snapshots = _snapshots()
    fields = dict(snapshots.catalog.fields)
    fields["gamma_one"] = _field("gamma_one", "ds_gamma")
    datasets = dict(snapshots.catalog.datasets)
    datasets["ds_gamma"] = DatasetMetadata(dataset_id="ds_gamma", name="ds_gamma", category="g")
    catalog = MetadataCache(
        cache_dir=Path("."), operators=snapshots.catalog.operators, fields=fields,
        datasets=datasets, info=snapshots.catalog.info,
    )
    occupied = InventoryRecord(
        ref_id="pending", candidate_id="pending", request_hash="pending",
        expression="rank(alpha_one)", queue_status="PENDING_SIMULATION", family="rank",
        dataset="ds_alpha", data_fields=("alpha_one",),
    )
    scoped = LocalSnapshots(
        catalog=catalog, catalog_dir=Path("."), catalog_source="test", catalog_age_hours=0,
        feedback=snapshots.feedback, inventory=CandidateInventory(records=(occupied,)),
    )

    issues = HighQualityGenerator._plan_issues(
        _plan(fields_to_use=["alpha_one"]), scoped, set(fields), _REFS
    )

    assert "PLAN_DATASET_CONCENTRATION" in issues


def _many_dataset_snapshots(count: int) -> tuple[LocalSnapshots, set[str]]:
    """A catalog with ``count`` datasets and zero pending candidates."""
    fields = {
        f"field_{index:03d}": _field(f"field_{index:03d}", f"ds_{index:03d}")
        for index in range(count)
    }
    datasets = {
        f"ds_{index:03d}": DatasetMetadata(
            dataset_id=f"ds_{index:03d}", name=f"ds_{index:03d}", category="c",
        )
        for index in range(count)
    }
    catalog = MetadataCache(
        cache_dir=Path("."),
        operators={
            name: OperatorMetadata(name=name, signature=f"{name}(x)", arity=1, description=name)
            for name in ("rank", "ts_delta", "ts_zscore", "divide")
        },
        fields=fields, datasets=datasets,
        info={"region": "USA", "universe": "TOP3000", "source": "test"},
    )
    return (
        LocalSnapshots(
            catalog=catalog, catalog_dir=Path("."), catalog_source="test", catalog_age_hours=0.0,
            feedback=FeedbackSummary(
                records=(), positive=(), near_pass=(), failures=(), self_corr_risk=(),
                failure_counts={},
            ),
            inventory=CandidateInventory(records=()),
        ),
        set(fields),
    )


def test_unoccupied_dataset_is_not_a_concentration_fault() -> None:
    """The gate exists to refuse *crowding*, not to mandate one arbitrary dataset.

    Priority is ``sorted(key=(occupancy, dataset))``.  On a fresh queue every
    dataset sits at occupancy 0, so ``priority[0]`` degrades to "the
    alphabetically first dataset" -- ``acquisition_model`` out of the real 297.
    Demanding exactly that one rejects 296 equally uncrowded datasets and,
    because PLAN_DATASET_CONCENTRATION is not locally groundable, aborts the
    whole cycle with zero candidates.
    """
    snapshots, allowed = _many_dataset_snapshots(297)

    # Not the alphabetically first dataset, but equally unoccupied.
    issues = HighQualityGenerator._plan_issues(
        _plan(fields_to_use=["field_150"]), snapshots, allowed, _REFS
    )

    assert "PLAN_DATASET_CONCENTRATION" not in issues
    assert issues == ()


def test_concentration_still_refuses_a_dataset_above_the_minimum() -> None:
    """One pending candidate must still push its dataset out of contention."""
    snapshots, allowed = _many_dataset_snapshots(297)
    occupied = InventoryRecord(
        ref_id="pending", candidate_id="pending", request_hash="pending",
        expression="rank(field_150)", queue_status="PENDING_SIMULATION", family="rank",
        dataset="ds_150", data_fields=("field_150",),
    )
    scoped = LocalSnapshots(
        catalog=snapshots.catalog, catalog_dir=Path("."), catalog_source="test",
        catalog_age_hours=0.0, feedback=snapshots.feedback,
        inventory=CandidateInventory(records=(occupied,)),
    )

    crowded = HighQualityGenerator._plan_issues(
        _plan(fields_to_use=["field_150"]), scoped, allowed, _REFS
    )
    assert "PLAN_DATASET_CONCENTRATION" in crowded

    # Any other dataset is still at the minimum and remains acceptable.
    free = HighQualityGenerator._plan_issues(
        _plan(fields_to_use=["field_151"]), scoped, allowed, _REFS
    )
    assert "PLAN_DATASET_CONCENTRATION" not in free


def _wide_snapshots(datasets: int, fields_each: int) -> LocalSnapshots:
    fields = {
        f"f_{d:03d}_{i:02d}": _field(f"f_{d:03d}_{i:02d}", f"ds_{d:03d}")
        for d in range(datasets)
        for i in range(fields_each)
    }
    catalog = MetadataCache(
        cache_dir=Path("."),
        operators={
            name: OperatorMetadata(name=name, signature=f"{name}(x)", arity=1, description=name)
            for name in ("rank", "ts_delta", "ts_zscore", "divide", "group_neutralize")
        },
        fields=fields,
        datasets={
            f"ds_{d:03d}": DatasetMetadata(
                dataset_id=f"ds_{d:03d}", name=f"ds_{d:03d}", category="c",
            )
            for d in range(datasets)
        },
        info={"region": "USA", "universe": "TOP3000", "source": "test"},
    )
    return LocalSnapshots(
        catalog=catalog, catalog_dir=Path("."), catalog_source="test", catalog_age_hours=0.0,
        feedback=FeedbackSummary(
            records=(), positive=(), near_pass=(), failures=(), self_corr_risk=(),
            failure_counts={},
        ),
        inventory=CandidateInventory(records=()),
    )


def test_research_prompt_stays_inside_the_model_context() -> None:
    """The prompt must be bounded by a global budget, not only per dataset.

    Measured against the real 297-dataset / 89768-field catalog on 2026-08-09:
    a 40-field per-dataset quota with no global cap produced 8680 visible
    fields and a 1,211,624-char prompt -- about 303k tokens against a 64k
    context.  ``catalog.fields_by_dataset`` alone was 99.2% of it, and because
    ``knowledge``, ``v50_seeds`` and ``plan_requirements`` serialize after it,
    the endpoint silently truncated exactly the blocks whose absence shows up
    as HALLUCINATED_KNOWLEDGE_REF and PLAN_CROSS_DATASET.
    """
    snapshots = _wide_snapshots(297, 40)

    prompt = HighQualityGenerator._research_prompt(snapshots, [], _Knowledge(), "cycle-1")
    payload = json.loads(prompt)

    # 64k-token context, ~4 chars/token, leaving room for the schema and reply.
    assert len(prompt) < 200_000, f"prompt is {len(prompt)} chars"
    # The instruction blocks must survive serialization, not be cut off.
    assert payload["plan_requirements"]
    assert payload["knowledge"]
    assert payload["v50_seeds"] == []


def test_visible_datasets_are_capped_but_each_keeps_a_full_quota() -> None:
    """A shallow view of every dataset cannot express an alpha; rotate instead."""
    snapshots = _wide_snapshots(297, 40)

    allowed = HighQualityGenerator._research_field_ids(snapshots, [])
    by_dataset: dict[str, int] = {}
    for field in allowed:
        by_dataset[snapshots.catalog.fields[field].dataset_id] = (
            by_dataset.get(snapshots.catalog.fields[field].dataset_id, 0) + 1
        )

    assert len(by_dataset) < 297, "the whole catalog cannot fit in one prompt"
    assert len(by_dataset) >= 3, "the concentration gate needs at least three datasets"
    # Every advertised dataset must be deep enough to build an expression from.
    assert min(by_dataset.values()) >= 20, by_dataset


def test_dataset_visibility_rotates_as_candidates_accumulate() -> None:
    """Occupancy must move the window, so the catalog is reachable over cycles."""
    snapshots = _wide_snapshots(297, 40)
    first = HighQualityGenerator._research_field_ids(snapshots, [])
    first_datasets = {snapshots.catalog.fields[f].dataset_id for f in first}

    # Fill every dataset in the first window.
    pending = tuple(
        InventoryRecord(
            ref_id=f"p{index}", candidate_id=f"p{index}", request_hash=f"p{index}",
            expression=f"rank({dataset}_x)", queue_status="PENDING_SIMULATION", family="rank",
            dataset=dataset, data_fields=(),
        )
        for index, dataset in enumerate(sorted(first_datasets))
    )
    occupied = LocalSnapshots(
        catalog=snapshots.catalog, catalog_dir=Path("."), catalog_source="test",
        catalog_age_hours=0.0, feedback=snapshots.feedback,
        inventory=CandidateInventory(records=pending),
    )

    second = HighQualityGenerator._research_field_ids(occupied, [])
    second_datasets = {occupied.catalog.fields[f].dataset_id for f in second}

    assert second_datasets != first_datasets
    assert not (second_datasets & first_datasets), "a filled dataset must yield its slot"


def test_seed_fields_stay_in_scope_even_outside_the_window() -> None:
    """A plan must still be able to reference its own parent seeds."""
    snapshots = _wide_snapshots(297, 40)

    class _Seed:
        expression = "rank(f_296_39)"

    allowed = HighQualityGenerator._research_field_ids(snapshots, [_Seed()])

    assert "f_296_39" in allowed


def test_queue_datasets_column_is_decoded_into_inventory() -> None:
    """The queue stores ``datasets`` as a JSON array, so it must be decoded.

    ``InventoryRecord.dataset`` was taken verbatim from the CSV column, giving
    ``'["analyst9"]'`` where every consumer compares against a bare dataset id.
    Measured on the validation queue: 5 pending rows on ``analyst9`` and an
    occupancy of 0 for it -- so both the visibility rotation and the
    concentration gate were blind to work already queued.  The database path
    already decodes the same column via ``_dataset_value``.
    """
    from alpha_mining.generation.snapshots import load_candidate_inventory

    loaded = load_candidate_inventory(
        [
            {
                "request_hash": "h1", "candidate_id": "c1", "expression": "rank(f_001_00)",
                "queue_status": "PENDING_SIMULATION", "operator_family": "rank",
                "datasets": '["analyst9"]', "data_fields": '["f_001_00"]',
            },
            # A bare value must survive untouched, and an empty one stay empty.
            {
                "request_hash": "h2", "candidate_id": "c2", "expression": "rank(f_002_00)",
                "queue_status": "PENDING_SIMULATION", "operator_family": "rank",
                "datasets": "analyst10", "data_fields": "[]",
            },
            {
                "request_hash": "h3", "candidate_id": "c3", "expression": "rank(f_003_00)",
                "queue_status": "PENDING_SIMULATION", "operator_family": "rank",
                "datasets": "", "data_fields": "[]",
            },
        ]
    )

    # Records come back sorted by ref_id, so compare by candidate.
    decoded = {item.candidate_id: item.dataset for item in loaded.records}
    assert decoded == {"c1": "analyst9", "c2": "analyst10", "c3": ""}

    catalog = _wide_snapshots(4, 2).catalog
    snapshots = LocalSnapshots(
        catalog=catalog, catalog_dir=Path("."), catalog_source="test", catalog_age_hours=0.0,
        feedback=FeedbackSummary(
            records=(), positive=(), near_pass=(), failures=(), self_corr_risk=(),
            failure_counts={},
        ),
        inventory=CandidateInventory(
            records=(
                InventoryRecord(
                    ref_id="r1", candidate_id="c1", request_hash="h1",
                    expression="rank(f_001_00)", queue_status="PENDING_SIMULATION",
                    family="rank", dataset="ds_001", data_fields=(),
                ),
            )
        ),
    )

    allowed = HighQualityGenerator._research_field_ids(snapshots, [])
    occupancy = HighQualityGenerator._dataset_occupancy(snapshots, allowed)

    assert occupancy["ds_001"] == 1, occupancy
    assert HighQualityGenerator._research_dataset_priority(snapshots, allowed)[-1] == "ds_001"


def test_group_axis_is_not_a_cross_dataset_data_draw() -> None:
    """A grouping keyword is a partition axis, not a field drawn from a dataset.

    On the live catalog ``sector``, ``industry``, ``subindustry``, ``market`` and
    ``country`` all exist as real fields of ``pv1``.  So ``group_rank(x, sector)``
    with a plan on any other dataset was refused as FIELD_DATASET_MISMATCH --
    while plan_requirements simultaneously instructs the model to include a
    grouping operator.  That contradiction rejected every grouped candidate on
    296 of the catalog's 297 datasets.
    """
    from alpha_mining.generation.high_quality import _group_axis_identifiers

    expression = "group_rank(ts_delta(alpha_one, 21), sector)"

    assert _group_axis_identifiers(expression) == {"sector"}
    # A group keyword used as an ordinary operand is still a data draw.
    assert _group_axis_identifiers("ts_delta(sector, 21)") == set()
    # Only the axis position of a group_* call counts, not its value argument.
    assert _group_axis_identifiers("group_rank(sector, industry)") == {"industry"}


def test_group_axis_mismatch_is_suppressed_but_real_mismatch_is_not() -> None:
    """The suppression must be positional, not a blanket pardon for the name."""
    from alpha_mining.generation.high_quality import _suppressible_scope_issue
    from alpha_mining.generation.validation import ValidationIssue

    axes = {"sector"}
    mismatch = ValidationIssue(
        "FIELD_DATASET_MISMATCH", "sector belongs to pv1, expected analyst_base_ref"
    )
    unknown = ValidationIssue("UNKNOWN_FIELD", "sector")
    other = ValidationIssue(
        "FIELD_DATASET_MISMATCH", "beta_one belongs to ds_beta, expected ds_alpha"
    )

    assert _suppressible_scope_issue(mismatch, axes)
    assert _suppressible_scope_issue(unknown, axes)
    # A genuine second dataset must still be refused.
    assert not _suppressible_scope_issue(other, axes)
    # And a group keyword that is NOT in an axis position must not be pardoned.
    assert not _suppressible_scope_issue(mismatch, set())


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
