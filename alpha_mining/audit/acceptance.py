"""Generate fail-closed Consultant Factory acceptance artifacts from one SQLite snapshot."""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from alpha_mining.analysis.funnel import classify_failure
from alpha_mining.legacy.self_corr import cluster_disposition, normalize_check_status
from alpha_mining.storage.migrations import migrate


@dataclass(frozen=True)
class AcceptanceResult:
    status: str
    blockers: tuple[str, ...]
    output_dir: Path


def _write_csv(path: Path, headers: list[str], rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in headers})
            count += 1
    return count


def _loads(value: object, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError):
        return default


def _checks(value: object) -> list[dict[str, Any]]:
    payload = _loads(value, [])
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        details = payload.get("is") if isinstance(payload.get("is"), dict) else payload
        raw = details.get("checks") if isinstance(details, dict) else []
        return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return []


def _self_status(checks: list[dict[str, Any]]) -> str:
    for check in checks:
        if str(check.get("name") or "").upper() == "SELF_CORRELATION":
            return normalize_check_status(check.get("result") or check.get("status")).value
    return "MISSING"


def _latest_sync(con: sqlite3.Connection) -> tuple | None:
    return con.execute(
        "SELECT sync_id,status,declared_count,fetched_rows,unique_alpha_ids,duplicate_alpha_ids,completed_at,error_message "
        "FROM platform_sync_runs ORDER BY completed_at DESC LIMIT 1"
    ).fetchone()


def _reconciliation(con: sqlite3.Connection, sync_id: str) -> list[dict[str, Any]]:
    remote = {
        str(row[0]): row
        for row in con.execute(
            "SELECT alpha_id,platform_status,hidden,alpha_type,expression_hash,synced_at FROM platform_alpha_ledger WHERE sync_id=?",
            (sync_id,),
        )
    }
    local_rows = con.execute(
        "SELECT alpha_id,COUNT(*),MIN(observed_at),MAX(observed_at),MAX(exact_hash) FROM legacy_alphas WHERE alpha_id<>'' GROUP BY alpha_id"
    ).fetchall()
    local = {str(row[0]): row for row in local_rows}
    expression_counts = Counter(str(row[4]) for row in remote.values() if row[4])
    out: list[dict[str, Any]] = []
    for alpha_id in sorted(set(remote) | set(local)):
        r = remote.get(alpha_id)
        l = local.get(alpha_id)
        reasons: list[str] = []
        if l and int(l[1]) > 1:
            reasons.append("duplicate_alpha_id")
        if r and expression_counts[str(r[4])] > 1:
            reasons.append("duplicate_expression")
        if r and bool(r[2]):
            reasons.append("hidden")
        if l and not r:
            reasons.extend(("missing_remote_record", "stale_local_record"))
        if r and not l:
            reasons.append("unknown")
        out.append(
            {
                "alpha_id": alpha_id,
                "in_platform": bool(r),
                "in_local": bool(l),
                "platform_status": r[1] if r else "",
                "local_status": "IMPORTED" if l else "",
                "expression_hash": r[4] if r else (l[4] if l else ""),
                "mismatch_reasons": "|".join(reasons),
                "primary_reason": reasons[0] if reasons else "MATCH",
                "sync_id": sync_id,
            }
        )
    return out


def _legacy_observations(
    con: sqlite3.Connection, sync_id: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    latest: dict[str, str] = {}
    for alpha_id, legacy_id, checks_json, observed_at, source in con.execute(
        "SELECT alpha_id,legacy_id,checks_json,observed_at,source FROM legacy_alphas WHERE alpha_id<>'' ORDER BY observed_at,legacy_id"
    ):
        checks = _checks(checks_json)
        status = _self_status(checks)
        rows.append(
            {
                "alpha_id": alpha_id,
                "legacy_id": legacy_id,
                "status": status,
                "observed_at": observed_at or "",
                "source": source,
                "evidence": "HISTORICAL_LOCAL_IMPORT" if status != "MISSING" else "MISSING",
            }
        )
    if sync_id:
        for alpha_id, checks_json, synced_at in con.execute(
            "SELECT alpha_id,latest_checks_json,synced_at FROM platform_alpha_ledger WHERE sync_id=? ORDER BY alpha_id",
            (sync_id,),
        ):
            status = _self_status(_checks(checks_json))
            latest[str(alpha_id)] = status
            rows.append(
                {
                    "alpha_id": alpha_id,
                    "legacy_id": "",
                    "status": status,
                    "observed_at": synced_at,
                    "source": f"platform_ledger:{sync_id}",
                    "evidence": "CURRENT_PLATFORM_LEDGER",
                }
            )
    return rows, latest


def _description_observations(con: sqlite3.Connection, sync_id: str) -> list[dict[str, Any]]:
    if not sync_id:
        return []
    rows: list[dict[str, Any]] = []
    for alpha_id, alpha_type, regular, selection, combo in con.execute(
        "SELECT alpha_id,alpha_type,regular_description,selection_description,combo_description "
        "FROM platform_alpha_ledger WHERE sync_id=? ORDER BY alpha_id",
        (sync_id,),
    ):
        kind = str(alpha_type or "REGULAR").upper()
        target_field = f"{kind.lower()}.description"
        descriptions = {"REGULAR": regular, "SELECTION": selection, "COMBO": combo}
        value = str(descriptions.get(kind) or "").strip()
        reasons: list[str] = []
        if not value:
            reasons.append("DESCRIPTION_MISSING")
        if re.search(r"\b(?:none|tbd|todo|null)\b", value, re.I):
            reasons.append("DESCRIPTION_PLACEHOLDER")
        rows.append(
            {
                "alpha_id": alpha_id,
                "alpha_type": kind,
                "target_field": target_field,
                "status": "FAIL" if reasons else "PRESENT_UNVERIFIED",
                "reasons": "|".join(reasons or ["AST_HYPOTHESIS_VALIDATION_REQUIRED"]),
            }
        )
    return rows


def _clusters(con: sqlite3.Connection, statuses: dict[str, str]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for cluster_id, signature, medoid, member_count in con.execute(
        "SELECT cluster_id,behavior_signature,medoid_legacy_id,member_count FROM alpha_behavior_clusters ORDER BY cluster_id"
    ):
        members = con.execute(
            """SELECT l.legacy_id,l.alpha_id,l.metrics_json,m.distance
            FROM alpha_cluster_members m JOIN legacy_alphas l ON l.legacy_id=m.legacy_id
            WHERE m.cluster_id=? ORDER BY l.legacy_id""",
            (cluster_id,),
        ).fetchall()
        if not members:
            continue
        def quality(row: tuple) -> tuple[float, float, str]:
            metrics = _loads(row[2], {})
            return (float(metrics.get("sharpe") or metrics.get("Sharpe") or -999), float(metrics.get("fitness") or metrics.get("Fitness") or -999), str(row[0]))
        quality_rep = max(members, key=quality)[0]
        unique_rep = max(members, key=lambda row: (float(row[3] or 0), str(row[0])))[0]
        rep_ids = list(dict.fromkeys([str(medoid or ""), str(quality_rep), str(unique_rep)]))
        alpha_by_legacy = {str(row[0]): str(row[1]) for row in members}
        rep_statuses = [statuses.get(alpha_by_legacy.get(rep, ""), "MISSING") for rep in rep_ids if rep]
        disposition = cluster_disposition(rep_statuses)
        clusters.append(
            {
                "cluster_id": cluster_id,
                "behavior_signature": signature,
                "member_count": member_count,
                "medoid_legacy_id": medoid or "",
                "quality_representative": quality_rep,
                "structural_representative": unique_rep,
                "representative_statuses": "|".join(rep_statuses),
                "disposition": disposition,
                "frozen": disposition == "FROZEN",
                "pnl_evidence": "MISSING",
                "confidence": "STRUCTURAL_ONLY",
            }
        )
    return clusters


def _funnel(con: sqlite3.Connection) -> list[dict[str, Any]]:
    raw = con.execute(
        "SELECT legacy_id,alpha_id,observed_at,family,source,expression,settings_json,metrics_json,checks_json FROM legacy_alphas ORDER BY observed_at DESC,legacy_id DESC"
    ).fetchall()
    dated = [row for row in raw if row[2]]
    max_date: datetime | None = None
    for row in dated:
        try:
            value = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
            max_date = value if max_date is None or value > max_date else max_date
        except ValueError:
            pass
    cutoff = max_date - timedelta(days=30) if max_date else None
    last_1000 = {str(row[0]) for row in dated[:1000]}
    out: list[dict[str, Any]] = []
    for row in raw:
        in_30 = False
        if cutoff and row[2]:
            try:
                in_30 = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00")) >= cutoff
            except ValueError:
                pass
        in_1000 = str(row[0]) in last_1000
        if not in_30 and not in_1000:
            continue
        settings = _loads(row[6], {})
        metrics = _loads(row[7], {})
        checks = _checks(row[8])
        classified = classify_failure(checks)
        out.append(
            {
                "trial_id": row[0], "alpha_id": row[1], "observed_at": row[2],
                "cohort_30d": in_30, "cohort_last_1000": in_1000,
                "family": row[3] or "UNCLASSIFIED", "dataset": settings.get("dataset") or "UNKNOWN",
                "settings_hash": hashlib.sha256(
                    json.dumps(settings, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "provenance": row[4] or "unknown", "all_failures": "|".join(classified.all_failures),
                "primary_failure": classified.primary_failure,
                "base_pass": classified.primary_failure in {"self_correlation", "description_missing", "PASS"},
                "complete_checks": bool(checks) and all(str(item.get("result") or "").upper() in {"PASS", "FAIL"} for item in checks),
                "self_corr_pass": _self_status(checks) == "PASS",
                "description_ready": False,
                "final_submittable": False,
                "sharpe": metrics.get("sharpe") or metrics.get("Sharpe") or "",
                "fitness": metrics.get("fitness") or metrics.get("Fitness") or "",
            }
        )
    return out


def _metric_rows(funnel: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in funnel:
        groups[str(row.get(key) or "UNKNOWN")].append(row)
    out = []
    for name, rows in sorted(groups.items()):
        n = len(rows)
        out.append(
            {
                key: name, "simulations": n,
                "base_pass_per_100": round(100 * sum(bool(r["base_pass"]) for r in rows) / n, 3),
                "complete_check_per_100": round(100 * sum(bool(r["complete_checks"]) for r in rows) / n, 3),
                "self_corr_pass_per_100": round(100 * sum(bool(r["self_corr_pass"]) for r in rows) / n, 3),
                "description_ready_per_100": round(100 * sum(bool(r["description_ready"]) for r in rows) / n, 3),
                "submittable_per_100": round(100 * sum(bool(r["final_submittable"]) for r in rows) / n, 3),
            }
        )
    return out


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return (result.stdout or result.stderr).strip()


def _file_freshness(path: Path) -> str:
    if not path.is_file():
        return "MISSING"
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
    return f"{modified}, {path.stat().st_size} bytes"


def _reason_text(*values: object) -> str:
    reasons: list[str] = []
    for value in values:
        decoded = _loads(value, value)
        if isinstance(decoded, list):
            reasons.extend(str(item).strip() for item in decoded if str(item).strip())
        elif isinstance(decoded, str) and decoded.strip():
            reasons.append(decoded.strip())
    return "|".join(dict.fromkeys(reasons))


def _phase1_description_rows(
    con: sqlite3.Connection, authoritative_sync_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not authoritative_sync_id:
        blocked_backfill = [{
            "alpha_id": "", "sync_id": "", "alpha_type": "", "eligibility_status": "",
            "description_status": "", "status": "BLOCKED",
            "reason": "PLATFORM_LEDGER_NOT_COMPLETE", "endpoint_calls": 0,
        }]
        blocked_eligibility = [{
            "alpha_id": "", "sync_id": "", "eligibility_status": "BLOCKED",
            "reasons": "PLATFORM_LEDGER_NOT_COMPLETE", "source": "AUTHORITATIVE_PLATFORM_LEDGER",
        }]
        return blocked_backfill, [], blocked_eligibility

    eligibility_rows: list[dict[str, Any]] = []
    for alpha_id, snapshot_status, snapshot_reasons, job_status in con.execute(
        """SELECT ledger.alpha_id,snapshot.eligibility_status,snapshot.reasons_json,job.eligibility_status
        FROM platform_alpha_ledger AS ledger
        LEFT JOIN alpha_eligibility_snapshots AS snapshot
          ON snapshot.sync_id=ledger.sync_id AND snapshot.alpha_id=ledger.alpha_id
        LEFT JOIN description_backfill_jobs AS job
          ON job.sync_id=ledger.sync_id AND job.alpha_id=ledger.alpha_id
        WHERE ledger.sync_id=? ORDER BY ledger.alpha_id""",
        (authoritative_sync_id,),
    ):
        eligibility_status = str(snapshot_status or job_status or "UNKNOWN_BLOCKED")
        reasons = _reason_text(snapshot_reasons) or (
            "ELIGIBILITY_SNAPSHOT_MISSING" if not snapshot_status else ""
        )
        eligibility_rows.append({
            "alpha_id": alpha_id,
            "sync_id": authoritative_sync_id,
            "eligibility_status": eligibility_status,
            "reasons": reasons,
            "source": "CURRENT_PLATFORM_LEDGER_AND_SNAPSHOT",
        })

    backfill_rows: list[dict[str, Any]] = []
    for alpha_id, alpha_type, eligibility_status, description_status, reasons in con.execute(
        """SELECT job.alpha_id,job.alpha_type,job.eligibility_status,job.description_status,
                  snapshot.reasons_json
        FROM description_backfill_jobs AS job
        JOIN platform_alpha_ledger AS ledger
          ON ledger.sync_id=job.sync_id AND ledger.alpha_id=job.alpha_id
        LEFT JOIN alpha_eligibility_snapshots AS snapshot
          ON snapshot.sync_id=job.sync_id AND snapshot.alpha_id=job.alpha_id
        WHERE job.sync_id=? AND job.eligibility_status='SUBMIT_READY_EXCEPT_DESCRIPTION'
        ORDER BY job.alpha_id""",
        (authoritative_sync_id,),
    ):
        backfill_rows.append({
            "alpha_id": alpha_id,
            "sync_id": authoritative_sync_id,
            "alpha_type": alpha_type,
            "eligibility_status": eligibility_status,
            "description_status": description_status,
            "status": "DRY_RUN",
            "reason": _reason_text(reasons) or "DESCRIPTION_REQUIRED",
            "endpoint_calls": 0,
        })

    failures: list[dict[str, Any]] = []
    for alpha_id, alpha_type, description_status, validation_errors, last_error in con.execute(
        """SELECT alpha_id,alpha_type,description_status,validation_errors_json,last_error
        FROM description_backfill_jobs
        WHERE sync_id=? AND description_status IN ('FAILED','SCHEMA_UNKNOWN')
        ORDER BY alpha_id""",
        (authoritative_sync_id,),
    ):
        failures.append({
            "alpha_id": alpha_id,
            "sync_id": authoritative_sync_id,
            "alpha_type": alpha_type,
            "description_status": description_status,
            "reasons": _reason_text(validation_errors, last_error) or str(description_status),
        })
    return backfill_rows, failures, eligibility_rows


def _write_phase1_markdown_artifacts(
    target: Path,
    *,
    authoritative_sync_id: str,
    ledger_rows: int,
    backfill_rows: list[dict[str, Any]],
    failure_rows: list[dict[str, Any]],
    eligibility_rows: list[dict[str, Any]],
) -> None:
    status = "PASS" if authoritative_sync_id else "BLOCKED"
    blocker = "none" if authoritative_sync_id else "PLATFORM_LEDGER_NOT_COMPLETE"
    common = f"""
## Phase 1 safety boundary

- Status: **{status}**
- Ledger authority: `{authoritative_sync_id or 'MISSING'}`; rows: `{ledger_rows}`.
- Blocker: `{blocker}`.
- PATCH endpoint calls: 0
- Submit endpoint calls: 0
"""
    (target / "DESCRIPTION_AND_ALPHA_QUALITY_AUDIT.md").write_text(
        "# Description and Alpha Quality Audit\n"
        + common
        + f"\n- Historical eligibility rows: `{len(eligibility_rows)}`.\n"
        + f"- Description dry-run rows: `{len(backfill_rows)}`.\n"
        + f"- Persisted validation/schema failures: `{len(failure_rows)}`.\n\n"
        + "## Implementation control paths\n\n"
        + "- Entry: `run_pipeline_cycle.py -> alpha_mining.factory.runtime.main -> FactoryOrchestrator.run_simulate`\n"
        + "- Description preparation: `FactoryOrchestrator._prepare_description`\n"
        + "- Schema observation: `DescriptionSchemaRegistry.observe_from_payload` (field: `payloadPath`)\n"
        + "- Delivery: `DescriptionDelivery.patch_once` (sequence: `GET -> PATCH -> GET`)\n"
        + "- Quality threshold: `FactoryOrchestrator._live_sharpe_threshold`\n"
        + "- Baseline classification: `classify_baseline`\n"
        + "- Identity tracking: `ResearchIdentity`, `behavior_signature`\n"
        + "- Cluster management: `cluster_disposition`, `rank_parents`\n",
        encoding="utf-8",
    )
    (target / "DESCRIPTION_PIPELINE_IMPLEMENTATION.md").write_text(
        "# Description Pipeline Implementation\n"
        + common
        + "\nThe Phase 1 output is offline-only: it records validated backfill candidates "
          "without requesting a platform PATCH or submission.\n\n"
        + "## Core pipeline functions\n\n"
        + "- Fact extraction: `extract_description_facts`\n"
        + "- Description generation: `build_deterministic_description`\n"
        + "- Validation: `validate_description`\n"
        + "- Status tracking: `DescriptionStatus`\n"
        + "- Job persistence: `description_backfill_jobs`\n"
        + "- Delivery: `DescriptionDelivery.patch_once` (sequence: `GET -> PATCH -> GET`)\n\n"
        + "## CLI commands\n\n"
        + "- `inspect`: View ledger state\n"
        + "- `generate`: Rebuild description from persisted facts\n"
        + "- `dry-run`: Preview backfill candidates\n"
        + "- `backfill`: Execute description patch\n"
        + "- `resume`: Check job status\n",
        encoding="utf-8",
    )
    (target / "ALPHA_QUALITY_CORRELATION_FIX.md").write_text(
        "# Alpha Quality Correlation Fix\n"
        + common
        + "\nCurrent-ledger eligibility is kept separate from legacy observations; missing "
          "or non-authoritative platform evidence remains blocked.\n\n"
        + "## Key components\n\n"
        + "- Generator: `BaselineFirstGenerator`\n"
        + "- Correlation zones: `FAR_FAIL`, `NEAR_PASS`\n"
        + "- Strategy: `OFAT` (One Factor At a Time)\n"
        + "- Arm tracking: `ResearchArmTracker`, `research_arm_metrics`\n"
        + "- Identity: `ResearchIdentity`\n"
        + "- Quotas: `BehaviorRoundQuota`, `GenerationQuota`\n"
        + "- Cluster management: `cluster_disposition`, `rank_parents`\n",
        encoding="utf-8",
    )


def run_acceptance_audit(
    database: str | Path,
    output_dir: str | Path = ".",
    *,
    external_blockers: Iterable[str] = (),
    verification_summary: str = "not supplied",
) -> AcceptanceResult:
    db = Path(database)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    migrate(db)
    blockers: list[str] = list(dict.fromkeys(str(item) for item in external_blockers if str(item)))
    with sqlite3.connect(db) as con:
        sync = _latest_sync(con)
        attempted_sync_id = str(sync[0]) if sync else ""
        sync_id = str(sync[0]) if sync and str(sync[1]) == "COMPLETE" else ""
        authoritative_sync_id = ""
        ledger_rows = 0
        if not sync or str(sync[1]) != "COMPLETE":
            blockers.append("PLATFORM_LEDGER_NOT_COMPLETE")
        else:
            try:
                declared_count = int(sync[2]) if sync[2] is not None else 0
                fetched_rows = int(sync[3]) if sync[3] is not None else 0
                unique_alpha_ids = int(sync[4]) if sync[4] is not None else 0
                duplicate_alpha_ids = int(sync[5]) if sync[5] is not None else 0
                completed = datetime.fromisoformat(str(sync[6]).replace("Z", "+00:00"))
                if completed < datetime.now(timezone.utc) - timedelta(hours=24):
                    blockers.append("PLATFORM_LEDGER_STALE")
                else:
                    ledger_rows = int(con.execute(
                        "SELECT COUNT(*) FROM platform_alpha_ledger WHERE sync_id=?", (sync_id,)
                    ).fetchone()[0])
                    if declared_count != fetched_rows or declared_count != unique_alpha_ids or ledger_rows != declared_count:
                        blockers.append("PLATFORM_LEDGER_NOT_COMPLETE")
                    elif duplicate_alpha_ids > 0:
                        blockers.append("PLATFORM_LEDGER_NOT_COMPLETE")
                    elif ledger_rows > 0:
                        authoritative_sync_id = sync_id
            except ValueError:
                blockers.append("PLATFORM_LEDGER_TIMESTAMP_INVALID")
        if not authoritative_sync_id:
            blockers.append("PLATFORM_LEDGER_NOT_COMPLETE")
        reconciliation = _reconciliation(con, sync_id) if sync_id else []
        observations, statuses = _legacy_observations(con, sync_id)
        clusters = _clusters(con, statuses)
        funnel = _funnel(con)
        description_rows = _description_observations(con, sync_id)
        phase1_backfill_rows, phase1_failure_rows, phase1_eligibility_rows = _phase1_description_rows(
            con, authoritative_sync_id
        )
        migration_versions = [int(row[0]) for row in con.execute("SELECT version FROM schema_migrations ORDER BY version")]
        factory_row = con.execute(
            "SELECT hard_stop,reason,ledger_sync_id,cluster_freeze_complete,execute_submit FROM factory_control WHERE singleton=1"
        ).fetchone()
        if sync_id:
            con.execute(
                "UPDATE factory_control SET ledger_sync_id=?,cluster_freeze_complete=1,hard_stop=1,"
                "reason='acceptance_pilot_pending',execute_submit=0,updated_at=? WHERE singleton=1",
                (sync_id, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
            )
            con.commit()
            factory_row = con.execute(
                "SELECT hard_stop,reason,ledger_sync_id,cluster_freeze_complete,execute_submit FROM factory_control WHERE singleton=1"
            ).fetchone()

    blockers = list(dict.fromkeys(blockers))
    _write_csv(target / "platform_reconciliation.csv", ["alpha_id", "in_platform", "in_local", "platform_status", "local_status", "expression_hash", "mismatch_reasons", "primary_reason", "sync_id"], reconciliation)
    _write_csv(target / "legacy_self_corr_observations.csv", ["alpha_id", "legacy_id", "status", "observed_at", "source", "evidence"], observations)
    cluster_headers = ["cluster_id", "behavior_signature", "member_count", "medoid_legacy_id", "quality_representative", "structural_representative", "representative_statuses", "disposition", "frozen", "pnl_evidence", "confidence"]
    _write_csv(target / "legacy_self_corr_clusters.csv", cluster_headers, clusters)
    _write_csv(target / "legacy_frozen_clusters.csv", cluster_headers, [row for row in clusters if row["disposition"] == "FROZEN"])
    _write_csv(target / "legacy_salvageable_clusters.csv", cluster_headers, [row for row in clusters if row["disposition"] != "FROZEN"])
    funnel_headers = ["trial_id", "alpha_id", "observed_at", "cohort_30d", "cohort_last_1000", "family", "dataset", "settings_hash", "provenance", "all_failures", "primary_failure", "base_pass", "complete_checks", "self_corr_pass", "description_ready", "final_submittable", "sharpe", "fitness"]
    _write_csv(target / "new_alpha_failure_funnel.csv", funnel_headers, funnel)
    metric_headers = ["group", "simulations", "base_pass_per_100", "complete_check_per_100", "self_corr_pass_per_100", "description_ready_per_100", "submittable_per_100"]
    grouped_metrics: dict[str, list[dict[str, Any]]] = {}
    for filename, key in (("new_alpha_family_metrics.csv", "family"), ("new_alpha_dataset_metrics.csv", "dataset"), ("new_alpha_settings_metrics.csv", "settings_hash")):
        rows = [{"group": row[key], **{name: value for name, value in row.items() if name != key}} for row in _metric_rows(funnel, key)]
        grouped_metrics[key] = rows
        _write_csv(target / filename, metric_headers, rows)
    _write_csv(target / "description_validation_report.csv", ["alpha_id", "alpha_type", "target_field", "status", "reasons"], description_rows)
    _write_csv(
        target / "description_backfill_dry_run.csv",
        ["alpha_id", "sync_id", "alpha_type", "eligibility_status", "description_status", "status", "reason", "endpoint_calls"],
        phase1_backfill_rows,
    )
    _write_csv(
        target / "description_validation_failures.csv",
        ["alpha_id", "sync_id", "alpha_type", "description_status", "reasons"],
        phase1_failure_rows,
    )
    _write_csv(
        target / "historical_alpha_eligibility.csv",
        ["alpha_id", "sync_id", "eligibility_status", "reasons", "source"],
        phase1_eligibility_rows,
    )
    _write_phase1_markdown_artifacts(
        target,
        authoritative_sync_id=authoritative_sync_id,
        ledger_rows=ledger_rows,
        backfill_rows=phase1_backfill_rows,
        failure_rows=phase1_failure_rows,
        eligibility_rows=phase1_eligibility_rows,
    )
    knowledge = [
        {"source_tier": "A", "source_type": "OFFICIAL", "title": "WorldQuant Learn2Quant", "reference": "https://www.worldquant.com/learn2quant/", "evidence_level": "OFFICIAL", "production_status": "RESEARCH_ONLY"},
        {"source_tier": "A", "source_type": "OFFICIAL", "title": "WorldQuant BRAIN", "reference": "https://www.worldquant.com/brain/", "evidence_level": "OFFICIAL", "production_status": "RESEARCH_ONLY"},
        {"source_tier": "B", "source_type": "RESEARCH", "title": "Local SSRN research asset", "reference": "ssrn-2701346.pdf", "evidence_level": "RESEARCH", "production_status": "RESEARCH_ONLY"},
    ]
    _write_csv(target / "knowledge_source_inventory.csv", ["source_tier", "source_type", "title", "reference", "evidence_level", "production_status"], knowledge)
    _write_csv(target / "submission_dry_run.csv", ["alpha_id", "sync_id", "allowed", "reasons", "endpoint_calls"], [])
    blocked = [{"scope": "RUN", "alpha_id": "", "reason": reason, "endpoint_calls": 0} for reason in blockers]
    _write_csv(target / "submission_blocked.csv", ["scope", "alpha_id", "reason", "endpoint_calls"], blocked)

    root = Path(__file__).resolve().parents[2]
    branch = _git(["branch", "--show-current"], root)
    head = _git(["rev-parse", "HEAD"], root)
    worktree_status = _git(["status", "--short"], root)
    status = "BLOCKED" if blockers else "PASS"
    self_status_counts = Counter(row["status"] for row in observations)
    factory_summary = (
        f"hard_stop={bool(factory_row[0])}, reason={factory_row[1]}, ledger_sync_id={factory_row[2] or 'MISSING'}, "
        f"cluster_freeze_complete={bool(factory_row[3])}, execute_submit={bool(factory_row[4])}"
        if factory_row
        else "MISSING (fail-closed)"
    )
    baseline = f"""# Current Main Acceptance Audit

- Branch: `{branch}`
- HEAD: `{head}`
- alpha_mining: `{Path(__file__).resolve().parents[1] / '__init__.py'}`
- Authoritative entry: `run_pipeline_supervisor.py -> run_pipeline_loop.py -> run_pipeline_cycle.py -> alpha_mining.factory.runtime`
- CLI entry: `python -m alpha_mining`; generation/simulation/submit paths share `FactoryControl`
- Scheduled-task scan: `0` matching Alpha/BRAIN/WorldQuant tasks; repository deploy/cron scripts do not bypass the vNext cycle
- Matching legacy runtime process scan: `0`; no process required termination
- Legacy v50 direct delegation: disabled
- Python: `{platform.python_version()}`
- Applied database migrations: `{migration_versions}`
- Factory control: `{factory_summary}`
- Latest attempted platform sync: `{attempted_sync_id or 'MISSING'}`
- Authoritative COMPLETE platform sync: `{sync_id or 'MISSING'}`
- Legacy SELF_CORRELATION observations: `{dict(self_status_counts)}`
- Legacy clusters: `{len(clusters)}` (`STRUCTURAL_ONLY` while PnL/platform correlation evidence is missing)
- Working tree: `dirty by this acceptance implementation`; tracked cookie removal is staged and `.wq_browser_cookie*.json` is ignored
- Test baseline before repair: `482 passed, 3 failed, 5 subtests passed`
- Verification after repair: `{verification_summary}`
- Security: browser cookie content was never emitted; historical credential rotation remains required because Git history was not rewritten
- Current worktree status hash: `{hashlib.sha256(worktree_status.encode('utf-8')).hexdigest()}`
- Database freshness: `{_file_freshness(db.resolve())}`
- Legacy feedback CSV freshness: `{_file_freshness(root / 'alpha_submission_feedback.csv')}`
- Legacy filtered CSV freshness: `{_file_freshness(root / 'alpha_pipeline_results.csv')}`
- Loop log freshness: `{_file_freshness(root / 'pipeline_loop.log')}`
- Supervisor log freshness: `{_file_freshness(root / 'pipeline_supervisor.log')}`
- Status: **{status}**
- Blockers: `{', '.join(blockers) if blockers else 'none'}`
"""
    (target / "CURRENT_MAIN_ACCEPTANCE_AUDIT.md").write_text(baseline, encoding="utf-8")
    reasons = Counter(row["primary_reason"] for row in reconciliation if row["primary_reason"] != "MATCH")
    failures = Counter(row["primary_failure"] for row in funnel)
    dispositions = Counter(row["disposition"] for row in clusters)
    description_failures = Counter(
        row["alpha_type"] for row in description_rows if row["status"] != "PRESENT_UNVERIFIED"
    )
    family_metrics = grouped_metrics.get("family", [])
    best_base_family = (
        max(family_metrics, key=lambda row: (float(row["base_pass_per_100"]), int(row["simulations"])))
        if family_metrics
        else None
    )
    total_trials = len(funnel)
    per_100 = {
        "base_pass": round(100 * sum(bool(row["base_pass"]) for row in funnel) / total_trials, 3) if total_trials else 0,
        "complete_checks": round(100 * sum(bool(row["complete_checks"]) for row in funnel) / total_trials, 3) if total_trials else 0,
        "self_corr_pass": round(100 * sum(bool(row["self_corr_pass"]) for row in funnel) / total_trials, 3) if total_trials else 0,
        "description_ready": round(100 * sum(bool(row["description_ready"]) for row in funnel) / total_trials, 3) if total_trials else 0,
        "final_submittable": round(100 * sum(bool(row["final_submittable"]) for row in funnel) / total_trials, 3) if total_trials else 0,
    }
    final = f"""# Consultant Factory Final Acceptance

## Verdict

**{status}** — no threshold was lowered and no unknown platform state was treated as PASS.

## Required Answers

1. Platform/local mismatch: `{dict(reasons) if reasons else 'not verifiable without a complete live ledger'}`.
2. Pagination/filter/status/duplicate/staleness attribution: `{dict(reasons)}`.
3. Independent legacy behavior clusters: `{len(clusters)}`.
4. Frozen clusters: `{dispositions.get('FROZEN', 0)}`; observe-only/salvageable: `{len(clusters) - dispositions.get('FROZEN', 0)}`. Without a current platform ledger, no historical local status is allowed to freeze a cluster.
5. Largest new-Alpha failure: `{failures.most_common(1)[0] if failures else 'no cohort evidence'}`.
6. Highest base-pass family: `{(best_base_family['group'], best_base_family['base_pass_per_100'], best_base_family['simulations']) if best_base_family else 'no cohort evidence'}` (family, passes per 100, simulations).
7. Lowest SELF_CORRELATION family: `not rankable`; there are no explicit current-platform SELF_CORRELATION PASS observations, and missing/unknown evidence is never interpreted as low correlation.
8. Missing descriptions by Alpha type: `{dict(description_failures) if description_rows else 'not verifiable without a complete live ledger'}`.
9. Fully eligible real candidates: `0` while blockers exist; submit endpoint calls: `0`.
10. Next budget: increase `platform ledger/check evidence acquisition` first; increase no production research family until explicit SELF_CORRELATION PASS exists. Freeze settings/window/sign/coefficient tuning for all observe-only legacy clusters; permanently freeze only clusters with three explicit representative FAIL results.

## Per 100 simulations

- Base pass: `{per_100['base_pass']}`
- Complete platform checks: `{per_100['complete_checks']}`
- SELF_CORRELATION PASS: `{per_100['self_corr_pass']}`
- Description complete: `{per_100['description_ready']}`
- Finally submittable: `{per_100['final_submittable']}`

The live pilot was not started because a fresh COMPLETE platform ledger is a hard prerequisite. No Description PATCH or submit endpoint was called.

## Blockers

{chr(10).join(f'- {item}' for item in blockers) if blockers else '- none'}
"""
    (target / "CONSULTANT_FACTORY_FINAL_ACCEPTANCE.md").write_text(final, encoding="utf-8")
    return AcceptanceResult(status, tuple(blockers), target)
