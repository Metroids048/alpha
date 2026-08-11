"""Temporary real-platform A/B harness for preserved v50 versus LLM rewrites.

This file is intentionally untracked. It only changes local validation state by
running the approved simulations and recording evidence; it never submits an
alpha and never changes tracked application code or tests.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent
DB = ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
AUTH_STATE = ROOT / ".wq_auth_state.json"
LOCK = ROOT / "worldquant_api.lock"
SETTINGS_SCHEMA = ROOT / "tmp_v50_ab_settings_cache.json"
QUEUE = ROOT / ".validation_workspace" / "待提交Alpha列表.csv"
REPORT = ROOT / "tmp_v50_ab_report.json"

BASELINE = "9a3c3fe2c5e4598f187c8e820f9475d5d7c5787f"
FIXED_SETTINGS: dict[str, Any] = {
    "instrumentType": "EQUITY",
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
    "decay": 4,
    "neutralization": "MARKET",
    "truncation": 0.08,
    "language": "FASTEXPR",
    "pasteurization": "ON",
    "unitHandling": "VERIFY",
    "nanHandling": "ON",
    "visualization": False,
}
B_FILES = (
    "tmp_sim_closeout_1_09e065.json",
    "tmp_sim_closeout_2_75f9ff.json",
    "tmp_sim_closeout_3_e7b26c.json",
    "tmp_simulate_report.json",
)


def _git_state() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()

    return {
        "branch": run("branch", "--show-current"),
        "head": run("rev-parse", "HEAD"),
        "status": run("status", "--short"),
        "remote": run("remote", "-v"),
    }


def _assert_baseline() -> dict[str, Any]:
    state = _git_state()
    tracked_changes = [line for line in state["status"].splitlines() if not line.startswith("??")]
    if state["branch"] != "main" or state["head"] != BASELINE or tracked_changes:
        raise RuntimeError(f"BLOCKED_BASELINE_CHANGED: {state}")
    return state


def _json_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _settings_match(settings: Any) -> bool:
    if not isinstance(settings, dict):
        return False
    return all(settings.get(key) == value for key, value in FIXED_SETTINGS.items())


def _quality_class(*, alpha_id: str, status: str, metrics: dict[str, Any], checks: list[dict[str, Any]]) -> tuple[str, list[str]]:
    from alpha_mining.quality.decision import evaluate_quality

    decision = evaluate_quality(
        alpha_id=alpha_id,
        status=status,
        metrics=metrics,
        checks=checks,
    )
    return decision.status.value, list(decision.reasons)


def _load_b() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for filename in B_FILES:
        path = ROOT / filename
        if not path.is_file():
            continue
        for item in _json_rows(path):
            settings = item.get("settings")
            if not _settings_match(settings):
                continue
            alpha_id = str(item.get("alpha_id") or "").strip()
            metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
            if not alpha_id or str(item.get("status") or "").upper() != "COMPLETE":
                continue
            if any(metrics.get(key) is None for key in ("sharpe", "fitness", "turnover")):
                continue
            if alpha_id in seen:
                continue
            seen.add(alpha_id)
            checks = item.get("checks") if isinstance(item.get("checks"), list) else []
            quality_status, reasons = _quality_class(
                alpha_id=alpha_id,
                status=str(item.get("status") or ""),
                metrics=metrics,
                checks=checks,
            )
            rows.append(
                {
                    "group": "current_llm_rewrite",
                    "source_file": filename,
                    "candidate_id": str(item.get("candidate_id") or ""),
                    "alpha_id": alpha_id,
                    "expression": str(item.get("expression") or "").strip(),
                    "settings": dict(settings),
                    "status": str(item.get("status") or ""),
                    "metrics": dict(metrics),
                    "checks": checks,
                    "quality_status": quality_status,
                    "major_failures": reasons,
                    "provenance": "PLATFORM_JSON_EVIDENCE",
                }
            )
    return rows


def _load_queue_rows() -> list[dict[str, str]]:
    if not QUEUE.is_file():
        return []
    with QUEUE.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _parent_mappings(b_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue_rows = _load_queue_rows()
    mappings: list[dict[str, Any]] = []
    for b in b_rows:
        candidate_id = b["candidate_id"]
        matches = [
            row
            for row in queue_rows
            if candidate_id
            and str(row.get("candidate_id") or "").startswith(candidate_id)
            and str(row.get("expression") or "").strip() == b["expression"]
        ]
        if len(matches) != 1:
            continue
        row = matches[0]
        parent_seed = str(row.get("parent_seed") or "").strip()
        if not parent_seed:
            continue
        mappings.append(
            {
                "b_alpha_id": b["alpha_id"],
                "b_candidate_id": candidate_id,
                "b_expression": b["expression"],
                "parent_seed": parent_seed,
                "parent_template": str(row.get("parent_template") or "").strip(),
                "mapping_evidence": {
                    "source": str(QUEUE),
                    "candidate_id_prefix_exact": True,
                    "expression_exact": True,
                    "parent_seed_field_present": True,
                },
            }
        )
    return mappings


def _local_catalog() -> Any:
    """Build the adapter's minimal catalog view from the preserved local cache.

    Paired A uses already-recorded v50 parent seeds, so invoking the legacy v50
    network catalog path would add quota and bypass the authoritative client.
    The adapter only needs field -> dataset ownership for deterministic legality.
    """
    path = ROOT / ".alpha_datafields_cache.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("EXTERNAL_VALIDATION_BLOCKED: local v50 field catalog cache is invalid")
    field_dataset: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        field = str(row.get("id") or "").strip()
        dataset = str(row.get("_ds") or row.get("dataset_id") or "").strip()
        if not dataset and isinstance(row.get("dataset"), dict):
            dataset = str(row["dataset"].get("id") or "").strip()
        if field and dataset:
            field_dataset[field] = dataset
    if not field_dataset:
        raise RuntimeError("EXTERNAL_VALIDATION_BLOCKED: local v50 field catalog cache has no ownership rows")
    return SimpleNamespace(field_dataset=field_dataset)


def _augment_catalog_from_platform(catalog: Any, gateway: Any) -> dict[str, Any]:
    """Read only the mapped parent fields missing from the local snapshot.

    The validation cache is intentionally not mutated.  This probe uses the
    same authenticated client as simulation and records only platform rows
    that explicitly match a requested field id, so the adapter remains the
    authority for legality and ownership.
    """
    missing = {
        field
        for field in {
            "asset_replacement_cost_factor_2",
            "cashflow_trend_analysis_10",
            "debt_to_ebitda_ratio_metric_3",
        }
        if field not in (getattr(catalog, "field_dataset", {}) or {})
    }
    if not missing:
        return {"status": "NO_MISSING_FIELDS", "rows": []}
    base = {
        "instrumentType": "EQUITY",
        "region": "USA",
        "universe": "TOP3000",
        "delay": 1,
        "limit": 50,
        "offset": 0,
    }
    rows: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for field in sorted(missing):
        found: dict[str, Any] | None = None
        for filter_key in ("id", "field.id", "search"):
            params = {**base, filter_key: field}
            try:
                payload = gateway.client.list_data_fields(params)
            except Exception as exc:
                attempts.append({"field": field, "filter": filter_key, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                continue
            result_rows = payload.get("results") if isinstance(payload, dict) else None
            result_rows = result_rows if isinstance(result_rows, list) else []
            exact = [row for row in result_rows if isinstance(row, dict) and str(row.get("id") or "").strip() == field]
            attempts.append({"field": field, "filter": filter_key, "result_count": len(result_rows), "exact_count": len(exact)})
            if exact:
                found = dict(exact[0])
                break
        if found is not None:
            dataset = str(found.get("_ds") or found.get("dataset_id") or "").strip()
            if not dataset and isinstance(found.get("dataset"), dict):
                dataset = str(found["dataset"].get("id") or "").strip()
            if dataset:
                catalog.field_dataset[field] = dataset
                rows.append({"id": field, "dataset": dataset, "type": found.get("type"), "source": "PLATFORM_CATALOG_PROBE"})
    return {"status": "COMPLETE", "rows": rows, "attempts": attempts, "remaining": sorted(set(missing) - {row["id"] for row in rows})}


def _platform_access_state(database: Path = DB) -> dict[str, Any]:
    from alpha_mining.platform.access import PlatformAccessController

    state = PlatformAccessController(database, LOCK).status()
    return {
        "state": state.state,
        "retry_after_until": state.retry_after_until,
        "recovery_attempts": state.recovery_attempts,
        "max_auto_recoveries": state.max_auto_recoveries,
        "last_successful_auth": state.last_successful_auth,
        "last_401": state.last_401,
        "last_429": state.last_429,
        "reason": state.reason,
    }


def _recent_status_counts(started_at: str) -> dict[str, int]:
    counts = {"401": 0, "429": 0}
    try:
        with sqlite3.connect(DB) as con:
            for code, count in con.execute(
                "SELECT status_code,COUNT(*) FROM platform_request_events WHERE timestamp>=? AND status_code IN (401,429) GROUP BY status_code",
                (started_at,),
            ):
                counts[str(int(code))] = int(count)
    except sqlite3.DatabaseError:
        pass
    return counts


def _auth_preflight() -> dict[str, Any]:
    """Prove the harness uses the current session/access recovery path."""
    from alpha_mining.common import load_workspace_env
    from alpha_mining.platform.gateway import PlatformGateway
    from alpha_mining.platform.bearer_auth import load_bearer_token
    from alpha_mining.platform.client import PlatformReadError

    load_workspace_env(ROOT / ".env")
    username_present = bool(os.environ.get("WQ_USERNAME", "").strip())
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state_before = _platform_access_state()
    proof: dict[str, Any] = {
        "status": "PENDING",
        "client": "alpha_mining.platform.client.ReadOnlyPlatformClient",
        "gateway": "alpha_mining.platform.gateway.PlatformGateway",
        "auth_state_path": str(AUTH_STATE),
        "username_present": username_present,
        "password_present": bool(os.environ.get("WQ_PASSWORD", "")),
        "stored_session_present": False,
        "stored_session_remaining_seconds": None,
        "stored_cookie_blob_present": False,
        "preflight_started_at": started_at,
        "access_before": state_before,
        "access_after": state_before,
        "401_count": 0,
        "429_count": 0,
    }
    try:
        state_payload = json.loads(AUTH_STATE.read_text(encoding="utf-8"))
        proof["stored_cookie_blob_present"] = bool(state_payload.get("cookie_blob_dpapi_b64"))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    if not username_present:
        proof.update(status="BLOCKED_AUTH_ACCOUNT_IDENTITY_REQUIRED", reason="WQ_USERNAME is not configured")
        return proof
    try:
        bearer = load_bearer_token(AUTH_STATE, os.environ.get("WQ_USERNAME", ""))
        if bearer is not None:
            proof["stored_session_present"] = True
            proof["stored_session_remaining_seconds"] = int(bearer.remaining_seconds)
    except Exception:
        pass
    if state_before["state"] == "RATE_LIMITED":
        proof.update(status="RATE_LIMIT_COOLDOWN", reason="PlatformAccessController circuit is open")
        return proof
    gateway = PlatformGateway(
        state_path=AUTH_STATE,
        database=DB,
        lock_path=LOCK,
        min_interval=3.0,
        timeout=60.0,
        settings_schema_path=SETTINGS_SCHEMA,
    )
    try:
        gateway.client.fetch_identity()
        proof["status"] = "AUTH_PREFLIGHT_OK"
    except Exception as exc:
        detail = str(exc)
        after = _platform_access_state()
        proof["access_after"] = after
        proof["reason"] = f"{type(exc).__name__}: {detail[:300]}"
        recent = _recent_status_counts(started_at)
        proof["401_count"] = recent["401"]
        proof["429_count"] = recent["429"]
        if proof["401_count"] == 0 and after.get("last_401") == started_at:
            proof["401_count"] = 1
        if proof["429_count"] == 0 and after.get("last_429") == started_at:
            proof["429_count"] = 1
        if after["state"] == "RATE_LIMITED":
            proof["status"] = "RATE_LIMIT_COOLDOWN"
        elif "401" in detail or after["last_401"]:
            proof["status"] = "BLOCKED_AUTH_INTERACTIVE_REQUIRED"
        else:
            proof["status"] = "EXTERNAL_VALIDATION_BLOCKED_TRUE_EXTERNAL"
    else:
        proof["access_after"] = _platform_access_state()
    return proof


def _write_report(report: dict[str, Any]) -> None:
    temp = REPORT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(REPORT)


def _catalog_and_raw_candidates():
    if os.environ.get("ALPHA_ENABLE_KNOWLEDGE_LLM") == "1":
        raise RuntimeError("EXTERNAL_VALIDATION_BLOCKED: ALPHA_ENABLE_KNOWLEDGE_LLM=1")
    os.environ.pop("ALPHA_ENABLE_KNOWLEDGE_LLM", None)
    from alpha_mining.factory.v50_adapter import generate_candidates

    # No knowledge database is passed: this is the preserved v50 path.
    return generate_candidates(knowledge_database=None)


def _adapt(candidate: Any, catalog: Any):
    from alpha_mining.domain.expression_normalization import expression_identity
    from alpha_mining.factory.v50_adapter import adapt_v50_candidate

    raw_expression = str(getattr(candidate, "expression", "") or "").strip()
    proposal = adapt_v50_candidate(candidate, catalog)
    if raw_expression != proposal.expression:
        raise RuntimeError("raw/adapted expression text changed")
    if expression_identity(raw_expression) != expression_identity(proposal.expression):
        raise RuntimeError("raw/adapted expression identity changed")
    return proposal


def _existing_hashes() -> set[str]:
    if not DB.is_file():
        return set()
    from alpha_mining.domain.expression_normalization import expression_identity

    hashes: set[str] = set()
    uri = f"file:{DB.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        for table, column in (("expression_identities", "exact_hash"), ("factory_candidate_claims", "exact_hash")):
            try:
                hashes.update(str(row[0]) for row in con.execute(f"SELECT {column} FROM {table}"))
            except sqlite3.DatabaseError:
                continue
    return hashes


def _classify_execution_failure(category: str, message: str) -> str:
    detail = f"{category} {message}".upper()
    if "429" in detail or "CIRCUITOPEN" in detail or "RATE_LIMIT" in detail:
        return "RATE_LIMIT_COOLDOWN"
    if "401" in detail or "AUTHENTICATION" in detail or "SESSION" in detail:
        return "BLOCKED_AUTH_INTERACTIVE_REQUIRED"
    return "SIMULATION_FAILED"


def _progress_evidence(gateway: Any, request_hash: str) -> dict[str, Any]:
    """Read back a previously accepted simulation progress URL without resubmitting."""
    with sqlite3.connect(DB) as con:
        row = con.execute(
            "SELECT progress_location,status,last_error FROM simulation_requests WHERE request_hash=?",
            (request_hash,),
        ).fetchone()
    if not row:
        return {"status": "MISSING_REQUEST"}
    location, request_status, last_error = row
    evidence: dict[str, Any] = {
        "request_status": str(request_status or ""),
        "last_error": str(last_error or "")[:300],
        "progress_location_present": bool(location),
    }
    if not location:
        return evidence
    try:
        response = gateway.client.request("GET", str(location), endpoint_class="simulation_poll")
        evidence["http_status"] = int(response.status_code)
        try:
            body = response.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            evidence["platform_status"] = str(body.get("status") or body.get("state") or "")
            for key in ("message", "error", "errors", "detail"):
                if key in body:
                    evidence[key] = body[key]
        return evidence
    except Exception as exc:
        evidence["readback_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return evidence


def _resume_failed_progress(prior_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Checkpoint final platform rejections so a resumed run never reposts them."""
    from alpha_mining.common import load_workspace_env
    from alpha_mining.platform.gateway import PlatformGateway

    load_workspace_env(ROOT / ".env")
    gateway = PlatformGateway(
        state_path=AUTH_STATE,
        database=DB,
        lock_path=LOCK,
        min_interval=3.0,
        timeout=60.0,
        settings_schema_path=SETTINGS_SCHEMA,
    )
    for row in prior_rows:
        if str(row.get("error_category") or "") != "INVALID_RESULT":
            continue
        if str(row.get("error_message") or "") != "alpha_id is empty":
            continue
        request_hash = str(row.get("request_hash") or "")
        if request_hash:
            row["platform_progress"] = _progress_evidence(gateway, request_hash)
            row["provenance"] = "PLATFORM_REJECTION_EVIDENCE"
            row["major_failures"] = ["PLATFORM_EXPRESSION_REJECTED"]
    by_expression = {
        str(row.get("expression") or ""): row
        for row in prior_rows
        if row.get("provenance") == "PLATFORM_REJECTION_EVIDENCE"
    }
    for row in prior_rows:
        if str(row.get("error_category") or "") != "CLAIM_REJECTED":
            continue
        source = by_expression.get(str(row.get("expression") or ""))
        if source is None:
            continue
        row["provenance"] = "PLATFORM_REJECTION_EVIDENCE"
        row["major_failures"] = ["PLATFORM_EXPRESSION_REJECTED_DUPLICATE_PARENT"]
        row["platform_progress"] = dict(source.get("platform_progress") or {})
        row["deduplicated_against_parent_b_alpha_id"] = source.get("parent_b_alpha_id", "")
    return prior_rows


def _simulate(
    items: list[dict[str, Any]],
    catalog: Any,
    *,
    checkpoint_context: dict[str, Any],
) -> list[dict[str, Any]]:
    from alpha_mining.common import load_workspace_env
    from alpha_mining.factory.orchestrator import FactoryOrchestrator
    from alpha_mining.generation.feedback import CandidateFeedbackStore
    from alpha_mining.platform.gateway import PlatformGateway

    load_workspace_env(ROOT / ".env")
    if os.environ.get("ALPHA_ENABLE_KNOWLEDGE_LLM") == "1":
        raise RuntimeError("EXTERNAL_VALIDATION_BLOCKED: ALPHA_ENABLE_KNOWLEDGE_LLM=1 after env load")
    gateway = PlatformGateway(
        state_path=AUTH_STATE,
        database=DB,
        lock_path=LOCK,
        min_interval=3.0,
        timeout=60.0,
        settings_schema_path=SETTINGS_SCHEMA,
    )
    contract = gateway.simulation_settings_contract
    if contract.prepare({**FIXED_SETTINGS, "alpha_type": "REGULAR"}) != FIXED_SETTINGS:
        raise RuntimeError("fixed settings do not match synchronized platform contract")
    if str(contract.alpha_type({**FIXED_SETTINGS, "alpha_type": "REGULAR"})).upper() != "REGULAR":
        raise RuntimeError("alpha_type contract is not REGULAR")
    with sqlite3.connect(DB) as con:
        row = con.execute("SELECT execute_submit FROM factory_control WHERE singleton=1").fetchone()
    if not row or int(row[0]) != 0:
        raise RuntimeError(f"EXTERNAL_VALIDATION_BLOCKED: execute_submit={row[0] if row else None}")

    factory = FactoryOrchestrator(DB, gateway)
    feedback = CandidateFeedbackStore(DB)
    results: list[dict[str, Any]] = []
    for item in items:
        candidate = item["candidate"]
        proposal = _adapt(candidate, catalog)
        execution = factory.execute_candidate(proposal, dict(FIXED_SETTINGS))
        row: dict[str, Any] = {
            "group": "raw_v50",
            "candidate_id": proposal.candidate_id,
            "expression": proposal.expression,
            "raw_expression": str(getattr(candidate, "expression", "")),
            "generator_source": "V50_PRESERVED",
            "parent_b_alpha_id": item.get("b_alpha_id", ""),
            "settings": dict(FIXED_SETTINGS),
            "request_hash": execution.request_hash,
            "status": "FAILED",
            "quality_status": "FAILED",
            "provenance": "PLATFORM_ERROR",
            "metrics": {},
            "checks": [],
            "major_failures": [],
        }
        if execution.result is None:
            row["error_category"] = execution.error_category
            row["error_message"] = execution.error_message
            row["major_failures"] = [_classify_execution_failure(execution.error_category, execution.error_message)]
            results.append(row)
            checkpoint_context["a"] = results
            checkpoint_context["pending_a"] = [
                {
                    "parent_b_alpha_id": value.get("b_alpha_id", ""),
                    "expression": str(getattr(value.get("candidate"), "expression", "")),
                }
                for value in items[len(results):]
            ]
            _write_report(checkpoint_context)
            if row["major_failures"][0] in {"RATE_LIMIT_COOLDOWN", "BLOCKED_AUTH_INTERACTIVE_REQUIRED"}:
                raise RuntimeError(row["major_failures"][0])
            continue
        result = execution.result
        from alpha_mining.quality.decision import evaluate_quality

        decision = evaluate_quality(
            alpha_id=result.alpha_id,
            status=result.status,
            metrics=result.metrics,
            checks=result.checks,
        )
        row.update(
            alpha_id=result.alpha_id,
            status=result.status,
            quality_status=decision.status.value,
            provenance="PLATFORM_VERIFIED",
            metrics=dict(result.metrics or {}),
            checks=list(result.checks or []),
            major_failures=list(decision.reasons),
        )
        feedback.record(
            execution.request_hash,
            decision.status.value,
            candidate_id=proposal.candidate_id,
            expression=proposal.expression,
            exact_hash=proposal.exact_hash,
            parameter_skeleton=proposal.parameter_skeleton,
            field_skeleton=proposal.field_skeleton,
            strategy_family=proposal.strategy_family,
            research_family=proposal.research_family,
            mechanism=proposal.mechanism,
            dataset=proposal.dataset,
            checks=list(result.checks or []),
            sharpe=result.metrics.get("sharpe"),
            fitness=result.metrics.get("fitness"),
            turnover=result.metrics.get("turnover"),
            quality_status=decision.status.value,
            quality_reasons=decision.reasons,
            region="USA",
            universe_name="TOP3000",
            delay="1",
            knowledge_usage_mode="NONE",
            provenance="PLATFORM_VERIFIED",
        )
        results.append(row)
        checkpoint_context["a"] = results
        checkpoint_context["pending_a"] = [
            {
                "parent_b_alpha_id": value.get("b_alpha_id", ""),
                "expression": str(getattr(value.get("candidate"), "expression", "")),
            }
            for value in items[len(results):]
        ]
        _write_report(checkpoint_context)
    with sqlite3.connect(DB) as con:
        row = con.execute("SELECT execute_submit FROM factory_control WHERE singleton=1").fetchone()
    if not row or int(row[0]) != 0:
        raise RuntimeError(f"STOPPED_SAFETY_RISK: execute_submit changed to {row[0] if row else None}")
    return results


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sharpes = [float(row["metrics"]["sharpe"]) for row in rows if row.get("metrics", {}).get("sharpe") is not None]
    fitness = [float(row["metrics"]["fitness"]) for row in rows if row.get("metrics", {}).get("fitness") is not None]
    successful = sum(str(row.get("status") or "").upper() == "COMPLETE" for row in rows)
    known_dead = [row for row in rows if row.get("provenance") == "PLATFORM_VERIFIED" and row.get("quality_status")]
    dead = sum(
        str(row.get("quality_status") or "").upper() == "FAR_FAIL"
        and float(row.get("metrics", {}).get("sharpe") or 0) <= 0
        and float(row.get("metrics", {}).get("fitness") or 0) <= 0
        for row in known_dead
    )
    return {
        "n": len(rows),
        "simulate_success_rate": successful / len(rows) if rows else 0.0,
        "median_sharpe": statistics.median(sharpes) if sharpes else None,
        "max_sharpe": max(sharpes) if sharpes else None,
        "median_fitness": statistics.median(fitness) if fitness else None,
        "sharpe_gt_0": sum(value > 0 for value in sharpes),
        "sharpe_gt_1": sum(value > 1 for value in sharpes),
        "near_pass": sum(str(row.get("quality_status") or "").upper() == "NEAR_PASS" for row in rows),
        "ready_pass": sum(str(row.get("quality_status") or "").upper() in {"READY_TO_SUBMIT", "PASS"} for row in rows),
        "dead_alpha": dead if known_dead else "UNKNOWN",
    }


def _paired_comparison(a_rows: list[dict[str, Any]], b_rows: list[dict[str, Any]], mappings: list[dict[str, Any]]) -> dict[str, Any]:
    b_by_id = {row["alpha_id"]: row for row in b_rows}
    a_by_parent = {row.get("parent_b_alpha_id"): row for row in a_rows}
    pairs: list[dict[str, Any]] = []
    for mapping in mappings:
        b = b_by_id.get(mapping["b_alpha_id"])
        a = a_by_parent.get(mapping["b_alpha_id"])
        if not b or not a:
            continue
        a_sharpe = a.get("metrics", {}).get("sharpe")
        b_sharpe = b.get("metrics", {}).get("sharpe")
        a_fitness = a.get("metrics", {}).get("fitness")
        b_fitness = b.get("metrics", {}).get("fitness")
        valid = all(value is not None for value in (a_sharpe, b_sharpe, a_fitness, b_fitness)) and str(a.get("status")).upper() == "COMPLETE"
        pairs.append({
            "b_alpha_id": mapping["b_alpha_id"],
            "a_expression": a.get("expression", ""),
            "b_expression": b.get("expression", ""),
            "a_sharpe": a_sharpe,
            "b_sharpe": b_sharpe,
            "sharpe_winner": "A" if valid and float(a_sharpe) > float(b_sharpe) else "B" if valid and float(b_sharpe) > float(a_sharpe) else "TIE" if valid else "INVALID",
            "a_fitness": a_fitness,
            "b_fitness": b_fitness,
            "fitness_winner": "A" if valid and float(a_fitness) > float(b_fitness) else "B" if valid and float(b_fitness) > float(a_fitness) else "TIE" if valid else "INVALID",
            "a_quality_class": a.get("quality_status", "UNKNOWN"),
            "b_quality_class": b.get("quality_status", "UNKNOWN"),
            "valid": valid,
        })
    valid_pairs = [pair for pair in pairs if pair["valid"]]
    a_valid = [a_by_parent[pair["b_alpha_id"]] for pair in valid_pairs]
    b_valid = [b_by_id[pair["b_alpha_id"]] for pair in valid_pairs]
    return {
        "pairs": pairs,
        "valid_pair_count": len(valid_pairs),
        "sharpe_wins": sum(pair["sharpe_winner"] == "A" for pair in valid_pairs),
        "fitness_wins": sum(pair["fitness_winner"] == "A" for pair in valid_pairs),
        "a_summary": _summary(a_valid),
        "b_summary": _summary(b_valid),
    }


def _decision(mode: str, a_summary: dict[str, Any], b_summary: dict[str, Any], comparison: dict[str, Any] | None) -> str:
    if mode == "PAIRED":
        valid = int(comparison["valid_pair_count"] if comparison else 0)
        if valid >= 3:
            a = comparison["a_summary"]
            b = comparison["b_summary"]
            if (
                comparison["sharpe_wins"] > valid / 2
                and a["median_sharpe"] > b["median_sharpe"]
                and a["median_fitness"] > b["median_fitness"]
            ):
                return "V50_BASELINE_OUTPERFORMS_LLM_REWRITE"
            return "V50_BASELINE_NOT_BETTER"
        return "EXTERNAL_VALIDATION_BLOCKED"
    if a_summary["median_sharpe"] is None or a_summary["median_fitness"] is None:
        return "EXTERNAL_VALIDATION_BLOCKED"
    if (
        a_summary["median_sharpe"] > b_summary["median_sharpe"]
        and a_summary["median_fitness"] > b_summary["median_fitness"]
        and a_summary["simulate_success_rate"] >= b_summary["simulate_success_rate"]
    ):
        return "V50_DIRECTIONALLY_BETTER"
    return "V50_BASELINE_NOT_BETTER"


def _rejection_only_decision(a_rows: list[dict[str, Any]]) -> str | None:
    """A platform rejection is a real A outcome, not a reason to resubmit it."""
    rejected = {
        str(row.get("expression") or "")
        for row in a_rows
        if row.get("provenance") == "PLATFORM_REJECTION_EVIDENCE"
        and str((row.get("platform_progress") or {}).get("platform_status") or "").upper()
        in {"FAILED", "ERROR", "REJECTED"}
    }
    return "AB_REJECTED_KEEP_BASELINE" if len(rejected) >= 3 else None


def main() -> int:
    git_state: dict[str, Any] = {"expected_head": BASELINE}
    b_rows: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    context: dict[str, Any] = {}
    try:
        git_state = _assert_baseline()
        for required in (DB, AUTH_STATE, SETTINGS_SCHEMA, ROOT / ".alpha_datafields_cache.json"):
            if not required.is_file():
                raise RuntimeError(f"EXTERNAL_VALIDATION_BLOCKED_TRUE_EXTERNAL: missing {required}")
        b_rows = _load_b()
        if len(b_rows) < 1:
            raise RuntimeError("EXTERNAL_VALIDATION_BLOCKED_TRUE_EXTERNAL: no fixed-settings B evidence")
        mappings = _parent_mappings(b_rows)
        prior: dict[str, Any] = {}
        if REPORT.is_file():
            try:
                raw_prior = json.loads(REPORT.read_text(encoding="utf-8"))
                prior = raw_prior if isinstance(raw_prior, dict) else {}
            except (OSError, ValueError, json.JSONDecodeError):
                prior = {}

        if os.environ.get("V50_AB_RECLASSIFY_ONLY") == "1":
            auth = dict(prior.get("auth") or {})
            started_at = str(auth.get("preflight_started_at") or "")
            if not started_at:
                after = auth.get("access_after") if isinstance(auth.get("access_after"), dict) else {}
                started_at = str(after.get("last_401") or "")
                auth["preflight_started_at"] = started_at
            try:
                state_payload = json.loads(AUTH_STATE.read_text(encoding="utf-8"))
                auth["stored_cookie_blob_present"] = bool(state_payload.get("cookie_blob_dpapi_b64"))
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            if started_at:
                recent = _recent_status_counts(started_at)
                auth["401_count"] = recent["401"]
                auth["429_count"] = recent["429"]
                after = auth.get("access_after") if isinstance(auth.get("access_after"), dict) else {}
                if auth["401_count"] == 0 and after.get("last_401") == started_at:
                    auth["401_count"] = 1
                if auth["429_count"] == 0 and after.get("last_429") == started_at:
                    auth["429_count"] = 1
            prior["auth"] = auth
            prior["decision"] = "BLOCKED_AUTH_INTERACTIVE_REQUIRED"
            prior["reason"] = auth.get("reason", "authentication recovery requires Persona/browser session import")
            if not prior.get("pending_a"):
                prior["pending_a"] = [
                    {"parent_b_alpha_id": mapping.get("b_alpha_id", ""), "expression": mapping.get("parent_seed", "")}
                    for mapping in (prior.get("parent_mappings") or [])
                ]
            _write_report(prior)
            print(json.dumps({"decision": prior["decision"], "auth": auth}, ensure_ascii=False, indent=2))
            return 5

        auth = _auth_preflight()
        mode = "PAIRED_CANDIDATE_MAPPING_READY" if len(mappings) >= 3 else "UNPAIRED_DIRECTIONAL_AB_READY"
        context = {
            "decision": auth["status"] if auth["status"] != "AUTH_PREFLIGHT_OK" else "AUTH_PREFLIGHT_OK",
            "mode": mode,
            "baseline": git_state,
            "auth": auth,
            "fixed_settings": FIXED_SETTINGS,
            "b": b_rows,
            "b_summary": _summary(b_rows),
            "parent_mappings": mappings,
            "a": list(prior.get("a") or []),
            "a_summary": _summary(list(prior.get("a") or [])),
            "paired_comparison": None,
            "pending_a": [
                {"parent_b_alpha_id": mapping.get("b_alpha_id", ""), "expression": mapping.get("parent_seed", "")}
                for mapping in mappings
            ],
            "submit_calls": 0,
            "settings_optimizer_used": False,
            "feedback_persistence_evidence_gap": True,
        }
        if auth["status"] != "AUTH_PREFLIGHT_OK":
            context["reason"] = auth.get("reason", auth["status"])
            _write_report(context)
            print(json.dumps(context, ensure_ascii=False, indent=2))
            return 5

        prior_a = [row for row in (prior.get("a") or []) if isinstance(row, dict)]
        prior_a = _resume_failed_progress(prior_a)
        rejection_decision = _rejection_only_decision(prior_a)
        if os.environ.get("V50_AB_PROGRESS_READBACK_ONLY") == "1":
            comparison = _paired_comparison(prior_a, b_rows, mappings)
            context.update(
                decision=rejection_decision or "EXTERNAL_VALIDATION_BLOCKED_TRUE_EXTERNAL",
                mode="PAIRED",
                b_summary=_summary(b_rows),
                a=prior_a,
                a_summary=_summary(prior_a),
                paired_comparison=comparison,
                field_catalog_probe=prior.get("field_catalog_probe"),
                pending_a=list(prior.get("pending_a") or []),
                reason=(
                    "Three distinct preserved v50 parent expressions were accepted as simulation requests "
                    "and later rejected by the platform without alpha_id; no resubmission performed."
                    if rejection_decision
                    else "existing progress endpoints did not provide three terminal platform rejections"
                ),
            )
            _write_report(context)
            print(json.dumps({"decision": context["decision"], "a": prior_a}, ensure_ascii=False, indent=2))
            return 0 if rejection_decision else 5

        if len(mappings) >= 3:
            catalog = _local_catalog()
            from alpha_mining.platform.gateway import PlatformGateway

            catalog_gateway = PlatformGateway(
                state_path=AUTH_STATE,
                database=DB,
                lock_path=LOCK,
                min_interval=3.0,
                timeout=60.0,
                settings_schema_path=SETTINGS_SCHEMA,
            )
            context["field_catalog_probe"] = _augment_catalog_from_platform(catalog, catalog_gateway)
            _write_report(context)
            raw_candidates: list[Any] = []
        else:
            raw_candidates, catalog = _catalog_and_raw_candidates()

        paired_items: list[dict[str, Any]] = []
        for mapping in mappings:
            candidate = SimpleNamespace(
                expression=mapping["parent_seed"],
                family="v50_preserved",
                source="V50_PRESERVED",
                score=0.0,
            )
            try:
                _adapt(candidate, catalog)
            except Exception as exc:
                mapping["adapt_error"] = f"{type(exc).__name__}: {exc}"
                continue
            mapping["a_expression"] = mapping["parent_seed"]
            paired_items.append({**mapping, "candidate": candidate})

        mode = "PAIRED" if len(paired_items) >= 3 else "UNPAIRED_DIRECTIONAL_AB"
        items = paired_items
        if mode != "PAIRED":
            items = []
            claimed = _existing_hashes()
            for candidate in raw_candidates:
                try:
                    proposal = _adapt(candidate, catalog)
                except Exception:
                    continue
                if proposal.exact_hash in claimed:
                    continue
                items.append({"candidate": candidate, "parent_b_alpha_id": ""})
                if len(items) == 4:
                    break
            if len(items) < 4:
                raise RuntimeError("EXTERNAL_VALIDATION_BLOCKED_TRUE_EXTERNAL: fewer than 4 legal unclaimed raw v50 candidates")

        completed_keys = {str(row.get("parent_b_alpha_id") or row.get("expression") or "") for row in prior_a if str(row.get("status") or "").upper() == "COMPLETE"}
        pending_items = [
            item for item in items
            if str(item.get("b_alpha_id") or item.get("candidate", {}).expression if hasattr(item.get("candidate"), "expression") else "") not in completed_keys
        ]
        context["mode"] = mode
        context["pending_a"] = [
            {"parent_b_alpha_id": item.get("b_alpha_id", ""), "expression": str(getattr(item.get("candidate"), "expression", ""))}
            for item in pending_items
        ]
        _write_report(context)
        a_new = _simulate(pending_items, catalog, checkpoint_context=context)
        a_rows = prior_a + a_new
        a_summary = _summary(a_rows)
        b_summary = _summary(b_rows)
        comparison = _paired_comparison(a_rows, b_rows, mappings) if mode == "PAIRED" else None
        decision = _decision(mode, a_summary, b_summary, comparison)
        context.update(
            decision=decision,
            b_summary=b_summary,
            a=a_rows,
            a_summary=a_summary,
            paired_comparison=comparison,
            pending_a=[],
        )
        _write_report(context)
        print(json.dumps({"decision": decision, "mode": mode, "a_summary": a_summary, "b_summary": b_summary, "comparison": comparison}, ensure_ascii=False, indent=2))
        return 0 if decision not in {"EXTERNAL_VALIDATION_BLOCKED", "EXTERNAL_VALIDATION_BLOCKED_TRUE_EXTERNAL"} else 5
    except Exception as exc:
        status = str(exc) if str(exc) in {"RATE_LIMIT_COOLDOWN", "BLOCKED_AUTH_INTERACTIVE_REQUIRED"} else "EXTERNAL_VALIDATION_BLOCKED_TRUE_EXTERNAL"
        if not context:
            context = {
                "baseline": git_state,
                "fixed_settings": FIXED_SETTINGS,
                "b": b_rows,
                "b_summary": _summary(b_rows) if b_rows else _summary([]),
                "parent_mappings": mappings,
                "a": [],
                "a_summary": _summary([]),
                "paired_comparison": None,
                "submit_calls": 0,
                "settings_optimizer_used": False,
                "feedback_persistence_evidence_gap": True,
            }
        context["decision"] = status
        context["reason"] = f"{type(exc).__name__}: {exc}"
        _write_report(context)
        print(context["reason"])
        print(status)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
