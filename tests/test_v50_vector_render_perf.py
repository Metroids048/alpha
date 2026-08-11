"""PERF-003: _render_vector_fields must not scan the whole catalog per candidate.

The pre-fix implementation looped over every field in ``catalog.field_type``
and ``re.compile``d a pattern for each VECTOR field, for *every* candidate.
With the production catalog (16,144 VECTOR fields out of 89,768) one
generation round of 5k-20k candidates meant 80M-320M regex compilations.

These tests pin the rewrite against a verbatim copy of the old implementation
used as an oracle, so the vector-reduction semantics cannot drift.
"""

from __future__ import annotations

import re

import auto_alpha_pipeline_rebuilt_v50 as v50


def _factory(field_type: dict[str, str]) -> v50.ExpressionFactory:
    catalog = v50.FieldCatalog(
        df=None,
        ids=set(field_type),
        by_ds={"ds1": sorted(field_type)},
        fund=[],
        analyst=[],
        model=[],
        sent=[],
        pv=[],
        other=[],
        field_type=dict(field_type),
    )
    return v50.ExpressionFactory(
        v50.PipelineConfig(username="u", password="p"),
        catalog,
        v50.PreflightValidator(catalog),
    )


def _oracle(factory: v50.ExpressionFactory, expr: str) -> str:
    """Verbatim pre-fix implementation."""
    rendered = v50._sig(expr)
    for field_name, field_type in factory.catalog.field_type.items():
        if field_type != "VECTOR":
            continue
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(field_name)}(?![A-Za-z0-9_])")

        def reduce_vector(match: "re.Match[str]") -> str:
            prefix = rendered[: match.start()]
            if re.search(r"\bvec_[a-z0-9_]+\s*\(\s*$", prefix, flags=re.I):
                return match.group(0)
            return f"vec_avg({field_name})"

        rendered = pattern.sub(reduce_vector, rendered)
    return v50._sig(rendered)


FIELD_TYPE = {
    "evt_flow": "VECTOR",
    "evt_flow_extended": "VECTOR",
    "news_sentiment": "VECTOR",
    "close": "MATRIX",
    "adv20": "MATRIX",
    "mdf_revenue": "MATRIX",
    "sector_code": "GROUP",
}

EXPRESSIONS = (
    # bare VECTOR field -> wrapped
    "group_neutralize(ts_zscore(evt_flow,63),market)",
    # already wrapped -> untouched
    "group_neutralize(ts_zscore(vec_avg(evt_flow),63),market)",
    # other vec_* reducer -> untouched
    "group_neutralize(ts_zscore(vec_sum(evt_flow),63),market)",
    "group_neutralize(ts_zscore(vec_stddev( evt_flow ),63),market)",
    # longest-name / prefix collision
    "group_neutralize(ts_zscore(evt_flow_extended,63),market)",
    "ts_mean(evt_flow,63)/ts_mean(evt_flow_extended,63)",
    # same field twice
    "ts_corr(evt_flow,evt_flow,63)",
    # mixed vector + matrix + group
    "group_neutralize(ts_zscore(evt_flow/adv20,126),sector)",
    # two distinct vector fields
    "ts_mean(evt_flow,63)+ts_mean(news_sentiment,63)",
    # no vector field at all
    "group_neutralize(ts_zscore(mdf_revenue/close,252),market)",
    # substring that must NOT match (word boundary)
    "group_neutralize(ts_zscore(xevt_flowy,63),market)",
    "group_neutralize(ts_zscore(my_evt_flow,63),market)",
    # local assignment form
    "tmp=ts_zscore(evt_flow,63);group_neutralize(tmp,market)",
    # whitespace normalization
    "group_neutralize(  ts_zscore( evt_flow , 63 ) , market )",
    "",
)


def test_perf_003_matches_oracle_on_every_expression() -> None:
    factory = _factory(FIELD_TYPE)
    for expr in EXPRESSIONS:
        assert factory._render_vector_fields(expr) == _oracle(factory, expr), expr


def test_perf_003_known_reductions() -> None:
    factory = _factory(FIELD_TYPE)

    assert factory._render_vector_fields(
        "group_neutralize(ts_zscore(evt_flow,63),market)"
    ) == "group_neutralize(ts_zscore(vec_avg(evt_flow),63),market)"

    # An existing reducer is preserved rather than double-wrapped.
    already = "group_neutralize(ts_zscore(vec_avg(evt_flow),63),market)"
    assert factory._render_vector_fields(already) == already

    # MATRIX fields are never wrapped.
    matrix = "group_neutralize(ts_zscore(close,63),market)"
    assert factory._render_vector_fields(matrix) == matrix

    # Word boundaries: a field name embedded in a longer identifier is left alone.
    embedded = "group_neutralize(ts_zscore(my_evt_flow,63),market)"
    assert factory._render_vector_fields(embedded) == embedded


def test_perf_003_does_not_iterate_catalog_field_type_per_call() -> None:
    """The rewrite must be driven by the expression, not by the catalog size."""

    class CountingFieldType(dict):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.iterations = 0

        def items(self):
            self.iterations += 1
            return super().items()

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    counting = CountingFieldType(FIELD_TYPE)
    factory = _factory(FIELD_TYPE)
    factory.catalog.field_type = counting
    baseline = counting.iterations

    for _ in range(25):
        factory._render_vector_fields("group_neutralize(ts_zscore(evt_flow,63),market)")

    assert counting.iterations == baseline
