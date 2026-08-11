"""Run exactly the remaining bounded Round 2 of the temporary pilot."""

from __future__ import annotations

import json
from pathlib import Path

import tmp_alpha_quality_pilot as pilot


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "tmp_alpha_quality_pilot_report_fresh.json"


def main() -> int:
    from alpha_mining.common import load_workspace_env
    from alpha_mining.generation.snapshots import load_local_snapshots
    from alpha_mining.generation.v50_kernel import V50Kernel
    from alpha_mining.platform.gateway import PlatformGateway

    load_workspace_env(ROOT / ".env")
    prior = json.loads(REPORT.read_text(encoding="utf-8"))
    round1 = list(prior.get("round1") or [])
    if len(round1) != 12:
        raise RuntimeError(f"round 2 requires exactly 12 completed round-1 records, got {len(round1)}")
    if any(str(row.get("quality_status")) in {"READY_TO_SUBMIT", "NEAR_PASS"} for row in round1):
        raise RuntimeError("round 2 is forbidden after READY_TO_SUBMIT or NEAR_PASS")
    snapshots = load_local_snapshots(root=pilot.VAL_ROOT, catalog_dir=pilot.VAL_ROOT, database=pilot.PROD_DB)
    snapshots, catalog_slice = pilot._bounded_v50_snapshots(snapshots)
    batch = V50Kernel(seed_pool_size=24).generate_batch(snapshots)
    rows = pilot._candidate_rows(batch, snapshots)
    seen = {str(row.get("expression") or "") for row in round1}
    rows = [row for row in rows if row["expression"] not in seen]
    settings = pilot._settings()
    gateway = PlatformGateway(
        state_path=pilot.AUTH_STATE,
        database=pilot.PROD_DB,
        lock_path=pilot.LOCK,
        min_interval=3.0,
        timeout=60.0,
        poll_interval=3.0,
        max_poll_seconds=600.0,
        settings_schema_path=pilot.SETTINGS_SCHEMA,
    )
    round2, status = pilot._simulate_rows(2, rows, settings, gateway, 12)
    prior["round2"] = round2
    prior["catalog_slice"] = catalog_slice
    all_rows = [*round1, *round2]
    prior["failure_clusters"] = pilot._cluster(all_rows)
    prior["round2_reason"] = "native v50 rerun after Round 1 platform evidence; no production code adjustment"
    prior["status"] = status or (
        "QUALITY_RECOVERY_FAILED_WITH_PLATFORM_EVIDENCE"
        if len(round2) == 12
        else "BLOCKED_EXTERNAL"
    )
    prior["observed_at"] = pilot._utc()
    REPORT.write_text(json.dumps(prior, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": prior["status"], "round1": len(round1), "round2": len(round2), "clusters": prior["failure_clusters"]}, ensure_ascii=False))
    return 0 if prior["status"] == "QUALITY_RECOVERY_FAILED_WITH_PLATFORM_EVIDENCE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
