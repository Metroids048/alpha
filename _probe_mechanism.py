"""Instrumented probe: capture why real candidates hit MECHANISM_OPERATOR_MISMATCH.

Runs one real cycle with the gates wrapped so the exact claimed-vs-extracted
operator sets are recorded. Read-only with respect to gate behaviour: every
wrapper returns the original verdict unchanged.
"""

import json

from alpha_mining.domain.expression_normalization import extract_functions
from alpha_mining.generation import high_quality as hq
from alpha_mining.generation.production import main

_ARITH = {"/": "divide", "*": "multiply", "+": "add", "-": "subtract"}
records: list[dict] = []

_orig_mechanism = hq._mechanism_issue
_orig_degenerate = hq._degenerate_shape


def _mechanism_issue(row, expression, fields, functions, snapshots):
    verdict = _orig_mechanism(row, expression, fields, functions, snapshots)
    if verdict:
        operator_roles = row.get("operator_roles")
        claimed = sorted(
            str(item.get("operator") or "").strip().lower()
            for item in (operator_roles if isinstance(operator_roles, list) else [])
            if isinstance(item, dict) and str(item.get("role") or "").strip()
        )
        symbols = sorted({name for sym, name in _ARITH.items() if sym in str(expression)})
        records.append({
            "gate": verdict,
            "expression": str(expression),
            "claimed_operators": claimed,
            "extracted_functions": sorted(functions),
            "extra_claims": sorted(set(claimed) - set(functions)),
            "missing_claims": sorted(set(functions) - set(claimed)),
            "arith_symbols_present": symbols,
            "roles_completed": bool(row.get("_mechanism_roles_completed")),
        })
    return verdict


def _degenerate_shape(expression, fields):
    verdict = _orig_degenerate(expression, fields)
    if verdict:
        records.append({
            "gate": "DEGENERATE_SHAPE",
            "expression": str(expression),
            "fields": sorted(fields),
            "extracted_functions": sorted(set(extract_functions(expression))),
            "arith_symbols_present": sorted(
                {name for sym, name in _ARITH.items() if sym in str(expression)}
            ),
        })
    return verdict


hq._mechanism_issue = _mechanism_issue
hq._degenerate_shape = _degenerate_shape

code = main(["--once", "--offline-catalog-max-age-hours", "10000"])

print("\n===== PROBE: %d gate hits =====" % len(records))
for item in records:
    print(json.dumps(item, ensure_ascii=False))

extra_only = [
    r for r in records
    if r["gate"] == "MECHANISM_OPERATOR_MISMATCH"
    and not r["missing_claims"]
    and set(r["extra_claims"]) <= set(r["arith_symbols_present"])
]
print("\nmismatches explained purely by arithmetic symbols: %d / %d" % (
    len(extra_only),
    sum(1 for r in records if r["gate"] == "MECHANISM_OPERATOR_MISMATCH"),
))
print("exit=%s" % code)
