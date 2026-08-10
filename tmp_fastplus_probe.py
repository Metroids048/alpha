"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT

Is the FASTPLUS type gate actually armed in this interpreter?

alpha_mining/generation/validation.py:60-62 runs the FastPlus type check only
when ``fp.available`` is true, and fastplus_gate.check_expression reports
available=False on a bare ImportError.  So a process without py-fastplus
installed silently downgrades to the Python AST walk, which knows arity and
field names but nothing about Group vs Matrix.  This probe reports, per
interpreter, whether that downgrade is in effect.
"""

from __future__ import annotations

import sys

from alpha_mining.parser.fastplus_gate import check_expression

# The row the frozen pipeline enqueued at 12:11:10 (40c7dc4e4be5017b).
LEAKED = (
    "group_neutralize(ts_std_dev(ts_delta(eps_y1_estimate_skewness,21),63),"
    "rank(eps_y2_consensus_value))"
)
# Same shape with a real GROUP axis, as a control.
CONTROL = (
    "group_neutralize(ts_std_dev(ts_delta(eps_y1_estimate_skewness,21),63),"
    "subindustry)"
)

print(f"sys.executable = {sys.executable}")
print(f"sys.version    = {sys.version.split()[0]}")
try:
    import fastplus

    print(f"fastplus       = IMPORTED {getattr(fastplus, '__version__', '?')}")
except ImportError as exc:
    print(f"fastplus       = IMPORT_FAILED {exc}")

for label, expr in (("LEAKED", LEAKED), ("CONTROL", CONTROL)):
    fp = check_expression(expr)
    print(f"\n[{label}] available={fp.available} ok={fp.ok}")
    print(f"  gate_fires = {fp.available and not fp.ok}")
    diag = " ".join(str(fp.diagnostic).split())
    print(f"  diagnostic = {diag[:200]}")
