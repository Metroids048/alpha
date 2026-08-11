"""Temporary bounded real-platform quality recovery pilot; never submits."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
VAL_ROOT = ROOT / ".validation_workspace"
PROD_DB = ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
PILOT_ROOT = ROOT / "tmp_alpha_quality_pilot_workspace_fresh"
PILOT_DB = PILOT_ROOT / "pilot.sqlite"
REPORT = ROOT / "tmp_alpha_quality_pilot_report_fresh.json"
AUTH_STATE = ROOT / ".wq_auth_state.json"
LOCK = ROOT / "worldquant_api.lock"
SETTINGS_SCHEMA = VAL_ROOT / ".alpha_simulation_settings_cache.json"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _init_pilot_db() -> None:
    PILOT_ROOT.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(PILOT_DB) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS simulations (
                round INTEGER NOT NULL, ordinal INTEGER NOT NULL, expression TEXT NOT NULL,
                generator_source TEXT NOT NULL, provenance TEXT NOT NULL, fields_json TEXT NOT NULL,
                settings_json TEXT NOT NULL, alpha_id TEXT, status TEXT, metrics_json TEXT NOT NULL,
                checks_json TEXT NOT NULL, quality_status TEXT, quality_reasons_json TEXT NOT NULL,
                error TEXT, observed_at TEXT NOT NULL
            )"""
        )


def _record(round_no: int, ordinal: int, row: dict[str, Any]) -> None:
    with sqlite3.connect(PILOT_DB) as con:
        con.execute(
            """INSERT INTO simulations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                round_no,
                ordinal,
                row.get("expression", ""),
                row.get("generator_source", "v50-native"),
                row.get("provenance", ""),
                json.dumps(row.get("fields", []), sort_keys=True),
                json.dumps(row.get("settings", {}), sort_keys=True),
                row.get("alpha_id", ""),
                row.get("status", ""),
                json.dumps(row.get("metrics", {}), sort_keys=True),
                json.dumps(row.get("checks", []), sort_keys=True),
                row.get("quality_status", ""),
                json.dumps(row.get("quality_reasons", []), sort_keys=True),
                row.get("error", ""),
                _utc(),
            ),
        )


def _settings() -> dict[str, Any]:
    payload = json.loads(SETTINGS_SCHEMA.read_text(encoding="utf-8"))
    defaults = dict(payload["defaults"])
    defaults.pop("alpha_type", None)
    # Fixed pilot contract; all remaining values come from the synchronized cache.
    fixed = {
        "instrumentType": "EQUITY",
        "region": "USA",
        "universe": "TOP3000",
        "delay": 1,
        "language": "FASTEXPR",
    }
    return {**defaults, **fixed}


def _candidate_rows(batch: Any, snapshots: Any) -> list[dict[str, Any]]:
    from alpha_mining.domain.expression_normalization import extract_fields
    from alpha_mining.factory.v50_adapter import adapt_v50_candidate

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for candidate in batch.candidates:
        expression = str(getattr(candidate, "expression", "") or "").strip()
        if not expression or expression in seen:
            continue
        seen.add(expression)
        try:
            proposal = adapt_v50_candidate(candidate, batch.catalog)
        except (TypeError, ValueError):
            continue
        fields = []
        for field_id in extract_fields(expression):
            if field_id in batch.catalog.base_vars:
                continue
            fields.append(
                {
                    "id": field_id,
                    "type": batch.catalog.field_type.get(field_id, "UNKNOWN"),
                    "dataset": batch.catalog.field_dataset.get(field_id, ""),
                }
            )
        rows.append(
            {
                "candidate": candidate,
                "proposal": proposal,
                "expression": expression,
                "generator_source": "v50-native",
                "provenance": "platform_feedback_pool+v50-native" if snapshots.feedback.records else "v50-native",
                "fields": fields,
            }
        )
    return rows


def _bounded_v50_snapshots(snapshots: Any) -> tuple[Any, dict[str, Any]]:
    """Keep a real mixed-type dataset slice so the pilot can finish promptly."""
    import auto_alpha_pipeline_rebuilt_v50 as v50
    from alpha_mining.offline.metadata import MetadataCache

    by_dataset: dict[str, list[Any]] = {}
    for item in snapshots.catalog.fields.values():
        by_dataset.setdefault(item.dataset_id, []).append(item)
    eligible = [
        (dataset_id, rows)
        for dataset_id, rows in by_dataset.items()
        if sum(item.field_type.upper() == "MATRIX" for item in rows) >= 12
        and sum(item.field_type.upper() == "VECTOR" for item in rows) >= 4
    ]
    if not eligible:
        raise RuntimeError("BLOCKED_EXTERNAL: current validated catalog has no mixed MATRIX/VECTOR dataset slice")
    dataset_id, fields = max(eligible, key=lambda item: len(item[1]))
    matrices = sorted(
        (item for item in fields if item.field_type.upper() == "MATRIX"),
        key=lambda item: (v50.field_quality_score(item.field_id), item.field_id),
        reverse=True,
    )[:12]
    vectors = sorted(
        (item for item in fields if item.field_type.upper() == "VECTOR"),
        key=lambda item: (v50.field_quality_score(item.field_id), item.field_id),
        reverse=True,
    )[:4]
    selected = {item.field_id: item for item in [*matrices, *vectors]}
    catalog = MetadataCache(
        snapshots.catalog.cache_dir,
        snapshots.catalog.operators,
        selected,
        snapshots.catalog.datasets,
        snapshots.catalog.info,
    )
    return replace(snapshots, catalog=catalog), {
        "dataset": dataset_id,
        "matrix_fields": len(matrices),
        "vector_fields": len(vectors),
    }


def _simulate_rows(round_no: int, rows: list[dict[str, Any]], settings: dict[str, Any], gateway: Any, budget: int) -> tuple[list[dict[str, Any]], str]:
    from alpha_mining.quality.decision import evaluate_quality

    results: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows[:budget], start=1):
        evidence = {key: row[key] for key in ("expression", "generator_source", "provenance", "fields")}
        evidence["round"] = round_no
        evidence["settings"] = dict(settings)
        try:
            result = gateway.simulate(expression=row["expression"], settings=settings, alpha_type="REGULAR")
            decision = evaluate_quality(
                alpha_id=result.alpha_id,
                status=result.status,
                metrics=result.metrics,
                checks=result.checks,
                prod_corr_exception_confirmed=bool((result.raw or {}).get("prodCorrExceptionConfirmed")),
            )
            evidence.update(
                alpha_id=result.alpha_id,
                status=result.status,
                metrics=result.metrics,
                checks=result.checks,
                quality_status=decision.status.value,
                quality_reasons=list(decision.reasons),
            )
        except Exception as exc:  # preserve bounded evidence; no retry loop here
            evidence.update(error=f"{type(exc).__name__}: {str(exc)[:500]}", quality_status="EXTERNAL_ERROR")
        _record(round_no, ordinal, evidence)
        results.append(evidence)
        print(json.dumps({k: evidence.get(k) for k in ("expression", "alpha_id", "metrics", "quality_status", "error")}, ensure_ascii=False))
        if evidence.get("quality_status") == "READY_TO_SUBMIT":
            return results, "SUCCESS_HIGH_QUALITY_ALPHA"
        if evidence.get("quality_status") == "EXTERNAL_ERROR":
            error = str(evidence.get("error") or "").lower()
            if any(token in error for token in ("429", "circuit", "authentication", "401", "403", "proxyerror")):
                return results, "BLOCKED_EXTERNAL"
    return results, ""


def _cluster(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reasons = row.get("quality_reasons") or [row.get("quality_status") or "UNKNOWN"]
        for reason in reasons:
            key = str(reason)
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def main() -> int:
    from alpha_mining.common import load_workspace_env
    from alpha_mining.factory.v50_adapter import adapt_v50_candidate
    from alpha_mining.generation.snapshots import load_local_snapshots
    from alpha_mining.generation.v50_kernel import V50Kernel
    from alpha_mining.platform.gateway import PlatformGateway

    load_workspace_env(ROOT / ".env")
    _init_pilot_db()
    snapshots = load_local_snapshots(
        root=VAL_ROOT,
        catalog_dir=VAL_ROOT,
        database=PROD_DB,
        allow_partial_offline=False,
    )
    feedback_provenance = {
        "records": len(snapshots.feedback.records),
        "platform_verified": sum(1 for item in snapshots.feedback.records if item.platform_verified),
        "positive": len(snapshots.feedback.positive),
        "near_pass": len(snapshots.feedback.near_pass),
    }
    pilot_snapshots, catalog_slice = _bounded_v50_snapshots(snapshots)
    batch = V50Kernel(seed_pool_size=12).generate_batch(pilot_snapshots)
    rows = _candidate_rows(batch, snapshots)
    settings = _settings()
    gateway = PlatformGateway(
        state_path=AUTH_STATE,
        database=PROD_DB,
        lock_path=LOCK,
        min_interval=3.0,
        timeout=60.0,
        poll_interval=3.0,
        max_poll_seconds=600.0,
        settings_schema_path=SETTINGS_SCHEMA,
    )
    all_results: list[dict[str, Any]] = []
    round_results, status = _simulate_rows(1, rows, settings, gateway, 12)
    all_results.extend(round_results)
    round2_reason = ""
    if status:
        final_status = status
    elif any(row.get("quality_status") == "NEAR_PASS" for row in round_results):
        # The first pilot has no room for broad search; bounded refinement is
        # intentionally delegated to the existing optimizer only.
        from alpha_mining.simulate.settings_optimizer import SettingsOptimizer

        optimizer = SettingsOptimizer(max_local_trials=4, total_budget=12, per_candidate_budget=4)
        near_rows = [row for row in round_results if row.get("quality_status") == "NEAR_PASS"][:2]
        for parent_index, parent in enumerate(near_rows, start=1):
            plan = optimizer.tune_plan(settings, candidate_id=f"pilot-parent-{parent_index}")
            for stage in plan.stages:
                for trial in SettingsOptimizer.stage_trials(stage, plan.base_settings):
                    if len(all_results) >= 12:
                        break
                    child = dict(parent)
                    child["expression"] = parent["expression"]
                    child["generator_source"] = "v50-native+SettingsOptimizer"
                    child["settings"] = trial.settings
                    trial_results, status = _simulate_rows(1, [child], trial.settings, gateway, 1)
                    all_results.extend(trial_results)
                    if status:
                        break
                if status or len(all_results) >= 12:
                    break
            if status:
                break
        final_status = status or "PARTIAL_NEAR_PASS_FOUND"
    else:
        # Round 1 provides the only evidence used to choose a bounded Round 2
        # arm. No production code is changed in this branch.
        clusters = _cluster(round_results)
        round2_reason = "no READY/NEAR; preserve generator, rerun native candidates after round-1 evidence"
        remaining = [row for row in rows if row["expression"] not in {item.get("expression") for item in round_results}]
        round2, status = _simulate_rows(2, remaining, settings, gateway, 12)
        all_results.extend(round2)
        final_status = status or ("QUALITY_RECOVERY_FAILED_WITH_PLATFORM_EVIDENCE" if len(round2) else "BLOCKED_EXTERNAL")
    report = {
        "status": final_status,
        "root_cause": "v50 generation boundary erased field_type before FieldCatalog/ExpressionFactory",
        "fixed_settings": settings,
        "feedback_provenance": feedback_provenance,
        "catalog_slice": catalog_slice,
        "round1": [row for row in all_results if row.get("round", 1) == 1],
        "round2": [row for row in all_results if row.get("round", 1) == 2],
        "failure_clusters": _cluster(all_results),
        "round2_reason": round2_reason,
        "submit_calls": 0,
        "pilot_database": str(PILOT_DB),
        "observed_at": _utc(),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": final_status, "feedback": feedback_provenance, "clusters": report["failure_clusters"], "report": str(REPORT)}, ensure_ascii=False))
    return 0 if final_status in {"SUCCESS_HIGH_QUALITY_ALPHA", "PARTIAL_NEAR_PASS_FOUND", "QUALITY_RECOVERY_FAILED_WITH_PLATFORM_EVIDENCE"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
