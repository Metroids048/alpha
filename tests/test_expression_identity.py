from __future__ import annotations


def test_identity_collapses_parameters_fields_wrappers_and_arithmetic_noise() -> None:
    from alpha_mining.domain.expression_normalization import expression_identity

    wrapped = expression_identity("normalize(rank(ts_delta(revenue, 21) + 0) * 1)")
    window_variant = expression_identity("ts_delta(revenue, 63)")
    field_variant = expression_identity("ts_delta(cashflow_op, 63)")

    assert wrapped.parameter_skeleton == window_variant.parameter_skeleton
    assert wrapped.field_skeleton == window_variant.field_skeleton
    assert wrapped.parameter_skeleton != field_variant.parameter_skeleton
    assert wrapped.field_skeleton == field_variant.field_skeleton


def test_identity_sorts_commutative_operands_and_keeps_grouping_controls() -> None:
    from alpha_mining.domain.expression_normalization import expression_identity

    first = expression_identity("ts_delta(revenue, 21) + ts_zscore(cashflow_op, 63)")
    reordered = expression_identity("ts_zscore(cashflow_op, 126) + ts_delta(revenue, 5)")
    neutralized = expression_identity("group_neutralize(ts_delta(revenue, 21), sector)")
    ranked = expression_identity("group_rank(ts_delta(revenue, 21), sector)")

    assert first.parameter_skeleton == reordered.parameter_skeleton
    assert first.field_skeleton == reordered.field_skeleton
    assert neutralized.field_skeleton != ranked.field_skeleton


def test_consultant_generator_does_not_invent_base_fields() -> None:
    from alpha_mining.generator.consultant_generator import ConsultantGenerator

    candidates = ConsultantGenerator().generate(
        hypothesis_id="h1",
        family="fundamental",
        mechanism="profitability surprise",
        horizon="medium",
        fields=("revenue",),
    )

    assert candidates
    assert all("close" not in item.expression and "adv20" not in item.expression for item in candidates)
