"""Evidence-driven WorldQuant Alpha recovery loop.

This module deliberately owns generation, simulation feedback, and recovery
state only.  It has no submission dependency and never writes the READY CSV.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from alpha_mining.domain.expression_normalization import (
    expression_identity,
    extract_fields,
    operator_topology,
)
from alpha_mining.domain.operator_registry import BASE_VARS
from alpha_mining.factory.orchestrator import FactoryOrchestrator
from alpha_mining.factory.v50_adapter import FactoryCandidateProposal
from alpha_mining.generation.feedback import CandidateFeedbackStore, record_candidate_outcome
from alpha_mining.generation.screening import CandidateScreeningPolicy, RejectionReason
from alpha_mining.generation.snapshots import LocalSnapshots, load_local_snapshots
from alpha_mining.legacy.features import extract_features
from alpha_mining.platform.catalog import ReadOnlyExpressionCatalog
from alpha_mining.platform.simulation_contract import SimulationSettingsContract
from alpha_mining.storage.migrations import migrate


ARMS = (
    "historical_winner_mutation",
    "near_pass_evolution",
    "historical_family_fresh",
    "broad_exploration",
    "ai_novel_hypothesis",
)
TARGET_QUALIFIED = 3
BATCH_SIZE = 20
WARMUP_PER_ARM = 4
LOCAL_POOL_SIZE = 1000

_CORE_METRICS = frozenset(("LOW_SHARPE", "LOW_FITNESS"))
_NAMED_BLOCKERS = frozenset(
    (
        "CONCENTRATED_WEIGHT",
        "IS_LADDER_SHARPE",
        "DATA_DIVERSITY",
        "SELF_CORRELATION",
        "PROD_CORRELATION",
        "PRODUCTION_CORRELATION",
        "REGULAR_SUBMISSION",
    )
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _checks(value: Any) -> list[dict[str, Any]]:
    parsed = value if isinstance(value, (list, dict)) else _loads(value, [])
    if isinstance(parsed, dict):
        parsed = parsed.get("checks", [])
    return [dict(item) for item in parsed if isinstance(item, Mapping)] if isinstance(parsed, list) else []


def _metrics(value: Any) -> dict[str, float]:
    raw = value if isinstance(value, Mapping) else _loads(value, {})
    out: dict[str, float] = {}
    if not isinstance(raw, Mapping):
        return out
    for key in ("sharpe", "fitness", "turnover", "returns", "drawdown", "margin"):
        try:
            if raw.get(key) is not None:
                out[key] = float(raw[key])
        except (TypeError, ValueError):
            continue
    return out


def _check_map(checks: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    severity = {"PASS": 0, "WARNING": 0, "PENDING": 1, "WAITING": 1, "UNKNOWN": 1, "MISSING": 1, "FAIL": 2, "FAILED": 2, "ERROR": 2, "REJECTED": 2}
    for item in checks:
        name = str(item.get("name") or item.get("check") or "").upper().strip()
        if name == "PRODUCTION_CORRELATION":
            name = "PROD_CORRELATION"
        if not name:
            continue
        current = result.get(name)
        candidate = dict(item)
        candidate["_status"] = str(item.get("result") or item.get("status") or "MISSING").upper()
        if current is None or severity.get(candidate["_status"], 2) >= severity.get(str(current.get("_status")), 2):
            result[name] = candidate
    return result


def _self_correlation(checks: Iterable[Mapping[str, Any]]) -> tuple[str, float | None]:
    item = _check_map(checks).get("SELF_CORRELATION")
    if not item:
        return "MISSING", None
    value: float | None = None
    for key in ("value", "correlation", "maxCorrelation"):
        try:
            if item.get(key) is not None:
                value = float(item[key])
                break
        except (TypeError, ValueError):
            continue
    return str(item.get("_status") or "MISSING"), value


def _is_blocking(name: str, item: Mapping[str, Any]) -> bool:
    if bool(item.get("mandatory")) or bool(item.get("blocking")):
        return True
    return name in _NAMED_BLOCKERS or name.startswith(("LOW_", "HIGH_", "MIN_", "MAX_"))


def classify_platform_result(
    *, status: str, metrics: Mapping[str, Any], checks: Iterable[Mapping[str, Any]]
) -> tuple[str, tuple[str, ...], str, float | None]:
    """Classify only with platform checks; local quality metrics have no role."""

    if str(status or "").upper() != "COMPLETE":
        return "FAR_FAIL", ("SIMULATION_NOT_COMPLETE",), "MISSING", None
    by_name = _check_map(checks)
    self_status, self_value = _self_correlation(checks)
    reasons: list[str] = []
    pending = False
    failed = False
    for name, item in by_name.items():
        if not _is_blocking(name, item):
            continue
        check_status = str(item.get("_status") or "MISSING")
        if check_status == "PASS":
            continue
        if check_status in {"PENDING", "WAITING", "UNKNOWN", "MISSING"}:
            pending = True
            reasons.append(f"{name}_{check_status}")
        else:
            failed = True
            reasons.append(f"{name}_{check_status}")
    missing_core = [name for name in _CORE_METRICS if name not in by_name]
    if missing_core:
        pending = True
        reasons.extend(f"{name}_MISSING" for name in sorted(missing_core))
    if failed:
        if _near_pass(metrics, by_name):
            return "NEAR_PASS", tuple(sorted(set(reasons))), self_status, self_value
        return "FAR_FAIL", tuple(sorted(set(reasons))), self_status, self_value
    if pending:
        return "WAITING_CHECKS", tuple(sorted(set(reasons))), self_status, self_value
    if self_status != "PASS":
        return "WAITING_CHECKS", (f"SELF_CORRELATION_{self_status}",), self_status, self_value
    return "QUALIFIED", ("ALL_PLATFORM_BLOCKING_CHECKS_PASSED",), self_status, self_value


def _near_pass(metrics: Mapping[str, Any], checks: Mapping[str, Mapping[str, Any]]) -> bool:
    failed = 0
    close = True
    for name in _CORE_METRICS:
        item = checks.get(name)
        if not item or str(item.get("_status")) == "PASS":
            continue
        failed += 1
        try:
            value = float(item.get("value", metrics.get(name.removeprefix("LOW_").lower())))
            limit = float(item["limit"])
            close = close and limit > 0 and value >= 0.9 * limit
        except (TypeError, ValueError, KeyError):
            close = False
    return failed == 1 and close


@dataclass(frozen=True)
class RecoveryCandidate:
    candidate_id: str
    expression: str
    search_arm: str
    dataset: str
    field_family: str
    parent_candidate_id: str = ""
    parent_history_id: str = ""
    lineage: Mapping[str, Any] | None = None


class RecoveryStore:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        migrate(self.database)

    def create_run(self, *, history_fingerprint: str, resume: bool) -> str:
        with sqlite3.connect(self.database) as con:
            if resume:
                row = con.execute(
                    "SELECT run_id FROM recovery_runs WHERE status IN ('RUNNING','AUTH_PAUSED','PARTIAL_ALPHA_FOUND','PLATFORM_LIMIT_REACHED_WITHOUT_SUCCESS','EXTERNAL_AUTH_BLOCKED') ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
                if row:
                    con.execute("UPDATE recovery_runs SET status='RUNNING',updated_at=? WHERE run_id=?", (_utc_now(), row[0]))
                    return str(row[0])
            run_id = "recovery_" + uuid.uuid4().hex
            now = _utc_now()
            con.execute(
                "INSERT INTO recovery_runs(run_id,started_at,updated_at,status,target_qualified,history_fingerprint,policy_json) VALUES (?,?,?,?,?,?,?)",
                (run_id, now, now, "RUNNING", TARGET_QUALIFIED, history_fingerprint, _json({"batch_size": BATCH_SIZE, "local_pool_size": LOCAL_POOL_SIZE, "arms": ARMS})),
            )
        return run_id

    def update_run(self, run_id: str, status: str, *, blocker: Mapping[str, Any] | None = None, simulations: int | None = None) -> None:
        columns = ["status=?", "updated_at=?"]
        values: list[Any] = [status, _utc_now()]
        if blocker is not None:
            columns.append("blocker_json=?")
            values.append(_json(dict(blocker)))
        if simulations is not None:
            columns.append("total_real_simulations=?")
            values.append(int(simulations))
        values.append(run_id)
        with sqlite3.connect(self.database) as con:
            con.execute("UPDATE recovery_runs SET " + ",".join(columns) + " WHERE run_id=?", values)

    def replace_history(self, rows: Iterable[Mapping[str, Any]], *, source_fingerprint: str) -> int:
        prepared = list(rows)
        with sqlite3.connect(self.database) as con:
            con.execute("DELETE FROM recovery_historical_index")
            con.executemany(
                """INSERT INTO recovery_historical_index
                (history_id,source_name,source_ref,alpha_id,expression,exact_hash,parameter_skeleton,field_skeleton,dataset,field_family,operator_topology,features_json,settings_json,metrics_json,checks_json,evidence_class,self_correlation_status,self_correlation_value,observed_at,source_fingerprint)
                VALUES (:history_id,:source_name,:source_ref,:alpha_id,:expression,:exact_hash,:parameter_skeleton,:field_skeleton,:dataset,:field_family,:operator_topology,:features_json,:settings_json,:metrics_json,:checks_json,:evidence_class,:self_correlation_status,:self_correlation_value,:observed_at,:source_fingerprint)""",
                [dict(row, source_fingerprint=source_fingerprint) for row in prepared],
            )
        return len(prepared)

    def history_rows(self, evidence: Iterable[str] | None = None) -> list[dict[str, Any]]:
        clauses, args = "", []
        if evidence:
            values = tuple(evidence)
            clauses = " WHERE evidence_class IN (" + ",".join("?" for _ in values) + ")"
            args = list(values)
        with sqlite3.connect(self.database) as con:
            rows = con.execute(
                "SELECT history_id,expression,dataset,field_family,operator_topology,features_json,settings_json,metrics_json,checks_json,evidence_class,alpha_id FROM recovery_historical_index" + clauses,
                args,
            ).fetchall()
        names = ("history_id", "expression", "dataset", "field_family", "operator_topology", "features_json", "settings_json", "metrics_json", "checks_json", "evidence_class", "alpha_id")
        return [dict(zip(names, row)) for row in rows]

    def history_hashes(self) -> tuple[set[str], set[str]]:
        with sqlite3.connect(self.database) as con:
            rows = con.execute("SELECT exact_hash,parameter_skeleton FROM recovery_historical_index").fetchall()
        return ({str(row[0]) for row in rows if row[0]}, {str(row[1]) for row in rows if row[1]})

    def history_is_current(self, fingerprint: str) -> bool:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                "SELECT COUNT(*) FROM recovery_historical_index WHERE source_fingerprint=?",
                (fingerprint,),
            ).fetchone()
        return bool(row and int(row[0]) > 0)

    def insert_candidate(self, run_id: str, candidate: RecoveryCandidate, settings: Mapping[str, Any]) -> bool:
        identity = expression_identity(candidate.expression)
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            cursor = con.execute(
                """INSERT OR IGNORE INTO recovery_candidates
                (candidate_id,run_id,expression,exact_hash,parameter_skeleton,field_skeleton,search_arm,parent_candidate_id,parent_history_id,lineage_json,dataset,field_family,operator_topology,settings_json,state,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (candidate.candidate_id, run_id, candidate.expression, identity.exact_hash, identity.parameter_skeleton, identity.field_skeleton,
                 candidate.search_arm, candidate.parent_candidate_id, candidate.parent_history_id, _json(candidate.lineage or {}), candidate.dataset,
                 candidate.field_family, operator_topology(candidate.expression), _json(dict(settings)), "LOCAL_ACCEPTED", now, now),
            )
        return bool(cursor.rowcount)

    def update_candidate(self, candidate_id: str, *, state: str, alpha_id: str = "", metrics: Mapping[str, Any] | None = None, checks: Iterable[Mapping[str, Any]] | None = None, self_status: str = "", self_value: float | None = None, request_hash: str = "", error_category: str = "", error_message: str = "") -> None:
        with sqlite3.connect(self.database) as con:
            con.execute(
                """UPDATE recovery_candidates SET state=?,alpha_id=CASE WHEN ?='' THEN alpha_id ELSE ? END,
                   metrics_json=COALESCE(?,metrics_json),checks_json=COALESCE(?,checks_json),self_correlation_status=CASE WHEN ?='' THEN self_correlation_status ELSE ? END,
                   self_correlation_value=?,request_hash=CASE WHEN ?='' THEN request_hash ELSE ? END,error_category=?,error_message=?,updated_at=? WHERE candidate_id=?""",
                (state, alpha_id, alpha_id, _json(dict(metrics)) if metrics is not None else None,
                 _json(list(checks)) if checks is not None else None, self_status, self_status, self_value,
                 request_hash, request_hash, error_category, error_message[:500], _utc_now(), candidate_id),
            )

    def candidate_rows(self, run_id: str, *, states: Iterable[str] | None = None) -> list[dict[str, Any]]:
        args: list[Any] = [run_id]
        where = "run_id=?"
        if states:
            wanted = tuple(states)
            where += " AND state IN (" + ",".join("?" for _ in wanted) + ")"
            args.extend(wanted)
        with sqlite3.connect(self.database) as con:
            rows = con.execute(
                "SELECT candidate_id,expression,search_arm,parent_candidate_id,parent_history_id,dataset,field_family,settings_json,state,alpha_id,metrics_json,checks_json,self_correlation_status,self_correlation_value,request_hash,error_category,error_message FROM recovery_candidates WHERE " + where + " ORDER BY created_at,candidate_id",
                args,
            ).fetchall()
        names = ("candidate_id", "expression", "search_arm", "parent_candidate_id", "parent_history_id", "dataset", "field_family", "settings_json", "state", "alpha_id", "metrics_json", "checks_json", "self_correlation_status", "self_correlation_value", "request_hash", "error_category", "error_message")
        return [dict(zip(names, row)) for row in rows]

    def orphan_request_rows(self, run_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database) as con:
            rows = con.execute(
                """SELECT c.candidate_id,c.expression,c.search_arm,c.parent_candidate_id,
                          c.parent_history_id,c.dataset,c.field_family,c.request_hash,
                          s.status,s.alpha_id,o.sharpe,o.fitness,o.turnover,o.checks_json
                   FROM recovery_candidates c
                   JOIN simulation_requests s ON s.request_hash=c.request_hash
                   LEFT JOIN candidate_outcomes o ON o.request_hash=c.request_hash
                  WHERE c.run_id=? AND c.state='LOCAL_ACCEPTED'""",
                (run_id,),
            ).fetchall()
        names = ("candidate_id","expression","search_arm","parent_candidate_id","parent_history_id","dataset","field_family","request_hash","request_status","alpha_id","sharpe","fitness","turnover","checks_json")
        return [dict(zip(names, row)) for row in rows]

    def qualified(self, run_id: str) -> list[dict[str, Any]]:
        return self.candidate_rows(run_id, states=("QUALIFIED",))

    def write_arm_window(self, run_id: str, batch_number: int, arm: str, allocation: int, stats: Mapping[str, Any], improved: bool) -> None:
        with sqlite3.connect(self.database) as con:
            con.execute(
                "INSERT OR REPLACE INTO recovery_arm_windows(window_id,run_id,batch_number,search_arm,allocation,statistics_json,improved,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (hashlib.sha256(f"{run_id}|{batch_number}|{arm}".encode()).hexdigest(), run_id, batch_number, arm, allocation, _json(dict(stats)), int(improved), _utc_now()),
            )

    def arm_windows(self, run_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database) as con:
            rows = con.execute("SELECT batch_number,search_arm,allocation,statistics_json,improved FROM recovery_arm_windows WHERE run_id=? ORDER BY batch_number,search_arm", (run_id,)).fetchall()
        return [{"batch_number": int(row[0]), "arm": str(row[1]), "allocation": int(row[2]), "statistics": _loads(row[3], {}), "improved": bool(row[4])} for row in rows]

    def retired_parent_ids(self, run_id: str) -> set[str]:
        """Return local parents whose last two child batches had no improvement.

        Parent retirement is deliberately based on real child outcomes only.
        Local screening, model opinion, and unsubmitted candidates cannot retire
        a lineage.
        """

        grouped: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for row in self.candidate_rows(run_id):
            parent = str(row["parent_candidate_id"] or "")
            if not parent:
                continue
            batch = _loads(row["settings_json"], {}).get("recovery_batch")
            try:
                batch_number = int(batch)
            except (TypeError, ValueError):
                continue
            grouped[parent][batch_number].append(row)
        retired: set[str] = set()
        for parent, by_batch in grouped.items():
            batches = sorted(by_batch)[-2:]
            if len(batches) < 2:
                continue
            parent_rows = [row for row in self.candidate_rows(run_id) if row["candidate_id"] == parent]
            parent_reward = _reward_value(parent_rows[0]) if parent_rows else 0.0
            improved = any(
                any(_lineage_improved(parent_rows[0], child, parent_reward) for child in by_batch[batch])
                for batch in batches
            )
            if not improved:
                retired.add(parent)
        return retired


def _history_fingerprint(paths: Iterable[Path]) -> str:
    parts = []
    for path in paths:
        try:
            stat = path.stat()
            parts.append(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}")
        except OSError:
            continue
    return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()


class HistoricalPlatformDataset:
    """Content-aware importer for platform evidence, never local quality proxies."""

    def __init__(self, root: str | Path, database: str | Path) -> None:
        self.root = Path(root)
        self.database = Path(database)
        self.quality_db = self.root / "research_memory_quality.sqlite"
        self.hopeful = self.root / "hopeful_alphas.jsonl"
        self.pilot = self.root / "tmp_alpha_quality_pilot_report_fresh.json"

    def fingerprint(self) -> str:
        return _history_fingerprint((self.quality_db, self.hopeful, self.pilot, self.database))

    def collect(self, *, field_metadata: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for source in (self._quality_rows(), self._hopeful_rows(), self._pilot_rows(), self._effective_rows()):
            for item in source:
                normalized = self._normalize(item, field_metadata or {})
                if not normalized:
                    continue
                key = f"{normalized['alpha_id']}|{normalized['exact_hash']}"
                old = rows.get(key)
                # Evidence quality comes before payload length.  A historical
                # import with an incomplete checks array must not overwrite a
                # platform performance winner merely because it has extra
                # unrelated check entries.
                evidence_rank = {"FULL_PASS": 4, "PERFORMANCE_PASS": 3, "NEAR_PASS": 2, "FAIL": 1, "LOCAL_ONLY": 0}
                if old is None or (
                    evidence_rank[str(normalized["evidence_class"])] > evidence_rank[str(old["evidence_class"])]
                    or (
                        evidence_rank[str(normalized["evidence_class"])] == evidence_rank[str(old["evidence_class"])]
                        and len(_checks(normalized["checks_json"])) > len(_checks(old["checks_json"]))
                    )
                ):
                    rows[key] = normalized
        return list(rows.values())

    def _quality_rows(self) -> Iterable[dict[str, Any]]:
        if not self.quality_db.is_file():
            return ()
        uri = f"{self.quality_db.resolve().as_uri()}?mode=ro"
        out: list[dict[str, Any]] = []
        try:
            with sqlite3.connect(uri, uri=True) as con:
                tables = {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "legacy_alphas" not in tables:
                    return ()
                for row in con.execute("SELECT legacy_id,alpha_id,expression,family,settings_json,metrics_json,checks_json,observed_at FROM legacy_alphas"):
                    out.append({"source_name": "research_memory_quality.sqlite", "source_ref": str(row[0]), "alpha_id": row[1], "expression": row[2], "family": row[3], "settings": _loads(row[4], {}), "metrics": _metrics(row[5]), "checks": _checks(row[6]), "observed_at": row[7]})
        except sqlite3.Error:
            return ()
        return out

    def _hopeful_rows(self) -> Iterable[dict[str, Any]]:
        if not self.hopeful.is_file():
            return ()
        out: list[dict[str, Any]] = []
        with self.hopeful.open("r", encoding="utf-8", errors="ignore") as handle:
            for index, line in enumerate(handle, start=1):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, Mapping):
                    continue
                out.append({"source_name": "hopeful_alphas.jsonl", "source_ref": str(index), "alpha_id": item.get("alpha_id"), "expression": item.get("expression"), "family": (item.get("meta") or {}).get("family", "") if isinstance(item.get("meta"), Mapping) else "", "settings": item.get("settings") or {}, "metrics": _metrics(item.get("metrics")), "checks": _checks(item.get("checks")), "observed_at": item.get("queued_at") or ""})
        return out

    def _pilot_rows(self) -> Iterable[dict[str, Any]]:
        if not self.pilot.is_file():
            return ()
        try:
            payload = json.loads(self.pilot.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        out: list[dict[str, Any]] = []
        for index, item in enumerate([*(payload.get("round1") or []), *(payload.get("round2") or [])], start=1):
            if not isinstance(item, Mapping):
                continue
            out.append({"source_name": "fresh_pilot", "source_ref": str(index), "alpha_id": item.get("alpha_id"), "expression": item.get("expression"), "family": item.get("research_family") or item.get("candidate_source") or "", "settings": item.get("settings") or {}, "metrics": _metrics(item.get("metrics")), "checks": _checks(item.get("checks")), "observed_at": payload.get("observed_at") or ""})
        return out

    def _effective_rows(self) -> Iterable[dict[str, Any]]:
        if not self.database.is_file():
            return ()
        out: list[dict[str, Any]] = []
        try:
            with sqlite3.connect(self.database) as con:
                cols = {str(row[1]) for row in con.execute("PRAGMA table_info(candidate_outcomes)")}
                if "candidate_outcomes" not in {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                    return ()
                provenance = "provenance" if "provenance" in cols else "'UNVERIFIED'"
                for row in con.execute(f"SELECT request_hash,expression,strategy_family,dataset,sharpe,fitness,turnover,checks_json,observed_at,{provenance} FROM candidate_outcomes"):
                    if str(row[9]) != "PLATFORM_VERIFIED":
                        continue
                    out.append({"source_name": "effective_candidate_outcomes", "source_ref": str(row[0]), "alpha_id": "", "expression": row[1], "family": row[2], "dataset": row[3], "settings": {}, "metrics": {key: value for key, value in (("sharpe", row[4]), ("fitness", row[5]), ("turnover", row[6])) if value is not None}, "checks": _checks(row[7]), "observed_at": row[8]})
        except sqlite3.Error:
            return ()
        return out

    def _normalize(self, item: Mapping[str, Any], fields: Mapping[str, Any]) -> dict[str, Any] | None:
        expression = str(item.get("expression") or "").strip()
        alpha_id = str(item.get("alpha_id") or "").strip()
        checks = _checks(item.get("checks"))
        if not expression:
            return None
        try:
            identity = expression_identity(expression)
            features = extract_features(expression)
            exact_hash = identity.exact_hash
            parameter_skeleton = identity.parameter_skeleton
            field_skeleton = identity.field_skeleton
            topology = features.topology
            features_payload = {
                "family": item.get("family") or "", "fields": features.fields,
                "field_categories": features.field_categories, "windows": features.windows,
                "grouping": features.grouping, "normalizers": features.normalizers,
                "operators": features.operators, "operator_count": features.operator_count,
                "parse_status": "PARSED",
            }
            extracted_fields = extract_fields(expression)
        except Exception:
            # Historical platform evidence remains evidence even when an older
            # expression dialect is no longer accepted by today's AST.  It is
            # excluded from typed mutation but still blocks exact replay and
            # contributes verified metric/check statistics.
            canonical = re.sub(r"\s+", "", expression)
            exact_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            parameter_skeleton = canonical
            field_skeleton = canonical
            topology = "UNPARSED_HISTORICAL"
            features_payload = {"family": item.get("family") or "", "parse_status": "UNPARSED_HISTORICAL"}
            extracted_fields = ()
        owned = [field for field in extracted_fields if field not in BASE_VARS]
        datasets = {str(getattr(fields.get(field), "dataset_id", "") or "") for field in owned if fields.get(field) is not None}
        dataset = str(item.get("dataset") or (next(iter(datasets)) if len(datasets) == 1 else ""))
        field_families = {str(getattr(fields.get(field), "field_type", "") or "") for field in owned if fields.get(field) is not None}
        field_family = next(iter(field_families)) if len(field_families) == 1 else ",".join(sorted(field_families))
        evidence = _historical_evidence(alpha_id, item.get("metrics") or {}, checks)
        self_status, self_value = _self_correlation(checks)
        history_id = hashlib.sha256(f"{item.get('source_name')}\0{item.get('source_ref')}\0{alpha_id}\0{exact_hash}".encode()).hexdigest()
        return {
            "history_id": history_id, "source_name": str(item.get("source_name") or "unknown"), "source_ref": str(item.get("source_ref") or ""),
            "alpha_id": alpha_id, "expression": expression, "exact_hash": exact_hash, "parameter_skeleton": parameter_skeleton,
            "field_skeleton": field_skeleton, "dataset": dataset, "field_family": field_family, "operator_topology": topology,
            "features_json": _json(features_payload),
            "settings_json": _json(item.get("settings") or {}), "metrics_json": _json(_metrics(item.get("metrics"))), "checks_json": _json(checks),
            "evidence_class": evidence, "self_correlation_status": self_status, "self_correlation_value": self_value, "observed_at": str(item.get("observed_at") or ""),
        }


def _historical_evidence(alpha_id: str, metrics: Mapping[str, Any], checks: list[dict[str, Any]]) -> str:
    if not alpha_id or not checks:
        return "LOCAL_ONLY"
    live, _, _, _ = classify_platform_result(status="COMPLETE", metrics=metrics, checks=checks)
    if live == "QUALIFIED":
        return "FULL_PASS"
    if live == "WAITING_CHECKS":
        by_name = _check_map(checks)
        if all(str(by_name.get(name, {}).get("_status")) == "PASS" for name in _CORE_METRICS):
            return "PERFORMANCE_PASS"
    if live == "NEAR_PASS":
        return "NEAR_PASS"
    return "FAIL"


class RecoveryCandidateGenerator:
    def __init__(self, store: RecoveryStore, snapshots: LocalSnapshots, run_id: str, *, hypothesis_provider: Callable[[Mapping[str, Any]], Iterable[Mapping[str, Any]]] | None = None) -> None:
        self.store, self.snapshots, self.run_id = store, snapshots, run_id
        self.fields = snapshots.catalog.fields
        self.hypothesis_provider = hypothesis_provider
        self._history_exact, self._history_skeleton = store.history_hashes()

    def generate(self, arm: str, count: int) -> list[RecoveryCandidate]:
        if arm == "historical_winner_mutation":
            raw = self._winner_mutations(count)
        elif arm == "near_pass_evolution":
            raw = self._near_pass_mutations(count)
        elif arm == "historical_family_fresh":
            raw = self._fresh_family(count)
        elif arm == "broad_exploration":
            raw = self._v50(count)
        else:
            raw = self._ai_hypotheses(count)
        return self._screen(raw)

    def _history(self, classes: Iterable[str]) -> list[dict[str, Any]]:
        return self.store.history_rows(classes)

    def _winner_mutations(self, count: int) -> list[RecoveryCandidate]:
        out: list[RecoveryCandidate] = []
        for index, row in enumerate(self._history(("PERFORMANCE_PASS", "FULL_PASS"))):
            if len(out) >= count:
                break
            mutated = self._orthogonal_mutation(str(row["expression"]), index)
            if mutated:
                out.append(self._candidate(mutated, "historical_winner_mutation", parent_history_id=str(row["history_id"]), mutation="typed_field_or_topology"))
        return out

    def _near_pass_mutations(self, count: int) -> list[RecoveryCandidate]:
        out: list[RecoveryCandidate] = []
        retired = self.store.retired_parent_ids(self.run_id)
        parents = self.store.candidate_rows(self.run_id, states=("NEAR_PASS", "FAR_FAIL"))
        for index, row in enumerate(parents):
            if len(out) >= count:
                break
            if str(row["candidate_id"]) in retired:
                continue
            checks = _check_map(_checks(row["checks_json"]))
            self_corr_fail = str(checks.get("SELF_CORRELATION", {}).get("_status") or "") in {"FAIL", "FAILED"}
            if str(row["state"]) != "NEAR_PASS" and not self_corr_fail:
                continue
            mutated = self._orthogonal_mutation(str(row["expression"]), index + 17)
            if mutated:
                out.append(self._candidate(mutated, "near_pass_evolution", parent_candidate_id=str(row["candidate_id"]), mutation="orthogonal_parent_mutation"))
        if out:
            return out
        for index, row in enumerate(self._history(("NEAR_PASS",))):
            if len(out) >= count:
                break
            mutated = self._orthogonal_mutation(str(row["expression"]), index + 31)
            if mutated:
                out.append(self._candidate(mutated, "near_pass_evolution", parent_history_id=str(row["history_id"]), mutation="historical_near_mutation"))
        return out

    def _fresh_family(self, count: int) -> list[RecoveryCandidate]:
        out: list[RecoveryCandidate] = []
        winner_datasets = Counter(
            str(row["dataset"])
            for row in self._history(("PERFORMANCE_PASS", "FULL_PASS"))
            if str(row["dataset"])
        )
        fields = [
            field for field in self.fields.values()
            if not winner_datasets or str(field.dataset_id) in winner_datasets
        ]
        fields.sort(
            key=lambda item: (-winner_datasets.get(str(item.dataset_id), 0), str(item.dataset_id), item.field_id)
        )
        patterns = (
            "rank(ts_delta({field},63))",
            "group_neutralize(rank(ts_delta({field},126)),sector)",
            "ts_rank(ts_zscore({field},126),63)",
            "group_neutralize(ts_rank({field},63),industry)",
            "rank(ts_mean({field},126)-ts_mean({field},21))",
        )
        for index, field in enumerate(fields):
            if len(out) >= count:
                break
            rendered = self._field_value(field)
            if not rendered:
                continue
            expression = patterns[index % len(patterns)].format(field=rendered)
            out.append(self._candidate(expression, "historical_family_fresh", mutation="empirical_family_resample"))
        return out

    def _v50(self, count: int) -> list[RecoveryCandidate]:
        try:
            from alpha_mining.generation.v50_kernel import V50Kernel

            # V50's historical factory expands its complete field catalog
            # before applying ``seed_pool_size``.  Recovery needs the broad
            # arm to be cheap enough that platform feedback, not local CPU,
            # controls the loop.  Keep a round-robin view across datasets so
            # this remains broad exploration rather than a single-dataset cut.
            buckets: dict[str, list[Any]] = defaultdict(list)
            for field in sorted(self.fields.values(), key=lambda item: (str(item.dataset_id), item.field_id)):
                buckets[str(field.dataset_id)].append(field)
            selected: list[Any] = []
            offset = 0
            while len(selected) < 256:
                added = False
                for dataset in sorted(buckets):
                    values = buckets[dataset]
                    if offset < len(values):
                        selected.append(values[offset])
                        added = True
                        if len(selected) >= 256:
                            break
                if not added:
                    break
                offset += 1
            limited_catalog = replace(self.snapshots.catalog, fields={field.field_id: field for field in selected})
            limited_snapshots = replace(self.snapshots, catalog=limited_catalog)
            batch = V50Kernel(seed_pool_size=max(24, min(120, count))).generate_batch(limited_snapshots)
            return [self._candidate(str(item.expression), "broad_exploration", mutation="v50_native") for item in batch.candidates[:count]]
        except Exception:
            return []

    def _ai_hypotheses(self, count: int) -> list[RecoveryCandidate]:
        if self.hypothesis_provider is None:
            return []
        out: list[RecoveryCandidate] = []
        context = {"datasets": sorted({item.dataset_id for item in self.fields.values()}), "max_candidates": count}
        for item in self.hypothesis_provider(context):
            if not isinstance(item, Mapping):
                continue
            required = ("hypothesis", "dataset_family", "mechanism", "expected_turnover", "orthogonal_reason")
            if any(not str(item.get(key) or "").strip() for key in required):
                continue
            expression = str(item.get("expression_template") or "").strip()
            if expression:
                out.append(self._candidate(expression, "ai_novel_hypothesis", mutation="grounded_ai_hypothesis", lineage=dict(item)))
            if len(out) >= count:
                break
        return out

    def _field_value(self, field: Any) -> str:
        field_type = str(getattr(field, "field_type", "") or "").upper()
        field_id = str(getattr(field, "field_id", "") or "")
        if not field_id:
            return ""
        return f"vec_avg({field_id})" if field_type == "VECTOR" else field_id

    def _orthogonal_mutation(self, expression: str, index: int) -> str:
        try:
            owned = [field for field in extract_fields(expression) if field in self.fields and field not in BASE_VARS]
        except Exception:
            return ""
        if owned:
            current = owned[index % len(owned)]
            meta = self.fields[current]
            alternatives = [item for item in self.fields.values() if item.field_id != current and item.field_type == meta.field_type and item.dataset_id != meta.dataset_id]
            if alternatives:
                replacement = alternatives[index % len(alternatives)]
                return expression.replace(current, self._field_value(replacement), 1)
        if "group_neutralize(" not in expression:
            return f"group_neutralize(rank({expression}),sector)"
        if "ts_rank(" not in expression:
            return f"ts_rank({expression},63)"
        return ""

    def _candidate(self, expression: str, arm: str, *, parent_candidate_id: str = "", parent_history_id: str = "", mutation: str = "", lineage: Mapping[str, Any] | None = None) -> RecoveryCandidate:
        identity = expression_identity(expression)
        owned = [field for field in extract_fields(expression) if field in self.fields and field not in BASE_VARS]
        datasets = {self.fields[field].dataset_id for field in owned}
        families = {self.fields[field].field_type for field in owned}
        candidate_id = "recovery_" + hashlib.sha256(f"{self.run_id}\0{arm}\0{identity.exact_hash}".encode()).hexdigest()[:32]
        return RecoveryCandidate(candidate_id, expression, arm, next(iter(datasets)) if len(datasets) == 1 else "", next(iter(families)) if len(families) == 1 else ",".join(sorted(families)), parent_candidate_id, parent_history_id, {"mutation": mutation, **dict(lineage or {})})

    def _screen(self, candidates: Iterable[RecoveryCandidate]) -> list[RecoveryCandidate]:
        catalog = ReadOnlyExpressionCatalog(self.snapshots.catalog, max_age_hours=336)
        policy = CandidateScreeningPolicy(catalog=catalog, group_rank_enabled=True, region="USA", universe="TOP3000", delay=1)
        existing = self.store.candidate_rows(self.run_id)
        seen_hashes = set(self._history_exact) | {str(row["candidate_id"]) for row in ()}
        seen_skeletons: set[str] = set()
        result: list[RecoveryCandidate] = []
        existing_exact = {expression_identity(str(row["expression"])).exact_hash for row in existing}
        for candidate in candidates:
            try:
                identity = expression_identity(candidate.expression)
            except Exception:
                continue
            if identity.exact_hash in seen_hashes or identity.exact_hash in existing_exact or identity.parameter_skeleton in self._history_skeleton:
                continue
            decision = policy.screen_expression(candidate.expression, round_seen_hashes=seen_hashes, round_seen_skeletons=seen_skeletons, expected_dataset_id=candidate.dataset or None)
            if decision not in (None, RejectionReason.NONE):
                continue
            seen_hashes.add(identity.exact_hash)
            seen_skeletons.add(identity.field_skeleton)
            result.append(candidate)
        return result


class RecoveryRunner:
    def __init__(self, *, database: str | Path, root: str | Path = ".", catalog_dir: str | Path = ".validation_workspace", auth_state_file: str | Path = ".wq_auth_state.json", lock_path: str | Path = "worldquant_api.lock", transport: str = "auto", browser_profile_dir: str | Path = ".validation_workspace/wq_browser_profile", gateway: Any | None = None, sleeper: Callable[[float], None] = time.sleep, hypothesis_provider: Callable[[Mapping[str, Any]], Iterable[Mapping[str, Any]]] | None = None) -> None:
        self.database, self.root, self.catalog_dir = Path(database), Path(root), Path(catalog_dir)
        self.auth_state_file, self.lock_path = Path(auth_state_file), Path(lock_path)
        self.transport, self.browser_profile_dir = str(transport or "auto"), Path(browser_profile_dir)
        self.gateway, self.sleeper, self.hypothesis_provider = gateway, sleeper, hypothesis_provider
        self.store = RecoveryStore(self.database)

    def analyze(self) -> dict[str, Any]:
        snapshots = load_local_snapshots(root=self.root, catalog_dir=self.catalog_dir, database=self.database)
        dataset = HistoricalPlatformDataset(self.root, self.database)
        rows = dataset.collect(field_metadata=snapshots.catalog.fields)
        fingerprint = dataset.fingerprint()
        count = self.store.replace_history(rows, source_fingerprint=fingerprint)
        buckets = Counter(str(row["evidence_class"]) for row in rows)
        source_evidence = Counter(
            f"{row['source_name']}:{row['evidence_class']}" for row in rows
        )
        comparison = self._empirical_regions(rows)
        return {
            "history_rows": count,
            "evidence_classes": dict(sorted(buckets.items())),
            "source_evidence": dict(sorted(source_evidence.items())),
            "fingerprint": fingerprint,
            "empirical_regions": comparison,
        }

    def run(self, *, resume: bool = False, max_batches: int = 0) -> dict[str, Any]:
        historical = HistoricalPlatformDataset(self.root, self.database)
        fingerprint = historical.fingerprint()
        if self.store.history_is_current(fingerprint):
            rows = self.store.history_rows()
            analysis = {
                "history_rows": len(rows),
                "fingerprint": fingerprint,
                "reused_historical_index": True,
            }
        else:
            analysis = self.analyze()
        snapshots = load_local_snapshots(root=self.root, catalog_dir=self.catalog_dir, database=self.database)
        try:
            contract = SimulationSettingsContract.load(self.catalog_dir / ".alpha_simulation_settings_cache.json")
            # These request-only keys are platform-observed requirements. They
            # are passed through the contract so unknown keys are retained.
            settings = contract.prepare({**contract.defaults, "instrumentType": "EQUITY"})
            validation_capable = True
        except ValueError:
            # Chain A remains usable with no current validation capability.
            # The next --resume reloads the contract before consuming pending
            # work; no stale cache is treated as a platform verdict.
            settings = {}
            validation_capable = False
        # A current, verifiable settings contract is a prerequisite to
        # creating a recoverable simulation run.  This prevents a stale local
        # capability cache from being mistaken for platform progress.
        run_id = self.store.create_run(history_fingerprint=str(analysis["fingerprint"]), resume=resume)
        gateway = self.gateway or self._gateway()
        generator = RecoveryCandidateGenerator(self.store, snapshots, run_id, hypothesis_provider=self.hypothesis_provider)
        feedback = CandidateFeedbackStore(self.database)
        executor = FactoryOrchestrator(self.database, gateway)
        self._reconcile_orphans(run_id, gateway, feedback)
        batch_number = len({row["batch_number"] for row in self.store.arm_windows(run_id)})
        batches_this_call = 0
        empty_rounds = 0
        while max_batches <= 0 or batches_this_call < max_batches:
            if not validation_capable:
                allocations = self._allocations(run_id)
                batch_number += 1
                batches_this_call += 1
                self._queue_candidates(run_id, generator, settings, allocations, batch_number)
                self.store.update_run(run_id, "AUTH_PAUSED", blocker={"kind": "PLATFORM_CAPABILITY", "detail": "simulation settings unavailable or stale"})
                return self.status(run_id)
            pending_blocker = self._drain_pending(run_id, gateway, feedback, executor)
            if pending_blocker:
                return self._stop_for_blocker(run_id, pending_blocker)
            blocker = self._refresh_waiting(run_id, gateway, feedback)
            if blocker:
                return self._stop_for_blocker(run_id, blocker)
            if len(self.store.qualified(run_id)) >= TARGET_QUALIFIED:
                self.store.update_run(run_id, "SUCCESS_ALPHA_FACTORY_RECOVERED")
                return self.status(run_id)
            allocations = self._allocations(run_id)
            batch_number += 1
            batches_this_call += 1
            attempted: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for arm, quota in allocations.items():
                pool = generator.generate(arm, max(20, LOCAL_POOL_SIZE // len(ARMS)))
                for candidate in pool[:quota]:
                    candidate = replace(
                        candidate,
                        lineage={**dict(candidate.lineage or {}), "batch_number": batch_number},
                    )
                    candidate_settings = {**dict(settings), "recovery_batch": batch_number}
                    if not self.store.insert_candidate(run_id, candidate, candidate_settings):
                        continue
                    outcome = self._simulate_candidate(run_id, candidate, candidate_settings, executor, feedback)
                    attempted[arm].append(outcome)
                    if outcome.get("blocker"):
                        if outcome["blocker"].get("kind") == "AUTH":
                            self.store.update_candidate(candidate.candidate_id, state="PENDING_SIMULATION", error_category="AUTH_PAUSED", error_message=str(outcome["blocker"].get("detail") or ""))
                            self._queue_candidates(run_id, generator, settings, allocations, batch_number)
                            self._record_windows(run_id, batch_number, allocations, attempted)
                            self.store.update_run(run_id, "AUTH_PAUSED", blocker=outcome["blocker"])
                            return self.status(run_id)
                        self._record_windows(run_id, batch_number, allocations, attempted)
                        return self._stop_for_blocker(run_id, outcome["blocker"])
                    if len(self.store.qualified(run_id)) >= TARGET_QUALIFIED:
                        self._record_windows(run_id, batch_number, allocations, attempted)
                        self.store.update_run(run_id, "SUCCESS_ALPHA_FACTORY_RECOVERED")
                        return self.status(run_id)
            self._record_windows(run_id, batch_number, allocations, attempted)
            if not attempted:
                empty_rounds += 1
                # Candidate exhaustion is local state, not a platform verdict;
                # keep the run resumable while preventing a hot CPU/SQLite loop.
                self.sleeper(min(60.0, float(2 ** min(empty_rounds, 6))))
            else:
                empty_rounds = 0
        self.store.update_run(run_id, "RUNNING")
        return self.status(run_id)

    def run_simulation_poc(self) -> dict[str, Any]:
        """Consume exactly one already-pending candidate through the normal lease path.

        This is intentionally separate from ``run()``: it proves browser POST,
        polling, alpha detail, checks, and feedback without starting a batch.
        """

        if self.transport != "browser":
            raise ValueError("the simulation POC requires --transport browser")
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                """SELECT run_id,candidate_id,expression,search_arm,parent_candidate_id,
                          parent_history_id,dataset,field_family,settings_json
                   FROM recovery_candidates WHERE state='PENDING_SIMULATION'
                   ORDER BY created_at,candidate_id LIMIT 1"""
            ).fetchone()
        if row is None:
            return {
                "BROWSER_VALIDATION_POC_PASS": False,
                "SIMULATION_POC": {"status": "NO_PENDING_CANDIDATE", "no_submit": True},
            }
        names = (
            "run_id", "candidate_id", "expression", "search_arm", "parent_candidate_id",
            "parent_history_id", "dataset", "field_family", "settings_json",
        )
        pending = dict(zip(names, row))
        candidate = self._candidate_from_row(pending)
        gateway = self.gateway or self._gateway()
        feedback = CandidateFeedbackStore(self.database)
        executor = FactoryOrchestrator(self.database, gateway)
        outcome = self._simulate_candidate(
            str(pending["run_id"]), candidate, _loads(pending["settings_json"], {}), executor, feedback
        )
        if outcome.get("blocker"):
            self.store.update_candidate(
                candidate.candidate_id,
                state="PENDING_SIMULATION",
                error_category="AUTH_PAUSED",
                error_message=str(outcome["blocker"].get("detail") or ""),
            )
            self.store.update_run(str(pending["run_id"]), "AUTH_PAUSED", blocker=outcome["blocker"])
            return {
                "BROWSER_VALIDATION_POC_PASS": False,
                "SIMULATION_POC": {"status": "AUTH_PAUSED", "no_submit": True},
            }
        completed = next(
            (item for item in self.store.candidate_rows(str(pending["run_id"])) if item["candidate_id"] == candidate.candidate_id),
            {},
        )
        status = str(completed.get("state") or outcome.get("state") or "SIMULATION_ERROR")
        alpha_id = str(completed.get("alpha_id") or "")
        passed = bool(alpha_id) and status not in {"SIMULATION_ERROR", "EXTERNAL_ERROR"}
        return {
            "BROWSER_VALIDATION_POC_PASS": passed,
            "SIMULATION_POC": {"alpha_id": alpha_id, "status": status, "no_submit": True},
        }

    @staticmethod
    def _candidate_from_row(row: Mapping[str, Any]) -> RecoveryCandidate:
        return RecoveryCandidate(
            str(row["candidate_id"]), str(row["expression"]), str(row["search_arm"]),
            str(row["dataset"]), str(row["field_family"]),
            str(row.get("parent_candidate_id") or ""), str(row.get("parent_history_id") or ""),
        )

    def _queue_candidates(self, run_id: str, generator: RecoveryCandidateGenerator, settings: Mapping[str, Any], allocations: Mapping[str, int], batch_number: int) -> int:
        queued = 0
        for arm, quota in allocations.items():
            pool = generator.generate(arm, max(20, LOCAL_POOL_SIZE // len(ARMS)))
            for candidate in pool[:quota]:
                candidate = replace(candidate, lineage={**dict(candidate.lineage or {}), "batch_number": batch_number})
                candidate_settings = {**dict(settings), "recovery_batch": batch_number}
                if self.store.insert_candidate(run_id, candidate, candidate_settings):
                    self.store.update_candidate(candidate.candidate_id, state="PENDING_SIMULATION")
                    queued += 1
        return queued

    def _drain_pending(self, run_id: str, gateway: Any, feedback: CandidateFeedbackStore, executor: FactoryOrchestrator) -> dict[str, Any] | None:
        for row in self.store.candidate_rows(run_id, states=("PENDING_SIMULATION",)):
            candidate = self._candidate_from_row(row)
            settings = _loads(row["settings_json"], {})
            outcome = self._simulate_candidate(run_id, candidate, settings, executor, feedback)
            if outcome.get("blocker"):
                self.store.update_candidate(candidate.candidate_id, state="PENDING_SIMULATION", error_category="AUTH_PAUSED", error_message=str(outcome["blocker"].get("detail") or ""))
                return outcome["blocker"]
        return None

    def _gateway(self) -> Any:
        from alpha_mining.platform.gateway import PlatformGateway

        browser_transport = None
        if self.transport == "browser":
            from alpha_mining.platform.browser_transport import BrowserBackedWorldQuantTransport

            browser_transport = BrowserBackedWorldQuantTransport(
                profile_dir=self.browser_profile_dir,
                database=self.database,
                lock_path=self.lock_path,
                min_interval=2.0,
            )

        return PlatformGateway(
            state_path=self.auth_state_file,
            database=self.database,
            lock_path=self.lock_path,
            min_interval=2.0,
            settings_schema_path=self.catalog_dir / ".alpha_simulation_settings_cache.json",
            require_stored_session=True,
            allow_auth_replay=False,
            transport=browser_transport,
        )

    def _simulate_candidate(self, run_id: str, candidate: RecoveryCandidate, settings: Mapping[str, Any], executor: FactoryOrchestrator, feedback: CandidateFeedbackStore) -> dict[str, Any]:
        proposal = self._proposal(candidate)
        # ``recovery_batch`` is local lineage metadata, never a platform
        # simulation setting.
        platform_settings = {key: value for key, value in settings.items() if key != "recovery_batch"}
        result = executor.execute_candidate(proposal, platform_settings)
        if result.result is None:
            blocker = self._external_blocker(result.error_category, result.error_message)
            state = "EXTERNAL_ERROR" if blocker else "SIMULATION_ERROR"
            self.store.update_candidate(candidate.candidate_id, state=state, request_hash=result.request_hash, error_category=result.error_category, error_message=result.error_message)
            record_candidate_outcome(feedback, proposal, result.request_hash or candidate.candidate_id, outcome="FAILED", result=None, error_category=result.error_category, error_message=result.error_message)
            return {"state": state, "blocker": blocker}
        simulated = result.result
        state, reasons, self_status, self_value = classify_platform_result(status=simulated.status, metrics=simulated.metrics, checks=simulated.checks)
        self.store.update_candidate(candidate.candidate_id, state=state, alpha_id=simulated.alpha_id, metrics=simulated.metrics, checks=simulated.checks, self_status=self_status, self_value=self_value, request_hash=result.request_hash)
        feedback_outcome = "PASS" if state == "QUALIFIED" else state if state in {"WAITING_CHECKS", "NEAR_PASS", "FAR_FAIL"} else "FAILED"
        record_candidate_outcome(feedback, proposal, result.request_hash or candidate.candidate_id, outcome=feedback_outcome, result=simulated, quality_reasons=reasons, provenance="PLATFORM_VERIFIED")
        return {"state": state, "metrics": simulated.metrics}

    def _refresh_waiting(self, run_id: str, gateway: Any, feedback: CandidateFeedbackStore) -> dict[str, Any] | None:
        for row in self.store.candidate_rows(run_id, states=("WAITING_CHECKS",)):
            alpha_id = str(row["alpha_id"] or "")
            if not alpha_id:
                continue
            try:
                fresh = gateway.refresh_alpha_checks(alpha_id)
            except Exception as exc:
                blocker = self._external_blocker(type(exc).__name__, str(exc))
                if blocker:
                    return blocker
                continue
            checks, metrics = _checks(fresh.get("checks")), _metrics(fresh.get("metrics"))
            state, reasons, self_status, self_value = classify_platform_result(status="COMPLETE", metrics=metrics, checks=checks)
            self.store.update_candidate(str(row["candidate_id"]), state=state, alpha_id=alpha_id, metrics=metrics, checks=checks, self_status=self_status, self_value=self_value)
            candidate = RecoveryCandidate(str(row["candidate_id"]), str(row["expression"]), str(row["search_arm"]), str(row["dataset"]), str(row["field_family"]), str(row["parent_candidate_id"]), str(row["parent_history_id"]))
            outcome = "PASS" if state == "QUALIFIED" else state if state in {"WAITING_CHECKS", "NEAR_PASS", "FAR_FAIL"} else "FAILED"
            refresh_result = type("RefreshResult", (), {"metrics": metrics, "checks": checks})()
            record_candidate_outcome(feedback, self._proposal(candidate), str(row["request_hash"] or row["candidate_id"]), outcome=outcome, result=refresh_result, quality_reasons=reasons, provenance="PLATFORM_VERIFIED")
        return None

    def _reconcile_orphans(self, run_id: str, gateway: Any, feedback: CandidateFeedbackStore) -> None:
        """Close candidates whose request completed before a process crash.

        COMPLETE requests already have their platform result in candidate_outcomes;
        requests carrying an alpha id can be refreshed without another POST.
        Requests without a checkpoint remain untouched until the shared lease
        policy can prove they are recoverable.
        """

        for row in self.store.orphan_request_rows(run_id):
            alpha_id = str(row["alpha_id"] or "")
            checks = _checks(row["checks_json"])
            metrics = _metrics({"sharpe": row["sharpe"], "fitness": row["fitness"], "turnover": row["turnover"]})
            if row["request_status"] == "COMPLETE" and alpha_id:
                state, _reasons, self_status, self_value = classify_platform_result(status="COMPLETE", metrics=metrics, checks=checks)
                self.store.update_candidate(str(row["candidate_id"]), state=state, alpha_id=alpha_id, metrics=metrics, checks=checks, self_status=self_status, self_value=self_value)
                continue
            if not alpha_id:
                continue
            try:
                fresh = gateway.refresh_alpha_checks(alpha_id)
            except Exception:
                continue
            checks, metrics = _checks(fresh.get("checks")), _metrics(fresh.get("metrics"))
            state, reasons, self_status, self_value = classify_platform_result(status="COMPLETE", metrics=metrics, checks=checks)
            self.store.update_candidate(str(row["candidate_id"]), state=state, alpha_id=alpha_id, metrics=metrics, checks=checks, self_status=self_status, self_value=self_value)
            candidate = RecoveryCandidate(str(row["candidate_id"]), str(row["expression"]), str(row["search_arm"]), str(row["dataset"]), str(row["field_family"]), str(row["parent_candidate_id"]), str(row["parent_history_id"]))
            refresh_result = type("RefreshResult", (), {"metrics": metrics, "checks": checks})()
            record_candidate_outcome(feedback, self._proposal(candidate), str(row["request_hash"]), outcome="PASS" if state == "QUALIFIED" else state, result=refresh_result, quality_reasons=reasons, provenance="PLATFORM_VERIFIED")

    @staticmethod
    def _proposal(candidate: RecoveryCandidate) -> FactoryCandidateProposal:
        identity = expression_identity(candidate.expression)
        return FactoryCandidateProposal(candidate_id=candidate.candidate_id, expression=candidate.expression, topic_id="", hypothesis_id="", research_family=candidate.search_arm, strategy_family=candidate.search_arm, mechanism=str((candidate.lineage or {}).get("mutation") or candidate.search_arm), dataset=candidate.dataset, parent_template=candidate.parent_history_id, exact_hash=identity.exact_hash, parameter_skeleton=identity.parameter_skeleton, field_skeleton=identity.field_skeleton, field_family=candidate.field_family, generator_source=candidate.search_arm, parent_candidate_id=candidate.parent_candidate_id)

    @staticmethod
    def _external_blocker(category: str, message: str) -> dict[str, Any] | None:
        text = f"{category} {message}".lower()
        if "429" in text or "rate-limit" in text or "circuitopen" in text:
            return {"kind": "PLATFORM_LIMIT", "detail": str(message)[:300]}
        if (
            "authentication" in text
            or "http 401" in text
            or "http 403" in text
            or "stored browser session" in text
            or "auth-state" in text
        ):
            return {"kind": "AUTH", "detail": str(message)[:300]}
        return None

    def _allocations(self, run_id: str) -> dict[str, int]:
        candidates = self.store.candidate_rows(run_id)
        by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in candidates:
            if row["state"] in {"FAR_FAIL", "NEAR_PASS", "WAITING_CHECKS", "QUALIFIED"}:
                by_arm[str(row["search_arm"])].append(row)
        if not any(by_arm.values()):
            return {arm: WARMUP_PER_ARM for arm in ARMS}
        total = sum(len(values) for values in by_arm.values())
        windows = self.store.arm_windows(run_id)
        disabled = {arm for arm in ARMS if arm != "broad_exploration" and len([item for item in windows if item["arm"] == arm and not item["improved"]]) >= 2 and all(not item["improved"] for item in [entry for entry in windows if entry["arm"] == arm][-2:])}
        allocation = {arm: 0 for arm in ARMS}
        allocation["broad_exploration"] = 2
        for _ in range(BATCH_SIZE - 2):
            scores: list[tuple[float, str]] = []
            for arm in ARMS:
                if arm in disabled:
                    continue
                rows = by_arm.get(arm, [])
                if not rows:
                    score = float("inf")
                else:
                    rewards = [self._reward(row) for row in rows]
                    score = sum(rewards) / len(rewards) + math.sqrt(2.0 * math.log(total + 1) / len(rows))
                scores.append((score, arm))
            if not scores:
                allocation["broad_exploration"] += 1
            else:
                allocation[max(scores, key=lambda item: (item[0], item[1]))[1]] += 1
        return allocation

    @staticmethod
    def _reward(row: Mapping[str, Any]) -> float:
        return _reward_value(row)

    def _record_windows(self, run_id: str, batch_number: int, allocations: Mapping[str, int], attempted: Mapping[str, list[dict[str, Any]]]) -> None:
        prior = self.store.arm_windows(run_id)
        for arm, allocation in allocations.items():
            rows = attempted.get(arm, [])
            observations = [item for item in rows if item.get("state") not in {"SIMULATION_ERROR", "EXTERNAL_ERROR"}]
            sharpes = [float(item.get("metrics", {}).get("sharpe")) for item in observations if item.get("metrics", {}).get("sharpe") is not None]
            fitnesses = [float(item.get("metrics", {}).get("fitness")) for item in observations if item.get("metrics", {}).get("fitness") is not None]
            stats = {"simulations": len(rows), "platform_errors": len(rows) - len(observations), "validity_rate": len(observations) / max(1, len(rows)), "positive_sharpe_rate": sum(1 for value in sharpes if value > 0) / max(1, len(observations)), "median_sharpe": _median(sharpes), "top_sharpe": max(sharpes) if sharpes else None, "median_fitness": _median(fitnesses), "top_fitness": max(fitnesses) if fitnesses else None, "near_pass_count": sum(1 for item in observations if item.get("state") == "NEAR_PASS"), "performance_viable_count": sum(1 for item in observations if item.get("state") in {"WAITING_CHECKS", "QUALIFIED"}), "final_success_count": sum(1 for item in observations if item.get("state") == "QUALIFIED")}
            prior_arm = [item for item in prior if item["arm"] == arm]
            previous_top = max((float(item["statistics"].get("top_sharpe") or float("-inf")) for item in prior_arm), default=float("-inf"))
            improved = bool(stats["near_pass_count"] or stats["performance_viable_count"] or stats["final_success_count"] or (stats["top_sharpe"] is not None and stats["top_sharpe"] > previous_top))
            self.store.write_arm_window(run_id, batch_number, arm, allocation, stats, improved)
        self.store.update_run(run_id, "RUNNING", simulations=self._real_simulation_count(run_id))

    def _real_simulation_count(self, run_id: str) -> int:
        completed_states = {"FAR_FAIL", "NEAR_PASS", "WAITING_CHECKS", "QUALIFIED"}
        return sum(
            1 for row in self.store.candidate_rows(run_id)
            if row["state"] in completed_states
        )

    def _stop_for_blocker(self, run_id: str, blocker: Mapping[str, Any]) -> dict[str, Any]:
        qualified = len(self.store.qualified(run_id))
        if str(blocker.get("kind")) == "AUTH":
            state = "AUTH_PAUSED"
        elif qualified:
            state = "PARTIAL_ALPHA_FOUND"
        else:
            state = "PLATFORM_LIMIT_REACHED_WITHOUT_SUCCESS"
        self.store.update_run(run_id, state, blocker=blocker)
        return self.status(run_id)

    def status(self, run_id: str | None = None) -> dict[str, Any]:
        with sqlite3.connect(self.database) as con:
            if run_id:
                run = con.execute("SELECT run_id,status,total_real_simulations,blocker_json FROM recovery_runs WHERE run_id=?", (run_id,)).fetchone()
            else:
                run = con.execute("SELECT run_id,status,total_real_simulations,blocker_json FROM recovery_runs ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not run:
            return {"STATUS": "NOT_STARTED", "QUALIFIED_ALPHAS": [], "SEARCH_STATISTICS": {}}
        run_id = str(run[0])
        blocker_payload = _loads(run[3], {})
        effective_status = str(run[1])
        # Migrate the pre-dual-chain label at read time; the validation pause
        # must never be presented as a permanent factory failure.
        if effective_status == "EXTERNAL_AUTH_BLOCKED" and blocker_payload.get("kind") == "AUTH":
            effective_status = "AUTH_PAUSED"
        qualified = self.store.qualified(run_id)
        def survivor(row: Mapping[str, Any]) -> tuple[float, float, float]:
            corr = row.get("self_correlation_value")
            metrics = _metrics(row.get("metrics_json"))
            return (float(corr) if corr is not None else float("inf"), -float(metrics.get("sharpe", -float("inf"))), -float(metrics.get("fitness", -float("inf"))))
        qualified.sort(key=survivor)
        candidates = self.store.candidate_rows(run_id)

        def describe(row: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "alpha_id": row["alpha_id"],
                "expression": row["expression"],
                "state": row["state"],
                "Sharpe": _metrics(row["metrics_json"]).get("sharpe"),
                "Fitness": _metrics(row["metrics_json"]).get("fitness"),
                "Turnover": _metrics(row["metrics_json"]).get("turnover"),
                "Self-correlation": row["self_correlation_value"] if row["self_correlation_value"] is not None else row["self_correlation_status"],
                "dataset/family": f"{row['dataset']}/{row['field_family']}",
                "search_lineage": {"arm": row["search_arm"], "parent_candidate": row["parent_candidate_id"], "parent_history": row["parent_history_id"]},
                "key_checks": _checks(row["checks_json"]),
            }

        viable = [row for row in candidates if row["state"] in {"FAR_FAIL", "NEAR_PASS", "WAITING_CHECKS", "QUALIFIED"}]
        viable.sort(
            key=lambda row: (
                -_reward_value(row),
                -float(_metrics(row["metrics_json"]).get("sharpe", float("-inf"))),
                -float(_metrics(row["metrics_json"]).get("fitness", float("-inf"))),
            )
        )
        errors = [row for row in candidates if row["state"] in {"SIMULATION_ERROR", "EXTERNAL_ERROR"}]
        errors.sort(key=lambda row: str(row["candidate_id"]), reverse=True)
        arms: dict[str, dict[str, Any]] = {}
        for arm in ARMS:
            rows = [row for row in candidates if row["search_arm"] == arm]
            arms[arm] = {"real_simulations": sum(1 for row in rows if row["state"] in {"FAR_FAIL", "NEAR_PASS", "WAITING_CHECKS", "QUALIFIED"}), "pending_simulations": sum(1 for row in rows if row["state"] == "PENDING_SIMULATION"), "qualified": sum(1 for row in rows if row["state"] == "QUALIFIED"), "near_pass": sum(1 for row in rows if row["state"] == "NEAR_PASS"), "waiting_checks": sum(1 for row in rows if row["state"] == "WAITING_CHECKS")}
        return {
            "STATUS": effective_status,
            "RUN_ID": run_id,
            "QUALIFIED_ALPHAS": [describe(row) for row in qualified],
            "CURRENT_BEST": [describe(row) for row in viable[:3]],
            "RECENT_ERRORS": [
                {
                    "expression": row["expression"],
                    "arm": row["search_arm"],
                    "category": row["error_category"],
                    "detail": row["error_message"],
                }
                for row in errors[:3]
            ],
            "SEARCH_STATISTICS": {
                "total_real_simulations": sum(1 for row in candidates if row["state"] in {"FAR_FAIL", "NEAR_PASS", "WAITING_CHECKS", "QUALIFIED"}),
                "pass_count": len(qualified),
                "near_pass_count": sum(1 for row in candidates if row["state"] == "NEAR_PASS"),
                "self_correlation_pass_count": sum(1 for row in candidates if row["self_correlation_status"] == "PASS"),
                "hit_rate_by_arm": arms,
            },
            "REMAINING_LIMITATION": blocker_payload,
        }

    @staticmethod
    def _empirical_regions(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[(str(row.get("dataset") or "UNKNOWN"), str(row.get("field_family") or "UNKNOWN"), str(row.get("operator_topology") or "UNKNOWN"))].append(row)
        output = []
        for (dataset, family, topology), values in groups.items():
            winners = [item for item in values if item.get("evidence_class") in {"PERFORMANCE_PASS", "FULL_PASS"}]
            recent_failures = [
                item for item in values
                if item.get("source_name") == "fresh_pilot" and item.get("evidence_class") == "FAIL"
            ]
            winner_metrics = [_metrics(item.get("metrics_json")) for item in winners]
            failure_metrics = [_metrics(item.get("metrics_json")) for item in recent_failures]
            feature_rows = [_loads(item.get("features_json"), {}) for item in values]
            output.append(
                {
                    "dataset": dataset,
                    "field_family": family,
                    "operator_topology": topology,
                    "records": len(values),
                    "performance_passes": len(winners),
                    "performance_pass_rate": len(winners) / len(values),
                    "recent_24_failures": len(recent_failures),
                    "winner_median_sharpe": _median([item["sharpe"] for item in winner_metrics if "sharpe" in item]),
                    "winner_median_fitness": _median([item["fitness"] for item in winner_metrics if "fitness" in item]),
                    "recent_fail_median_sharpe": _median([item["sharpe"] for item in failure_metrics if "sharpe" in item]),
                    "recent_fail_median_fitness": _median([item["fitness"] for item in failure_metrics if "fitness" in item]),
                    "windows": dict(Counter(str(window) for feature in feature_rows for window in feature.get("windows", []))),
                    "grouping": dict(Counter(str(group) for feature in feature_rows for group in feature.get("grouping", []))),
                    "normalizers": dict(Counter(str(value) for feature in feature_rows for value in feature.get("normalizers", []))),
                }
            )
        return sorted(output, key=lambda item: (-item["performance_pass_rate"], -item["records"], item["dataset"]))[:200]


def _reward_value(row: Mapping[str, Any]) -> float:
    state = str(row.get("state") or "")
    if state == "QUALIFIED":
        return 4.0
    if state == "WAITING_CHECKS":
        return 3.0
    if state == "NEAR_PASS":
        return 2.0
    metrics = _metrics(row.get("metrics_json"))
    return 1.0 if float(metrics.get("sharpe", 0.0)) > 0 else 0.0


def _lineage_improved(parent: Mapping[str, Any], child: Mapping[str, Any], parent_reward: float) -> bool:
    if _reward_value(child) > parent_reward:
        return True
    parent_metrics = _metrics(parent.get("metrics_json"))
    child_metrics = _metrics(child.get("metrics_json"))
    if child_metrics.get("sharpe", float("-inf")) > parent_metrics.get("sharpe", float("-inf")):
        return True
    if child_metrics.get("fitness", float("-inf")) > parent_metrics.get("fitness", float("-inf")):
        return True
    parent_corr = parent.get("self_correlation_value")
    child_corr = child.get("self_correlation_value")
    return parent_corr is not None and child_corr is not None and float(child_corr) < float(parent_corr)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
