"""PERF-001 / PERF-002: v50 preflight identifier catalog must be normalized once.

The v50 ExpressionFactory validates thousands of raw candidates per generation
round.  ``PreflightValidator._unknown_identifiers`` used to rebuild the entire
lowercased field-name set on every single call, which on the full 89k-field
catalog turns one generation round into hundreds of millions of redundant
string operations.  These tests pin the caching behaviour and prove that the
unknown-identifier gate keeps its exact semantics.
"""

from __future__ import annotations

import auto_alpha_pipeline_rebuilt_v50 as v50


class CountingIds:
    """Set-like container that records how many times it is fully iterated."""

    def __init__(self, values) -> None:
        self._values = tuple(values)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return iter(self._values)

    def __contains__(self, item) -> bool:
        return item in self._values

    def __len__(self) -> int:
        return len(self._values)


def _catalog(ids, base_vars=None) -> "v50.FieldCatalog":
    """Build a FieldCatalog without iterating the tracked containers."""
    catalog = v50.FieldCatalog(
        df=None,
        ids=ids,
        by_ds={"ds1": []},
        fund=[],
        analyst=[],
        model=[],
        sent=[],
        pv=[],
        other=[],
    )
    if base_vars is not None:
        catalog.base_vars = base_vars
    return catalog


def test_perf_001_catalog_ids_normalized_once_across_validations() -> None:
    ids = CountingIds(["field_1", "field_2", "field_3"])
    validator = v50.PreflightValidator(_catalog(ids))

    for name in ("field_1", "field_2", "field_3"):
        assert validator.validate(f"group_neutralize(ts_zscore({name},63),market)") == (True, "ok")

    assert ids.iterations == 1


def test_perf_001_base_vars_normalized_once_across_validations() -> None:
    base_vars = CountingIds(sorted(v50.BASE_VARS))
    validator = v50.PreflightValidator(_catalog(CountingIds(["field_1"]), base_vars=base_vars))

    for _ in range(3):
        assert validator.validate("group_neutralize(ts_zscore(close,63),market)") == (True, "ok")

    assert base_vars.iterations == 1


def test_perf_002_identifier_gate_semantics_unchanged() -> None:
    validator = v50.PreflightValidator(_catalog(CountingIds(["field_1"])))

    # Catalog field, case-insensitive catalog field, base var, group axis and
    # locally assigned name all stay legal.
    assert validator.validate("group_neutralize(ts_zscore(field_1,63),market)") == (True, "ok")
    assert validator.validate("group_neutralize(ts_zscore(FIELD_1,63),market)") == (True, "ok")
    assert validator.validate("group_neutralize(ts_zscore(close,63),market)") == (True, "ok")
    assert validator.validate("group_neutralize(ts_zscore(field_1,63),sector)") == (True, "ok")
    assert validator.validate(
        "tmp_a=ts_zscore(field_1,63);group_neutralize(tmp_a,market)"
    ) == (True, "ok")

    # An off-catalog field is still rejected with the same reason code.
    assert validator.validate("group_neutralize(ts_zscore(mystery_field,63),market)") == (
        False,
        "unknown_variable:mystery_field",
    )


def test_perf_002_assigned_names_do_not_leak_between_expressions() -> None:
    """A cached allow-list must not absorb one expression's local variables."""
    validator = v50.PreflightValidator(_catalog(CountingIds(["field_1"])))

    assert validator.validate(
        "tmp_a=ts_zscore(field_1,63);group_neutralize(tmp_a,market)"
    ) == (True, "ok")

    assert validator.validate("group_neutralize(ts_zscore(tmp_a,63),market)") == (
        False,
        "unknown_variable:tmp_a",
    )


def test_perf_002_module_level_identifier_sets_not_mutated() -> None:
    """Caching must not write through to the shared FUNCTIONS / GROUPS sets."""
    functions_before = set(v50.FUNCTIONS)
    groups_before = set(v50.GROUPS)
    base_vars_before = set(v50.BASE_VARS)

    validator = v50.PreflightValidator(_catalog(CountingIds(["field_1"])))
    validator.validate("tmp_a=ts_zscore(field_1,63);group_neutralize(tmp_a,market)")

    assert set(v50.FUNCTIONS) == functions_before
    assert set(v50.GROUPS) == groups_before
    assert set(v50.BASE_VARS) == base_vars_before


def test_perf_002_missing_catalog_still_skips_identifier_gate() -> None:
    validator = v50.PreflightValidator(None)

    assert validator.validate("group_neutralize(ts_zscore(anything_at_all,63),market)") == (
        True,
        "ok",
    )
