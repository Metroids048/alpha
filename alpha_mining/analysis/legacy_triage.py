"""Legacy alpha triage: filter and cluster 20k historical alphas for reuse."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── data loading ──────────────────────────────────────────────────────────────

_OPERATOR_TOKENS: frozenset[str] = frozenset(
    {
        "add",
        "sub",
        "multiply",
        "divide",
        "rank",
        "zscore",
        "market",
        "group_neutralize",
        "group_rank",
        "winsorize",
        "ts_rank",
        "ts_zscore",
        "ts_delta",
        "ts_mean",
        "ts_std",
        "ts_sum",
        "ts_min",
        "ts_max",
        "log",
        "sign",
        "abs",
        "min",
        "max",
        "pow",
        "exp",
        "cap",
        "floor",
        "normalize",
        "regression_neut",
        "truncate",
        "indneutralize",
        "pasteurize",
        "subindustry",
        "industry",
        "sector",
        "country",
    }
)

_TOKEN_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_]*\b")


def _data_fields(expression: str) -> frozenset[str]:
    """Return the set of non-operator identifiers used in an expression."""
    tokens = _TOKEN_RE.findall(str(expression or ""))
    return frozenset(t.lower() for t in tokens if t.lower() not in _OPERATOR_TOKENS)


def _to_float(val: Any) -> float | None:
    try:
        return (
            float(val)
            if val is not None and str(val).strip() not in ("", "nan", "None")
            else None
        )
    except (TypeError, ValueError):
        return None


def _load_from_csv(path: str | Path) -> list[dict]:
    """Load alpha records from alpha_submission_feedback.csv."""
    rows: list[dict] = []
    with open(str(path), encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def _load_from_sqlite(path: str | Path) -> list[dict]:
    """Load alpha records from research_memory.sqlite simulation_runs table."""
    rows: list[dict] = []
    con = sqlite3.connect(str(path))
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT alpha_id, expression, status, queue_status, sharpe, fitness, "
            "turnover, fail_reason FROM simulation_runs"
        )
        for row in cur.fetchall():
            rows.append(dict(row))
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return rows


def _normalise_record(raw: dict) -> dict:
    """Standardise field names across CSV and SQLite sources."""
    return {
        "alpha_id": str(raw.get("alpha_id") or ""),
        "expression": str(raw.get("expression") or ""),
        "family": str(raw.get("family") or ""),
        "sharpe": _to_float(raw.get("Sharpe") or raw.get("sharpe")),
        "fitness": _to_float(raw.get("Fitness") or raw.get("fitness")),
        "turnover": _to_float(raw.get("Turnover") or raw.get("turnover")),
        "failure_reasons": str(
            raw.get("Failure Reasons") or raw.get("fail_reason") or ""
        ),
        "region": str(raw.get("Region") or raw.get("region") or ""),
        "universe": str(raw.get("Universe") or raw.get("universe") or ""),
        "neutralization": str(
            raw.get("Neutralization") or raw.get("neutralization") or ""
        ),
    }


def load_records(source: str | Path) -> list[dict]:
    """Load and normalise alpha records from a CSV or SQLite source.

    Tries CSV first (richer fields), falls back to SQLite.
    """
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Source not found: {path}")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        raw = _load_from_csv(path)
    elif suffix in (".sqlite", ".sqlite3", ".db"):
        raw = _load_from_sqlite(path)
    else:
        # Try CSV first, then SQLite
        try:
            raw = _load_from_csv(path)
        except Exception:
            raw = _load_from_sqlite(path)
    return [
        _normalise_record(r) for r in raw if r.get("expression") or r.get("alpha_id")
    ]


# ── filtering ─────────────────────────────────────────────────────────────────


def filter_worth_resubmitting(
    source: str | Path | list[dict],
    *,
    min_sharpe: float | None = None,
    min_fitness: float | None = None,
    min_turnover: float | None = None,
    max_turnover: float | None = None,
) -> list[dict]:
    """Compatibility filter requiring caller-supplied dynamic gate limits.

    Composite filter: Sharpe > min_sharpe AND Fitness > min_fitness AND
    Turnover in [min_turnover, max_turnover].  All three must pass — this is
    an AND condition, not OR.  Alphas with missing Fitness or Sharpe are skipped.
    Turnover-missing alphas pass the turnover check (missing ≠ out-of-range).

    Accepts a file path (CSV/SQLite) or an already-loaded list of dicts.
    Raw CSV-style dicts (capitalised "Sharpe" etc.) are normalised automatically.
    """
    if None in (min_sharpe, min_fitness, min_turnover, max_turnover):
        raise ValueError("explicit Dynamic Gate Registry limits are required")
    if isinstance(source, list):
        records = [_normalise_record(r) for r in source]
    else:
        records = load_records(source)
    out: list[dict] = []
    for rec in records:
        sh = rec.get("sharpe")
        fi = rec.get("fitness")
        to = rec.get("turnover")
        if sh is None or fi is None:
            continue
        if sh <= min_sharpe:
            continue
        if fi <= min_fitness:
            continue
        if to is not None and (to < min_turnover or to > max_turnover):
            continue
        out.append(rec)
    return out


# ── clustering ────────────────────────────────────────────────────────────────


@dataclass
class AlphaCluster:
    cluster_id: str
    family: str
    data_fields: frozenset[str]
    members: list[dict] = field(default_factory=list)

    @property
    def avg_sharpe(self) -> float | None:
        vals = [r["sharpe"] for r in self.members if r.get("sharpe") is not None]
        return sum(vals) / len(vals) if vals else None

    @property
    def avg_turnover(self) -> float | None:
        vals = [r["turnover"] for r in self.members if r.get("turnover") is not None]
        return sum(vals) / len(vals) if vals else None


def cluster_by_structure(
    records: list[dict],
    *,
    max_clusters: int = 200,
    representatives_per_cluster: int = 5,
) -> list[AlphaCluster]:
    """Group alpha records by (family, data_fields) structural similarity.

    Records with the same family and the same set of non-operator data fields
    are placed in the same cluster.  Representatives are selected as the top N
    by Sharpe within each cluster.

    Returns up to max_clusters clusters sorted by member count descending.
    """
    buckets: dict[tuple[str, frozenset[str]], AlphaCluster] = {}
    for rec in records:
        if not rec.get("expression"):
            continue
        fam = str(rec.get("family") or "unknown")
        fields = _data_fields(rec["expression"])
        key = (fam, fields)
        if key not in buckets:
            cluster_id = f"{fam}|{'_'.join(sorted(fields)[:6])}"
            buckets[key] = AlphaCluster(
                cluster_id=cluster_id,
                family=fam,
                data_fields=fields,
            )
        buckets[key].members.append(rec)

    clusters = sorted(buckets.values(), key=lambda c: len(c.members), reverse=True)
    clusters = clusters[:max_clusters]

    # Keep only top representatives per cluster to reduce output noise.
    for cl in clusters:
        cl.members.sort(key=lambda r: r.get("sharpe") or -999, reverse=True)
        cl.members = cl.members[:representatives_per_cluster]

    return clusters


# ── report generation ─────────────────────────────────────────────────────────

_RESIM_QUEUE_FIELDS = [
    "alpha_id",
    "expression",
    "settings_json",
    "region",
    "universe",
    "delay",
    "neutralization",
    "decay",
    "truncation",
    "sharpe",
    "fitness",
    "turnover",
    "returns",
    "drawdown",
    "margin",
    "date_created",
    "new_alpha_id",
    "resim_status",
]

_DEFAULT_SETTINGS_TEMPLATE = {
    "instrumentType": "EQUITY",
    "pasteurization": "ON",
    "unitHandling": "VERIFY",
    "nanHandling": "ON",
    "maxTrade": "OFF",
    "maxPosition": "OFF",
    "language": "FASTEXPR",
    "visualization": False,
}


def _reconstruct_settings_json(rec: dict) -> str:
    """Build a settings_json string compatible with 提交通过门槛的alpha.py.

    Tries in order:
    1. Parse platform_simulation_json if present (most reliable).
    2. Reconstruct from individual Region/Universe/Neutralization/Decay/Truncation/Delay fields.
    """
    raw = str(rec.get("platform_simulation_json") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("region"):
                return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pass

    settings = dict(_DEFAULT_SETTINGS_TEMPLATE)
    settings["region"] = str(rec.get("region") or "USA").strip() or "USA"
    settings["universe"] = str(rec.get("universe") or "TOP3000").strip() or "TOP3000"
    settings["neutralization"] = (
        str(rec.get("neutralization") or "MARKET").strip() or "MARKET"
    )
    try:
        settings["delay"] = int(float(rec.get("delay") or 1))
    except (TypeError, ValueError):
        settings["delay"] = 1
    try:
        settings["decay"] = int(float(rec.get("decay") or 4))
    except (TypeError, ValueError):
        settings["decay"] = 4
    try:
        settings["truncation"] = float(rec.get("truncation") or 0.05)
    except (TypeError, ValueError):
        settings["truncation"] = 0.05
    return json.dumps(settings, ensure_ascii=False, separators=(",", ":"))


def export_to_resim_queue(
    records: list[dict],
    output_path: str | Path,
    *,
    skip_existing_ids: set[str] | None = None,
    overwrite: bool = False,
) -> int:
    """Write qualified records to alpha_resim_queue.csv for 提交通过门槛的alpha.py.

    The output CSV is directly consumable by 提交通过门槛的alpha.py — all rows
    start with resim_status=PENDING so they will be submitted on the next run.
    Records whose alpha_id already appears in the queue (DONE or PENDING) are
    skipped unless overwrite=True.

    Returns: number of rows written.
    """

    path = Path(output_path)
    existing_ids: set[str] = set(skip_existing_ids or [])
    existing_rows: list[dict] = []

    if path.is_file() and not overwrite:
        with open(str(path), encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                existing_rows.append(row)
                if row.get("alpha_id"):
                    existing_ids.add(str(row["alpha_id"]).strip())

    new_rows: list[dict] = []
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for rec in records:
        aid = str(rec.get("alpha_id") or "").strip()
        if not aid or aid in existing_ids:
            continue
        expr = str(rec.get("expression") or "").strip()
        if not expr:
            continue

        new_rows.append(
            {
                "alpha_id": aid,
                "expression": expr,
                "settings_json": _reconstruct_settings_json(rec),
                "region": str(rec.get("region") or "USA"),
                "universe": str(rec.get("universe") or "TOP3000"),
                "delay": rec.get("delay") or 1,
                "neutralization": str(rec.get("neutralization") or "MARKET"),
                "decay": rec.get("decay") or 4,
                "truncation": rec.get("truncation") or 0.05,
                "sharpe": rec.get("sharpe") or "",
                "fitness": rec.get("fitness") or "",
                "turnover": rec.get("turnover") or "",
                "returns": rec.get("returns") or "",
                "drawdown": rec.get("drawdown") or "",
                "margin": rec.get("margin") or "",
                "date_created": now,
                "new_alpha_id": "",
                "resim_status": "PENDING",
            }
        )

    all_rows = existing_rows + new_rows
    with open(str(path), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=_RESIM_QUEUE_FIELDS, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(all_rows)

    return len(new_rows)


def run_triage(
    source: str | Path,
    output_dir: str | Path = ".",
    *,
    min_sharpe: float | None = None,
    min_fitness: float | None = None,
    min_turnover: float | None = None,
    max_turnover: float | None = None,
    max_clusters: int = 200,
    representatives_per_cluster: int = 5,
    resim_queue_path: str | Path | None = None,
) -> dict[str, Any]:
    """Deprecated entry point requiring explicit Dynamic Gate Registry limits.

    Writes:
        legacy_triage_resubmit.csv   — candidates worth resubmitting
        legacy_triage_clusters.csv   — structural clusters for composite-signal research
        alpha_resim_queue.csv        — if resim_queue_path is provided, appends PENDING rows
                                       consumable by 提交通过门槛的alpha.py

    Returns a summary dict with counts.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    records = load_records(source)
    total = len(records)

    resubmit = filter_worth_resubmitting(
        records,
        min_sharpe=min_sharpe,
        min_fitness=min_fitness,
        min_turnover=min_turnover,
        max_turnover=max_turnover,
    )

    clusters = cluster_by_structure(
        records,
        max_clusters=max_clusters,
        representatives_per_cluster=representatives_per_cluster,
    )

    # Write resubmit CSV
    resubmit_path = output_path / "legacy_triage_resubmit.csv"
    _write_csv(
        resubmit_path,
        resubmit,
        [
            "alpha_id",
            "expression",
            "family",
            "sharpe",
            "fitness",
            "turnover",
            "region",
            "universe",
            "neutralization",
            "failure_reasons",
        ],
    )

    # Write clusters CSV
    clusters_path = output_path / "legacy_triage_clusters.csv"
    cluster_rows: list[dict] = []
    for cl in clusters:
        for rec in cl.members:
            cluster_rows.append(
                {
                    "cluster_id": cl.cluster_id,
                    "family": cl.family,
                    "data_fields": " | ".join(sorted(cl.data_fields)),
                    "cluster_size": 0,
                    "avg_sharpe": cl.avg_sharpe,
                    "avg_turnover": cl.avg_turnover,
                    "expression": rec.get("expression", ""),
                    "sharpe": rec.get("sharpe"),
                    "turnover": rec.get("turnover"),
                    "failure_reasons": rec.get("failure_reasons", ""),
                }
            )
    size_map: dict[str, int] = {}
    for cl in cluster_by_structure(
        records, max_clusters=max_clusters, representatives_per_cluster=999999
    ):
        size_map[cl.cluster_id] = len(cl.members)
    for row in cluster_rows:
        row["cluster_size"] = size_map.get(str(row["cluster_id"]), 0)

    _write_csv(
        clusters_path,
        cluster_rows,
        [
            "cluster_id",
            "family",
            "data_fields",
            "cluster_size",
            "avg_sharpe",
            "avg_turnover",
            "expression",
            "sharpe",
            "turnover",
            "failure_reasons",
        ],
    )

    # Legacy automatic queue writing is disabled. vNext triage selects one
    # deterministic medoid per cluster and owns all simulation budgeting.
    queue_written = 0
    queue_path_used: str | None = None
    if resim_queue_path is not None:
        queue_path_used = str(resim_queue_path)
        print(
            "[triage] legacy resimulation queue write BLOCKED; use python -m alpha_mining legacy triage"
        )

    summary = {
        "total_records": total,
        "resubmit_candidates": len(resubmit),
        "clusters": len(clusters),
        "resubmit_path": str(resubmit_path),
        "clusters_path": str(clusters_path),
        "queue_written": queue_written,
        "queue_path": queue_path_used,
    }
    print(
        f"[triage] total={total} resubmit={len(resubmit)} clusters={len(clusters)} "
        f"resubmit→{resubmit_path.name} clusters→{clusters_path.name}"
    )
    return summary


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(str(path), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
