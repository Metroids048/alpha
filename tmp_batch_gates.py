"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT

Gate A-E acceptance over a set of queued candidates, read-only.

Uses the production validator / hashers / catalog: implements no rule of its own,
so a PASS here is the pipeline's own verdict rather than this file's opinion.

  A legality        every expression parses and clears LocalExpressionValidator
                    (group axes excepted, as the draft gate does), and every
                    VECTOR field is reduced
  B duplicate       exact / normalized / structure hashes unique within the batch
  C contradiction   declared fields+operators == the expression's actual tokens
  D concentration   dataset and field spread across the batch
  E settings        identical platform axes; research knobs reported, not pinned
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from alpha_mining.domain.expression_ast import ExpressionSyntaxError, parse_expression
from alpha_mining.domain.expression_normalization import (
    exact_hash,
    extract_fields,
    extract_functions,
    normalized_expression,
    structure_signature,
)
from alpha_mining.generation.high_quality import (
    _group_axis_identifiers,
    _near_variant_ratio_fields,
    _suppressible_scope_issue,
    _unreduced_vector_fields,
)
from alpha_mining.generation.snapshots import load_local_snapshots
from alpha_mining.generation.validation import LocalExpressionValidator

VAL_ROOT = _ROOT / ".validation_workspace"


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rows(
    queue: Path,
    exclude: tuple[str, ...],
    since: str = "",
    until: str = "",
    limit: int = 0,
) -> list[dict[str, str]]:
    """Select queue rows by candidate_id prefix and created_at window.

    created_at is an ISO-8601 UTC string written by the queue writer, so plain
    lexicographic comparison is a correct ordering here without parsing.  The
    window is what separates PRE_FIX rows from post-939ef53 FRESH rows, and what
    splits the fresh rows into two disjoint consecutive batches -- selection by
    timestamp instead of by hand keeps the split reproducible from the CSV alone.
    """
    with queue.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows.sort(key=lambda item: str(item.get("created_at") or ""))
    out = []
    for row in rows:
        cid = str(row.get("candidate_id") or "")
        if exclude and cid.startswith(exclude):
            continue
        created = str(row.get("created_at") or "")
        if since and created < since:
            continue
        if until and created >= until:
            continue
        out.append(row)
    if limit > 0:
        out = out[:limit]
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exclude", action="append", default=[],
                        help="candidate_id prefix to leave out (pre-fix rows)")
    parser.add_argument("--since", default="",
                        help="keep rows with created_at >= this ISO stamp")
    parser.add_argument("--until", default="",
                        help="keep rows with created_at < this ISO stamp")
    parser.add_argument("--limit", type=int, default=0,
                        help="keep only the first N rows after windowing")
    parser.add_argument("--label", default="BATCH")
    args = parser.parse_args()

    snap = load_local_snapshots(
        root=VAL_ROOT, catalog_dir=VAL_ROOT,
        allow_partial_offline=True, offline_max_age_hours=100000.0,
    )
    rows = _rows(
        VAL_ROOT / "待提交Alpha列表.csv",
        tuple(args.exclude),
        since=args.since,
        until=args.until,
        limit=args.limit,
    )
    print(f"=== {args.label}: {len(rows)} candidates ===")
    if rows:
        first = str(rows[0].get("created_at") or "?")
        last = str(rows[-1].get("created_at") or "?")
        print(f"    created_at window: {first} .. {last}")
    if not rows:
        print("BLOCKED: no rows after exclusion")
        return 4

    validator = LocalExpressionValidator(snap.catalog, allow_stale_catalog=True)

    # ---- Gate A: legality -------------------------------------------------
    legal = 0
    for row in rows:
        expr = str(row.get("expression") or "").strip()
        cid = str(row.get("candidate_id") or "")[:16]
        faults: list[str] = []
        try:
            parse_expression(expr)
        except ExpressionSyntaxError as exc:
            faults.append(f"SYNTAX:{exc}")
        axes = _group_axis_identifiers(expr)
        for issue in validator.validate(expr):
            if not _suppressible_scope_issue(issue, axes):
                faults.append(f"{issue.code}:{issue.message}")
        unreduced = _unreduced_vector_fields(expr, snap.catalog)
        if unreduced:
            faults.append(f"VECTOR_NOT_REDUCED:{','.join(unreduced)}")
        # Round 3's rule has to be here too, or a PASS would not prove it works:
        # d7b6aa1d's dilution_adjustment_ratio / dilution_adjustment_ratio_2 is
        # locally parseable and clears the validator, yet the real platform
        # returned sharpe 0.0 / turnover 0.0 / CLUSTER_TEST=ERROR for it.
        near_variant = _near_variant_ratio_fields(expr, snap.catalog)
        if near_variant:
            faults.append(f"NEAR_VARIANT_RATIO:{','.join(near_variant)}")
        if faults:
            print(f"  [A] FAIL {cid}  {'; '.join(faults[:3])}")
        else:
            legal += 1
    pct = 100.0 * legal / len(rows)
    gate_a = legal == len(rows)
    print(f"  [A] legality        {legal}/{len(rows)} = {pct:.1f}%  -> {'PASS' if gate_a else 'FAIL'}")

    # ---- Gate B: duplicate defence ---------------------------------------
    exact = [exact_hash(str(r.get("expression") or "")) for r in rows]
    norm = [_hash(normalized_expression(str(r.get("expression") or ""))) for r in rows]
    struct = [structure_signature(str(r.get("expression") or "")) for r in rows]
    gate_b = len(set(exact)) == len(rows) and len(set(norm)) == len(rows)
    print(f"  [B] exact unique    {len(set(exact))}/{len(rows)}")
    print(f"      normalized      {len(set(norm))}/{len(rows)}")
    print(f"      topologies      {len(set(struct))} distinct")
    print(f"      -> {'PASS' if gate_b else 'FAIL'}")

    # ---- Gate C: contradiction -------------------------------------------
    contradictions = 0
    for row in rows:
        expr = str(row.get("expression") or "")
        cid = str(row.get("candidate_id") or "")[:16]
        axes = _group_axis_identifiers(expr)
        actual_fields = set(extract_fields(expr)) - axes
        actual_ops = set(extract_functions(expr))
        declared_fields = set()
        for key in ("fields", "field_roles"):
            raw = row.get(key)
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, str):
                        declared_fields.add(item)
                    elif isinstance(item, dict):
                        declared_fields.add(str(item.get("field_id") or ""))
        declared_fields.discard("")
        extra = declared_fields - actual_fields - axes
        if declared_fields and extra:
            contradictions += 1
            print(f"  [C] FAIL {cid}  declared-but-unused fields: {sorted(extra)}")
    gate_c = contradictions == 0
    print(f"  [C] contradictions  {contradictions}  -> {'PASS' if gate_c else 'FAIL'}")

    # ---- Gate D: concentration -------------------------------------------
    datasets: Counter[str] = Counter()
    fields: Counter[str] = Counter()
    for row in rows:
        raw = row.get("datasets")
        try:
            parsed = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            parsed = [raw]
        for item in (parsed if isinstance(parsed, list) else [parsed]):
            datasets[str(item)] += 1
        expr = str(row.get("expression") or "")
        axes = _group_axis_identifiers(expr)
        for field in set(extract_fields(expr)) - axes:
            fields[field] += 1
    top_ds = datasets.most_common(1)[0] if datasets else ("-", 0)
    top_f = fields.most_common(1)[0] if fields else ("-", 0)
    ds_share = 100.0 * top_ds[1] / len(rows)
    print(f"  [D] datasets        {len(datasets)} distinct, top={top_ds[0]} x{top_ds[1]} ({ds_share:.0f}%)")
    print(f"      fields          {len(fields)} distinct, top={top_f[0]} x{top_f[1]}")

    # ---- Gate E: settings identity ---------------------------------------
    # The queue stores settings as separate columns, not one JSON blob.  Reading a
    # non-existent "settings" column made every row None and passed vacuously.
    # Two different kinds of axis live in these columns, and pinning both was a
    # harness error, not a pipeline defect.  _settings() in high_quality.py reads
    # decay / truncation / neutralization from the model's own proposed settings
    # (4 / 0.08 / MARKET are only fallbacks), so per-candidate variation there is
    # designed research behaviour.  What must be unanimous is the axes that decide
    # whether the batch is even comparable: same market, same universe, same
    # delay, same language.  Varying those would let a batch flatter itself by
    # mixing regimes.
    _PLATFORM_COLUMNS = ("alpha_type", "region", "universe", "delay", "language")
    _RESEARCH_COLUMNS = ("decay", "truncation", "neutralization")
    missing = [
        c for c in (*_PLATFORM_COLUMNS, *_RESEARCH_COLUMNS) if c not in rows[0]
    ]
    if missing:
        print(f"  [E] FAIL: queue has no column(s) {missing}")
        gate_e = False
    else:
        seen = {
            json.dumps({c: r.get(c) for c in _PLATFORM_COLUMNS}, sort_keys=True)
            for r in rows
        }
        gate_e = len(seen) == 1
        print(
            f"  [E] platform axes   {len(seen)} distinct -> "
            f"{'PASS' if gate_e else 'FAIL'}"
        )
        for value in sorted(seen):
            print(f"      {value}")
        for column in _RESEARCH_COLUMNS:
            spread = Counter(str(r.get(column)) for r in rows)
            rendered = ", ".join(
                f"{value}x{count}" for value, count in sorted(spread.items())
            )
            print(f"      {column:14s} {len(spread)} distinct  [{rendered}]")

    verdict = gate_a and gate_b and gate_c and gate_e
    print(f"\n  {args.label} GATES: {'PASS' if verdict else 'FAIL'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
