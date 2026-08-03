from __future__ import annotations

import re

from alpha_mining.generator.consultant_generator import ConsultantGenerator


def _generate(**overrides):
    values = {
        "hypothesis_id": "hypothesis-1",
        "family": "fundamental",
        "mechanism": "profitability growth",
        "horizon": "medium",
        "fields": ("revenue", "cashflow_op"),
    }
    values.update(overrides)
    return ConsultantGenerator().generate(**values)


def test_generator_uses_secondary_fields() -> None:
    assert any("cashflow_op" in item.expression for item in _generate())


def test_horizon_changes_windows() -> None:
    short = {int(value) for item in _generate(horizon="short") for value in re.findall(r",(\d+)\)", item.expression)}
    long = {int(value) for item in _generate(horizon="long") for value in re.findall(r",(\d+)\)", item.expression)}

    assert short <= {1, 5, 10, 20}
    assert long <= {1, 63, 126, 252}
    assert short != long


def test_mechanism_changes_template_selection() -> None:
    momentum = _generate(mechanism="price momentum trend")
    reversal = _generate(mechanism="contrarian mean reversion")

    assert momentum[0].mutation_type == "medium_horizon_momentum"
    assert reversal[0].mutation_type == "short_horizon_reversal"


def test_distinct_specs_do_not_collapse_to_identical_expressions() -> None:
    first = {item.expression for item in _generate(mechanism="growth momentum", horizon="short")}
    second = {item.expression for item in _generate(mechanism="volatility risk", horizon="long")}

    assert first != second


def test_generation_is_deterministic() -> None:
    assert _generate() == _generate()


def test_generation_remains_bounded() -> None:
    candidates = ConsultantGenerator(max_per_hypothesis=3).generate(
        hypothesis_id="h", family="risk", mechanism="volatility", horizon="long", fields=("a", "b", "c")
    )
    assert len(candidates) == 3


def test_group_rank_remains_disabled() -> None:
    assert all("group_rank" not in item.expression for item in _generate())
