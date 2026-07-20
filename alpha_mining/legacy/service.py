"""Database triage, clustering, and streaming report generation."""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import sqlite3
from typing import Any
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alpha_mining.platform.gates import (
    GateRegistry,
    GateScope,
    MissingGateSnapshot,
    StaleGateSnapshot,
)
from .clustering import deterministic_medoid
from .triage import classify_legacy


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class TriageSummary:
    classifications: dict[str, int]
    clusters: int
    medoids: int


def triage_database(
    database: str | Path,
    *,
    near_pass_ratio: float = 0.90,
    gate_freshness_hours: float = 24.0,
) -> TriageSummary:
    counts: Counter[str] = Counter()
    clusters = medoids = 0
    now = _utc_now()
    registry = GateRegistry(database, freshness_hours=gate_freshness_hours)
    with sqlite3.connect(database) as con:
        query = """SELECT l.legacy_id,l.expression,l.metrics_json,l.checks_json,l.parse_valid,l.settings_json,
                   f.behavior_signature,f.structure_signature,f.unit_warnings_json
                   FROM legacy_alphas l JOIN alpha_expression_features f ON f.canonical_id=l.canonical_id
                   WHERE l.is_canonical=1 ORDER BY f.behavior_signature,l.legacy_id"""
        cursor = con.execute(query)
        for signature, group in itertools.groupby(cursor, key=lambda row: str(row[6])):
            members = []
            for row in group:
                metrics = json.loads(row[2] or "{}")
                checks_payload = json.loads(row[3] or "[]")
                checks = (
                    checks_payload.get("checks", [])
                    if isinstance(checks_payload, dict)
                    else checks_payload
                )
                members.append(
                    {
                        "legacy_id": row[0],
                        "expression": row[1],
                        "parse_valid": bool(row[4]),
                        "settings": json.loads(row[5] or "{}"),
                        "behavior_signature": signature,
                        "structure_signature": row[7],
                        "unit_warnings": json.loads(row[8] or "[]"),
                        "checks": checks,
                        **metrics,
                    }
                )
            if not members:
                continue
            medoid = deterministic_medoid(members)
            cluster_id = (
                "cluster_" + hashlib.sha256(signature.encode()).hexdigest()[:20]
            )
            evaluated = []
            for member in members:
                raw_settings = member.get("settings")
                settings: dict[str, Any] = (
                    raw_settings if isinstance(raw_settings, dict) else {}
                )
                scope = GateScope(
                    region=settings.get("region", "*"),
                    universe=settings.get("universe", "*"),
                    delay=settings.get("delay", "*"),
                    alpha_type=settings.get("type")
                    or settings.get("alpha_type")
                    or "REGULAR",
                    theme_id=settings.get("theme_id", "*"),
                    pyramid_id=settings.get("pyramid_id", "*"),
                )
                limits: dict[str, float] = {}
                versions: dict[str, int] = {}
                for gate_name in ("LOW_SHARPE", "LOW_FITNESS"):
                    try:
                        snapshot = registry.require_fresh(scope, gate_name)
                    except (MissingGateSnapshot, StaleGateSnapshot):
                        continue
                    limits[gate_name] = snapshot.limit
                    versions[snapshot.snapshot_key] = snapshot.version
                decision = classify_legacy(
                    member, limits=limits, near_pass_ratio=near_pass_ratio
                )
                # Only a medoid can be the default RECHECK candidate.
                if (
                    decision.classification == "RECHECK"
                    and member["legacy_id"] != medoid["legacy_id"]
                ):
                    decision = type(decision)(
                        "SEED_ONLY", "non_medoid_recheck_suppressed"
                    )
                evaluated.append((member, decision, versions))

            # Registry reads above happen before this connection obtains a write
            # lock, avoiding cross-connection lock inversion on large imports.
            con.execute(
                "INSERT OR REPLACE INTO alpha_behavior_clusters(cluster_id,behavior_signature,medoid_legacy_id,member_count,algorithm,created_at) VALUES (?,?,?,?,?,?)",
                (
                    cluster_id,
                    signature,
                    medoid["legacy_id"],
                    len(members),
                    "exact_medoid" if len(members) <= 500 else "deterministic_clara",
                    now,
                ),
            )
            clusters += 1
            medoids += 1
            for member, decision, versions in evaluated:
                con.execute(
                    "INSERT OR REPLACE INTO alpha_cluster_members(cluster_id,legacy_id,distance) VALUES (?,?,?)",
                    (
                        cluster_id,
                        member["legacy_id"],
                        0.0 if member["legacy_id"] == medoid["legacy_id"] else 1.0,
                    ),
                )
                counts[decision.classification] += 1
                con.execute(
                    "INSERT OR REPLACE INTO legacy_triage_results(legacy_id,classification,reason,gate_snapshot_versions_json,cluster_id,created_at) VALUES (?,?,?,?,?,?)",
                    (
                        member["legacy_id"],
                        decision.classification,
                        decision.reason,
                        json.dumps(versions, sort_keys=True),
                        cluster_id,
                        now,
                    ),
                )
            con.commit()
    return TriageSummary(dict(counts), clusters, medoids)


def report_database(database: str | Path, output_dir: str | Path) -> dict[str, int]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    specs = {
        "legacy_inventory.csv": (
            """SELECT l.legacy_id,l.alpha_id,l.expression,l.family,l.is_canonical,t.classification,t.reason,t.cluster_id FROM legacy_alphas l LEFT JOIN legacy_triage_results t ON t.legacy_id=l.legacy_id ORDER BY l.legacy_id""",
            [
                "legacy_id",
                "alpha_id",
                "expression",
                "family",
                "is_canonical",
                "classification",
                "reason",
                "cluster_id",
            ],
        ),
        "legacy_cluster_summary.csv": (
            "SELECT cluster_id,behavior_signature,medoid_legacy_id,member_count,algorithm FROM alpha_behavior_clusters ORDER BY member_count DESC,cluster_id",
            [
                "cluster_id",
                "behavior_signature",
                "medoid_legacy_id",
                "member_count",
                "algorithm",
            ],
        ),
        "legacy_seed_candidates.csv": (
            """SELECT l.legacy_id,l.alpha_id,l.expression,t.classification,t.reason,t.cluster_id FROM legacy_triage_results t JOIN legacy_alphas l ON l.legacy_id=t.legacy_id WHERE t.classification IN ('RECHECK','SEED_ONLY') ORDER BY t.classification,l.legacy_id""",
            [
                "legacy_id",
                "alpha_id",
                "expression",
                "classification",
                "reason",
                "cluster_id",
            ],
        ),
        "legacy_repair_candidates.csv": (
            """SELECT l.legacy_id,l.alpha_id,l.expression,t.reason,t.cluster_id FROM legacy_triage_results t JOIN legacy_alphas l ON l.legacy_id=t.legacy_id WHERE t.classification='REPAIR' ORDER BY l.legacy_id""",
            ["legacy_id", "alpha_id", "expression", "reason", "cluster_id"],
        ),
        "legacy_archive.csv": (
            """SELECT l.legacy_id,l.alpha_id,l.expression,t.reason,t.cluster_id FROM legacy_triage_results t JOIN legacy_alphas l ON l.legacy_id=t.legacy_id WHERE t.classification='ARCHIVE' ORDER BY l.legacy_id""",
            ["legacy_id", "alpha_id", "expression", "reason", "cluster_id"],
        ),
    }
    with sqlite3.connect(database) as con:
        for filename, (query, headers) in specs.items():
            count = 0
            with (target / filename).open(
                "w", newline="", encoding="utf-8-sig"
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(headers)
                for row in con.execute(query):
                    writer.writerow(row)
                    count += 1
            counts[filename] = count
    return counts
