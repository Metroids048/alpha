"""VECTOR fields are event streams and must be reduced before any other operator.

The live catalog is 16144 VECTOR fields against 71556 MATRIX ones, and the
generation layer never read ``FieldMetadata.field_type`` at all. So the model was
handed event-stream fields with nothing marking them as such, and 7 of the 10
field usages in the first fresh batch were VECTOR fed straight into time-series
operators. Every local gate passed; the platform refused the simulation with
``Operator ts_std_dev does not support event inputs.``

A VECTOR field is legal -- but only wrapped in a ``vec_*`` reducer, which is what
collapses the per-instrument event stream to one value per day. So the rule is
positional: a VECTOR identifier's immediate parent must be a ``vec_*`` call.
"""

import json
from pathlib import Path

from alpha_mining.generation.high_quality import (
    HighQualityGenerator,
    _unreduced_vector_fields,
)
from alpha_mining.generation.snapshots import (
    CandidateInventory,
    FeedbackSummary,
    LocalSnapshots,
)
from alpha_mining.offline.metadata import (
    DatasetMetadata,
    FieldMetadata,
    MetadataCache,
    OperatorMetadata,
)


def _field(field_id: str, dataset_id: str, field_type: str = "MATRIX") -> FieldMetadata:
    return FieldMetadata(
        field_id=field_id,
        dataset_id=dataset_id,
        field_type=field_type,
        category=dataset_id,
        description=f"{field_id} description",
    )


_OPERATORS = (
    "rank", "ts_delta", "ts_zscore", "ts_std_dev", "divide",
    "vec_avg", "vec_sum", "group_neutralize",
)


def _snapshots() -> LocalSnapshots:
    catalog = MetadataCache(
        cache_dir=Path("."),
        operators={
            name: OperatorMetadata(name=name, signature=f"{name}(x)", arity=1, description=name)
            for name in _OPERATORS
        },
        fields={
            "mat_one": _field("mat_one", "ds_alpha"),
            "mat_two": _field("mat_two", "ds_alpha"),
            "evt_one": _field("evt_one", "ds_alpha", "VECTOR"),
            "evt_two": _field("evt_two", "ds_alpha", "VECTOR"),
            "grp_one": _field("grp_one", "ds_alpha", "GROUP"),
        },
        datasets={
            "ds_alpha": DatasetMetadata(dataset_id="ds_alpha", name="ds_alpha", category="a"),
        },
        info={"region": "USA", "universe": "TOP3000", "source": "test"},
    )
    return LocalSnapshots(
        catalog=catalog,
        catalog_dir=Path("."),
        catalog_source="test",
        catalog_age_hours=0.0,
        feedback=FeedbackSummary(
            records=(), positive=(), near_pass=(), failures=(),
            self_corr_risk=(), failure_counts={},
        ),
        inventory=CandidateInventory(records=()),
    )


class _Snippet:
    def __init__(self, ref_id: str) -> None:
        self.ref_id = ref_id
        self.text = "knowledge"


class _Knowledge:
    snippets = (_Snippet("ref-1"),)


class _Seed:
    def __init__(self, expression: str) -> None:
        self.expression = expression


def test_bare_vector_field_in_a_time_series_operator_is_unreduced() -> None:
    """The exact shape the platform refused, reduced to test fields.

    ``ts_std_dev(evt_one, 126) / evt_two`` is two separate faults: the event
    stream reaches ``ts_std_dev`` directly, and the second one reaches a divide.
    """

    expression = (
        "group_neutralize(ts_zscore(ts_std_dev(evt_one, 126) / evt_two, 126), grp_one)"
    )

    assert _unreduced_vector_fields(expression, _snapshots().catalog) == ("evt_one", "evt_two")


def test_vector_field_wrapped_in_a_reducer_is_accepted() -> None:
    """A ``vec_*`` reducer collapses the event stream, so the field becomes legal."""

    catalog = _snapshots().catalog

    assert _unreduced_vector_fields("ts_std_dev(vec_avg(evt_one), 126)", catalog) == ()
    assert _unreduced_vector_fields("vec_sum(evt_one) / vec_avg(evt_two)", catalog) == ()


def test_matrix_and_group_fields_are_never_flagged() -> None:
    """Only event streams need reducing; a grouping axis is not a vector."""

    catalog = _snapshots().catalog

    assert _unreduced_vector_fields("group_neutralize(ts_delta(mat_one, 21), grp_one)", catalog) == ()
    assert _unreduced_vector_fields("rank(mat_one / mat_two)", catalog) == ()


def test_reduction_must_be_immediate() -> None:
    """A reducer somewhere up the tree does not help.

    ``vec_avg(ts_delta(evt_one, 21))`` still hands the raw event stream to
    ``ts_delta``, which is the operator that refuses it. Only the immediate
    parent counts.
    """

    catalog = _snapshots().catalog

    assert _unreduced_vector_fields("vec_avg(ts_delta(evt_one, 21))", catalog) == ("evt_one",)
    # Arithmetic is an operator too: it consumes the stream before the reducer.
    assert _unreduced_vector_fields("vec_avg(evt_one / evt_two)", catalog) == ("evt_one", "evt_two")


def test_draft_gate_refuses_an_unreduced_vector_candidate() -> None:
    """End to end: the gate must reject before the expression reaches the platform."""

    generator = HighQualityGenerator(llm=object(), kernel=object())
    snapshots = _snapshots()
    seeds = [_Seed("rank(mat_one)")]
    plan = {
        "fields_to_use": ["evt_one"],
        "operators_to_use": ["ts_std_dev", "vec_avg"],
        "knowledge_refs": ["ref-1"],
    }
    row = {
        "expression": "ts_std_dev(evt_one, 126)",
        "knowledge_refs": ["ref-1"],
        "feedback_patterns_used": [],
        "parent_seed": "rank(mat_one)",
    }

    verdict = generator._validate_candidate(
        row, plan, snapshots, seeds, _Knowledge(), set(), set(), [],
    )

    assert verdict == "VECTOR_FIELD_NOT_REDUCED"

    # The wrapped form must clear this specific gate.
    row["expression"] = "ts_std_dev(vec_avg(evt_one), 126)"
    assert generator._validate_candidate(
        row, plan, snapshots, seeds, _Knowledge(), set(), set(), [],
    ) != "VECTOR_FIELD_NOT_REDUCED"


def test_research_prompt_discloses_field_type_and_the_reduction_rule() -> None:
    """Enforcement without disclosure is a gate the model cannot satisfy.

    ``field_type`` was absent from the payload entirely, so a rule about VECTOR
    fields would reject candidates for a property the model could not observe.
    """

    payload = json.loads(
        HighQualityGenerator._research_prompt(_snapshots(), [], _Knowledge(), "cycle-1")
    )

    entries = {
        entry["id"]: entry for entry in payload["catalog"]["fields_by_dataset"]["ds_alpha"]
    }
    assert entries["evt_one"]["field_type"] == "VECTOR"
    assert entries["mat_one"]["field_type"] == "MATRIX"

    rule = " ".join(payload["plan_requirements"])
    assert "VECTOR" in rule and "vec_" in rule


def test_candidate_prompt_names_the_vector_fields_and_the_reducer() -> None:
    """The gate runs on the expression this step writes, so the rule belongs here.

    Exactly the defect the window rule had: it was stated only in the plan
    prompt, so the model that actually writes the expression never saw it.
    """

    payload = json.loads(
        HighQualityGenerator._candidate_prompt(
            _snapshots(),
            [_Seed("rank(mat_one)")],
            _Knowledge(),
            {
                "fields_to_use": ["evt_one", "mat_one"],
                "operators_to_use": ["ts_std_dev", "vec_avg"],
            },
        )
    )

    assert payload["vector_fields_requiring_reduction"] == ["evt_one"]
    rule = " ".join(payload["candidate_requirements"])
    assert "vec_avg" in rule and "VECTOR_FIELD_NOT_REDUCED" in rule


def test_candidate_prompt_forbids_vector_fields_when_no_reducer_is_whitelisted() -> None:
    """operators_to_use is a closed whitelist, so an unreducible field is unusable.

    Telling the model to wrap in a reducer it is not allowed to use would trade
    VECTOR_FIELD_NOT_REDUCED for PLAN_SCOPE_VIOLATION.
    """

    payload = json.loads(
        HighQualityGenerator._candidate_prompt(
            _snapshots(),
            [_Seed("rank(mat_one)")],
            _Knowledge(),
            {
                "fields_to_use": ["evt_one", "mat_one"],
                "operators_to_use": ["ts_std_dev", "rank"],
            },
        )
    )

    rule = " ".join(payload["candidate_requirements"])
    assert "cannot be used at all" in rule
    assert "vec_avg" not in rule, "must not recommend an operator outside the whitelist"


def test_plan_selecting_a_vector_field_must_whitelist_a_reducer() -> None:
    """operators_to_use is closed, so a VECTOR field without a reducer is a dead plan.

    Measured on the live catalog: the least-loaded dataset ``acquisition_model``
    is 15 VECTOR fields out of 15. A plan there that omits a ``vec_*`` reducer
    leaves the expression step structurally unable to write anything legal, and
    every candidate died as VECTOR_FIELD_NOT_REDUCED.
    """

    issues = HighQualityGenerator._plan_issues(
        {
            "research_direction": "d", "hypothesis": "h", "economic_mechanism": "m",
            "anti_correlation_plan": "a", "expected_turnover_behavior": "t",
            "fields_to_use": ["evt_one", "evt_two"],
            "operators_to_use": ["ts_std_dev"],
            "knowledge_refs": ["ref-1"],
        },
        _snapshots(),
        {"evt_one", "evt_two", "mat_one"},
        {"ref-1"},
    )

    assert "PLAN_VECTOR_WITHOUT_REDUCER" in issues


def test_plan_with_a_reducer_or_only_matrix_fields_is_clean() -> None:
    def _issues(fields: list[str], operators: list[str]) -> tuple[str, ...]:
        return HighQualityGenerator._plan_issues(
            {
                "research_direction": "d", "hypothesis": "h", "economic_mechanism": "m",
                "anti_correlation_plan": "a", "expected_turnover_behavior": "t",
                "fields_to_use": fields,
                "operators_to_use": operators,
                "knowledge_refs": ["ref-1"],
            },
            _snapshots(),
            {"evt_one", "evt_two", "mat_one", "mat_two"},
            {"ref-1"},
        )

    assert "PLAN_VECTOR_WITHOUT_REDUCER" not in _issues(
        ["evt_one"], ["ts_std_dev", "vec_avg"]
    )
    assert "PLAN_VECTOR_WITHOUT_REDUCER" not in _issues(
        ["mat_one", "mat_two"], ["ts_std_dev"]
    )


def test_vector_reducer_gap_is_locally_groundable() -> None:
    """Adding a catalog reducer to a whitelist invents nothing, so it must not abort.

    PLAN_DATASET_CONCENTRATION already proved the cost of the alternative: an
    issue outside this set discards the whole cycle with zero candidates.
    """
    from alpha_mining.generation.high_quality import _LOCALLY_GROUNDABLE_PLAN_ISSUES

    assert "PLAN_VECTOR_WITHOUT_REDUCER" in _LOCALLY_GROUNDABLE_PLAN_ISSUES


def test_local_grounding_adds_a_reducer_for_a_vector_plan() -> None:
    """The deterministic repair must close the gap without an LLM round trip."""

    grounded = HighQualityGenerator._locally_ground_plan(
        {
            "research_direction": "d", "hypothesis": "h", "economic_mechanism": "m",
            "anti_correlation_plan": "a", "expected_turnover_behavior": "t",
            "fields_to_use": ["evt_one"],
            "operators_to_use": ["ts_std_dev"],
            "knowledge_refs": ["ref-1"],
        },
        _snapshots(),
        [_Seed("rank(mat_one)")],
        {"evt_one", "mat_one"},
        {"ref-1"},
    )

    operators = [str(item) for item in grounded["operators_to_use"]]
    assert any(item.startswith("vec_") for item in operators), operators
