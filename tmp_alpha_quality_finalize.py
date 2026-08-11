"""Add compact platform-evidence clusters to the temporary pilot report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "tmp_alpha_quality_pilot_report_fresh.json"


def main() -> int:
    from alpha_mining.generation.snapshots import load_local_snapshots
    from alpha_mining.generation.v50_kernel import V50Kernel
    import tmp_alpha_quality_pilot as pilot

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    rows = [*report.get("round1", []), *report.get("round2", [])]
    snapshots = load_local_snapshots(root=pilot.VAL_ROOT, catalog_dir=pilot.VAL_ROOT, database=pilot.PROD_DB)
    snapshots, _ = pilot._bounded_v50_snapshots(snapshots)
    batch = V50Kernel(seed_pool_size=24).generate_batch(snapshots)
    lineage = {
        str(getattr(candidate, "expression", "")): {
            "research_family": str(getattr(candidate, "family", "") or "v50"),
            "candidate_source": str(getattr(candidate, "source", "") or "v50"),
        }
        for candidate in batch.candidates
    }
    for row in rows:
        row.update(lineage.get(row.get("expression", ""), {"research_family": "v50", "candidate_source": "v50"}))
    checks = Counter()
    dimensions = Counter()
    for row in rows:
        metrics = row.get("metrics") or {}
        if float(metrics.get("sharpe", -999)) < 1.58:
            dimensions["LOW_SHARPE"] += 1
        if float(metrics.get("fitness", -999)) < 1.0:
            dimensions["LOW_FITNESS"] += 1
        for check in row.get("checks") or []:
            if str(check.get("result") or "").upper() in {"FAIL", "ERROR"}:
                checks[str(check.get("name") or "UNKNOWN")] += 1
        field_types = {str(item.get("type") or "UNKNOWN") for item in row.get("fields") or []}
        for field_type in field_types:
            dimensions[f"FIELD_TYPE_{field_type}"] += 1
    dimensions.update({f"CHECK_{key}": value for key, value in checks.items()})
    report["platform_failure_clusters"] = dict(sorted(dimensions.items()))
    report["quality_distribution"] = dict(Counter(str(row.get("quality_status") or "UNKNOWN") for row in rows))
    report["best_alpha"] = max(
        rows,
        key=lambda row: float((row.get("metrics") or {}).get("sharpe", -999)),
    )
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "platform_failure_clusters": report["platform_failure_clusters"], "quality_distribution": report["quality_distribution"], "best_alpha_id": report["best_alpha"].get("alpha_id")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
