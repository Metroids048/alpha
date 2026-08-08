"""Role gates must not punish arithmetic written as a symbol.

``extract_functions`` matches ``name(`` only, so ``a / b`` yields no function.
The candidate prompt tells the model to write arithmetic as ``+ - * /`` and to
name operators from the catalog, whose entries include divide/subtract. Obeying
both makes ``operator_roles`` a superset of the extracted functions. That was
read as a false claim, and the completion guard then refused to repair the row,
so an honest candidate had no route back.
"""

from pathlib import Path

from alpha_mining.generation.high_quality import (
    _complete_mechanism_roles,
    _mechanism_issue,
    _symbol_operator_names,
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


def _snapshots() -> LocalSnapshots:
    catalog = MetadataCache(
        cache_dir=Path("."),
        operators={
            name: OperatorMetadata(name=name, signature=f"{name}(x)", arity=1, description=name)
            for name in ("rank", "ts_delta", "ts_std_dev", "divide", "subtract")
        },
        fields={
            name: FieldMetadata(
                field_id=name,
                dataset_id="ds_alpha",
                field_type="MATRIX",
                category="ds_alpha",
                description=name,
            )
            for name in ("alpha_one", "alpha_two")
        },
        datasets={"ds_alpha": DatasetMetadata(dataset_id="ds_alpha", name="ds_alpha", category="a")},
        info={"region": "USA", "universe": "TOP3000", "source": "test"},
    )
    return LocalSnapshots(
        catalog=catalog,
        catalog_dir=Path("."),
        catalog_source="test",
        catalog_age_hours=0.0,
        feedback=FeedbackSummary(
            records=(), positive=(), near_pass=(), failures=(), self_corr_risk=(), failure_counts={}
        ),
        inventory=CandidateInventory(records=()),
    )


def _row(operators: list[str], fields: list[str]) -> dict[str, object]:
    return {
        "field_roles": [{"field_id": name, "role": "signal source"} for name in fields],
        "operator_roles": [{"operator": name, "role": "transform"} for name in operators],
        "turnover_controls": ["ts_delta"],
        "correlation_diversifiers": [fields[0]],
        "economic_rationale": "a slow fundamental change scaled by its own volatility",
    }


def test_symbol_operators_detects_binary_arithmetic() -> None:
    assert _symbol_operator_names("ts_delta(a,63) / ts_std_dev(a,126)") == {"divide"}
    assert _symbol_operator_names("rank(a) - rank(b)") == {"subtract"}
    assert _symbol_operator_names("ts_mean(a,63) + ts_mean(b,63)") == {"add"}


def test_symbol_operators_ignores_sign_markers_and_visible_calls() -> None:
    """A negative window is not a subtraction, and divide( is already visible."""

    assert _symbol_operator_names("ts_delta(a,-5)") == set()
    assert _symbol_operator_names("divide(a,b)") == set()
    assert _symbol_operator_names("ts_zscore(a,252)") == set()


def test_symbol_backed_operator_claim_is_accepted() -> None:
    expression = "ts_delta(alpha_one,63) / ts_std_dev(alpha_one,126)"
    row = _row(["ts_delta", "ts_std_dev", "divide"], ["alpha_one"])

    issue = _mechanism_issue(
        row, expression, ("alpha_one",), {"ts_delta", "ts_std_dev"}, _snapshots()
    )

    assert issue == ""


def test_operator_claim_without_any_backing_is_still_refused() -> None:
    """The gate must keep refusing an operator the expression never applies."""

    expression = "ts_delta(alpha_one,63) / ts_std_dev(alpha_one,126)"
    row = _row(["ts_delta", "ts_std_dev", "divide", "rank"], ["alpha_one"])

    issue = _mechanism_issue(
        row, expression, ("alpha_one",), {"ts_delta", "ts_std_dev"}, _snapshots()
    )

    assert issue == "MECHANISM_OPERATOR_MISMATCH"


def test_hiding_a_used_function_is_still_refused() -> None:
    expression = "ts_delta(alpha_one,63) / ts_std_dev(alpha_one,126)"
    row = _row(["ts_delta"], ["alpha_one"])

    issue = _mechanism_issue(
        row, expression, ("alpha_one",), {"ts_delta", "ts_std_dev"}, _snapshots()
    )

    assert issue == "MECHANISM_OPERATOR_MISMATCH"


def test_completion_fills_omitted_entry_despite_symbol_backed_extra() -> None:
    """One symbol-backed claim must not block completion of a genuine omission."""

    expression = "ts_delta(alpha_one,63) / ts_std_dev(alpha_one,126)"
    row = _row(["ts_delta", "divide"], ["alpha_one"])

    completed = _complete_mechanism_roles(
        row,
        ("alpha_one",),
        {"ts_delta", "ts_std_dev"},
        tolerated_operators=_symbol_operator_names(expression),
    )

    claimed = {str(item["operator"]) for item in completed["operator_roles"]}
    assert claimed == {"ts_delta", "ts_std_dev", "divide"}
    assert completed["_mechanism_roles_completed"] is True
    assert _mechanism_issue(
        completed, expression, ("alpha_one",), {"ts_delta", "ts_std_dev"}, _snapshots()
    ) == ""


def test_completion_still_refuses_to_repair_an_unbacked_claim() -> None:
    expression = "ts_delta(alpha_one,63) / ts_std_dev(alpha_one,126)"
    row = _row(["ts_delta", "rank"], ["alpha_one"])

    completed = _complete_mechanism_roles(
        row,
        ("alpha_one",),
        {"ts_delta", "ts_std_dev"},
        tolerated_operators=_symbol_operator_names(expression),
    )

    # Nothing was filled in, so the false claim still reaches the gate.
    assert completed.get("_mechanism_roles_completed") is not True
    assert _mechanism_issue(
        completed, expression, ("alpha_one",), {"ts_delta", "ts_std_dev"}, _snapshots()
    ) == "MECHANISM_OPERATOR_MISMATCH"
