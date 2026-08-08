"""Pairwise similarity audit of the pending queue. Writes a file; no reliance on stdout."""

import csv
import json
from itertools import combinations

from alpha_mining.domain.expression_normalization import (
    behavior_signature,
    extract_fields,
    extract_functions,
    normalized_expression,
)
from alpha_mining.generation.high_quality import _similarity

CYCLE_CEILING = 0.65
HISTORY_CEILING = 0.72

rows = list(csv.DictReader(open("待提交Alpha列表.csv", encoding="utf-8-sig")))
pending = [r for r in rows if r.get("queue_status") == "PENDING_SIMULATION"]

report: dict[str, object] = {
    "csv_rows": len(rows),
    "pending": len(pending),
    "expressions": [r["expression"] for r in pending],
}

pairs = []
for a, b in combinations(pending, 2):
    ea, eb = a["expression"], b["expression"]
    sim = _similarity(ea, eb)
    ta = behavior_signature(ea).split("::", 1)[-1]
    tb = behavior_signature(eb).split("::", 1)[-1]
    pairs.append({
        "sim": round(sim, 4),
        "a": ea,
        "b": eb,
        "same_topology": ta == tb,
        "same_normalized": normalized_expression(ea) == normalized_expression(eb),
        "fields_a": sorted(extract_fields(ea)),
        "fields_b": sorted(extract_fields(eb)),
        "ops_a": sorted(set(extract_functions(ea))),
        "ops_b": sorted(set(extract_functions(eb))),
        "above_cycle_ceiling": sim >= CYCLE_CEILING,
        "above_history_ceiling": sim >= HISTORY_CEILING,
    })

pairs.sort(key=lambda item: -item["sim"])
report["total_pairs"] = len(pairs)
report["pairs_at_or_above_cycle_ceiling"] = sum(1 for p in pairs if p["above_cycle_ceiling"])
report["pairs_at_or_above_history_ceiling"] = sum(1 for p in pairs if p["above_history_ceiling"])
report["max_sim"] = pairs[0]["sim"] if pairs else None
report["top_pairs"] = pairs[:15]

# Distinct-topology count: the real acceptance measure.
topologies = {behavior_signature(r["expression"]).split("::", 1)[-1] for r in pending}
normalized = {normalized_expression(r["expression"]) for r in pending}
report["distinct_topologies"] = len(topologies)
report["distinct_normalized"] = len(normalized)

with open("_pairwise_report.json", "w", encoding="utf-8") as handle:
    json.dump(report, handle, ensure_ascii=False, indent=2)
