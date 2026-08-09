"""The draft step must be told the legal grouping axes it is required to use.

The candidate prompt recommends "a peer-group neutralization" as a valid shape
whenever a ``group_*`` operator is whitelisted, and simultaneously declares
``allowed_fields`` / ``allowed_operators`` to be COMPLETE whitelists. But a
legal grouping axis (``sector``, ``industry``, ``subindustry``, ``market``,
``country``) is not a catalog field, so it is never in ``allowed_fields``:
``fields_to_use`` is drawn from a single dataset key.

That is self-contradictory. The model is told to build a peer-group shape, told
every token must come from the whitelist, and the only legal axis token is
absent from that whitelist. The one shape it can write is
``group_neutralize(<matrix>, rank(<allowed_field>))`` -- which the live platform
refuses as ``InvalidArgumentType { expected: Group, actual: Matrix }``.

``_suppressible_scope_issue`` is the proof the axes are meant to be legal
out-of-whitelist tokens: it deliberately tolerates a GROUPS label that would
otherwise be UNKNOWN_FIELD / FIELD_DATASET_MISMATCH. The design expects them;
the draft prompt never disclosed them.

Same defect shape as the two rules already fixed, whose code comments name it:
the window rule was stated only in the plan prompt, and ``field_type`` had to be
disclosed in the draft step "too". A rule the gate enforces on the expression
THIS step writes has to be visible to THIS step. The repair prompt already
discloses it ("group_labels are grouping arguments, not fields"); the draft
prompt, which writes the first expression, gets neither the vocabulary nor the
type rule.
"""

from __future__ import annotations

import json
from pathlib import Path

from alpha_mining.domain.operator_registry import GROUPS
from alpha_mining.generation.high_quality import HighQualityGenerator
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

_OPERATORS = ("rank", "ts_delta", "ts_std_dev", "divide", "group_neutralize")


def _field(field_id: str, dataset_id: str, field_type: str = "MATRIX") -> FieldMetadata:
    return FieldMetadata(
        field_id=field_id,
        dataset_id=dataset_id,
        field_type=field_type,
        category=dataset_id,
        description=f"{field_id} description",
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


def _payload(operators: list[str]) -> dict:
    return json.loads(
        HighQualityGenerator._candidate_prompt(
            _snapshots(),
            [_Seed("rank(mat_one)")],
            _Knowledge(),
            {"fields_to_use": ["mat_one", "mat_two"], "operators_to_use": operators},
        )
    )


def test_candidate_prompt_supplies_the_legal_group_axes() -> None:
    """The vocabulary itself, not just the instruction to group."""

    payload = _payload(["group_neutralize", "ts_delta", "rank"])

    assert payload["group_labels"] == sorted(GROUPS)


def test_candidate_prompt_states_the_group_slot_type_rule() -> None:
    """Disclosure alongside enforcement, as the VECTOR and window rules required."""

    rule = " ".join(_payload(["group_neutralize", "ts_delta", "rank"])["candidate_requirements"])

    # The axes are legal despite being absent from allowed_fields -- without
    # this the two whitelist statements make the recommended shape unbuildable.
    assert "group_labels" in rule
    assert "allowed_fields" in rule
    # The type rule: the slot takes an axis, never a field or an expression.
    assert "subindustry" in rule
    assert "rank(" in rule, "must show the refused shape, not only describe it"


def test_group_axes_are_not_offered_without_a_grouping_operator() -> None:
    """Same closed-whitelist discipline the vec_* disclosure already follows.

    Naming an axis the plan cannot use would trade one rejection for a scope
    violation, which is what offering group_neutralize unconditionally already
    did once.
    """

    payload = _payload(["ts_delta", "rank", "divide"])

    assert "group_labels" not in payload
    rule = " ".join(payload["candidate_requirements"])
    assert "subindustry" not in rule


def test_disclosed_axes_match_what_the_gate_accepts() -> None:
    """A disclosed axis the validator would reject is worse than no disclosure.

    ``_suppressible_scope_issue`` only tolerates a token that is in GROUPS, so
    the prompt must offer exactly GROUPS -- no more, no less.
    """

    from alpha_mining.generation.high_quality import _group_axis_identifiers, _suppressible_scope_issue
    from alpha_mining.generation.validation import ValidationIssue

    for label in _payload(["group_neutralize", "rank"])["group_labels"]:
        expression = f"group_neutralize(rank(mat_one), {label})"
        axes = _group_axis_identifiers(expression)
        assert label in axes, f"{label} is not recognised as an axis by the gate"
        assert _suppressible_scope_issue(ValidationIssue("UNKNOWN_FIELD", label), axes) is True, (
            f"the gate would reject disclosed axis {label} as an unknown field"
        )
