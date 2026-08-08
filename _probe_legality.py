"""Independent legality + gate re-verification of the pending queue. Writes a file."""

import csv
import json
import re

from alpha_mining.domain.expression_normalization import extract_fields, extract_functions
from alpha_mining.generation.high_quality import _GHOST_OPERATORS, _degenerate_shape, _has_short_window
from alpha_mining.generation.snapshots import load_local_snapshots
from alpha_mining.generation.validation import LocalExpressionValidator

snapshots = load_local_snapshots(root=".", allow_partial_offline=True, offline_max_age_hours=10_000)
catalog = snapshots.catalog
rows = list(csv.DictReader(open("待提交Alpha列表.csv", encoding="utf-8-sig")))
pending = [r for r in rows if r.get("queue_status") == "PENDING_SIMULATION"]

validator = LocalExpressionValidator(catalog, allow_stale_catalog=True)
results = []
for row in pending:
    expression = row["expression"]
    fields = sorted(extract_fields(expression))
    functions = sorted(set(extract_functions(expression)))
    unknown_fields = [f for f in fields if f not in catalog.fields]
    unknown_ops = [f for f in functions if f not in catalog.operators]
    ghosts = [f for f in functions if f in _GHOST_OPERATORS]
    datasets = sorted({catalog.fields[f].dataset_id for f in fields if f in catalog.fields})
    windows = [int(w) for _, w in re.findall(r"\b(ts_[a-z_]+)\([^)]*?,\s*(\d+)\s*\)", expression.lower())]
    issues = [i.code for i in validator.validate(expression, expected_dataset_id=datasets[0] if datasets else None)]
    results.append({
        "expression": expression,
        "quality": row.get("local_quality_score"),
        "status": row.get("queue_status"),
        "fields": fields,
        "operators": functions,
        "datasets": datasets,
        "unknown_fields": unknown_fields,
        "unknown_operators": unknown_ops,
        "ghost_operators": ghosts,
        "cross_dataset": len(datasets) != 1,
        "min_window": min(windows) if windows else None,
        "short_window": _has_short_window(expression),
        "degenerate": _degenerate_shape(expression, tuple(fields)),
        "validator_issues": issues,
        "legal": not (unknown_fields or unknown_ops or ghosts or len(datasets) != 1
                      or _has_short_window(expression) or _degenerate_shape(expression, tuple(fields))),
    })

report = {
    "pending": len(pending),
    "all_legal": all(r["legal"] for r in results),
    "illegal_count": sum(1 for r in results if not r["legal"]),
    "cross_dataset_count": sum(1 for r in results if r["cross_dataset"]),
    "short_window_count": sum(1 for r in results if r["short_window"]),
    "degenerate_count": sum(1 for r in results if r["degenerate"]),
    "unknown_field_count": sum(1 for r in results if r["unknown_fields"]),
    "unknown_operator_count": sum(1 for r in results if r["unknown_operators"]),
    "with_validator_issues": sum(1 for r in results if r["validator_issues"]),
    "datasets_used": sorted({d for r in results for d in r["datasets"]}),
    "min_window_overall": min([r["min_window"] for r in results if r["min_window"] is not None], default=None),
    "quality_scores": [r["quality"] for r in results],
    "rows": results,
}
with open("_legality_report.json", "w", encoding="utf-8") as handle:
    json.dump(report, handle, ensure_ascii=False, indent=2)
