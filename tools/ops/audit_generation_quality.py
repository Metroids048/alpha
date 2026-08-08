"""Read-only quality matrix for a fresh local generation batch."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from alpha_mining.domain.expression_normalization import (
    behavior_signature,
    extract_fields,
    structure_signature,
)
from alpha_mining.generation.validation import LocalExpressionValidator
from alpha_mining.platform.simulation_contract import SimulationSettingsContract


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _sample_size_passes(size: int, minimum: int) -> bool:
    return size >= max(1, int(minimum))


def _claim_contradiction(row: dict[str, str], fields: set[str], functions: set[str]) -> bool:
    text = " ".join(
        row.get(key, "")
        for key in ("research_direction", "economic_hypothesis", "economic_rationale", "expected_turnover_behavior")
    ).casefold()

    def has_field(*names: str) -> bool:
        return any(name in fields or any(part == name for part in field.split("_")) for name in names for field in fields)

    has_price = has_field("close", "open", "high", "low", "vwap", "price", "returns")
    has_volume = has_field("volume", "adv20", "adv")
    has_cap = has_field("cap", "marketcap")
    if ("price-volume" in text or "price volume" in text or "volume confirmation" in text) and not (has_price and has_volume):
        return True
    if "price momentum" in text and not has_price:
        return True
    if any(token in text for token in ("returns momentum", "returns signal", "returns leg", "returns component")) and not has_field("returns"):
        return True
    if any(token in text for token in ("adv20", "volume liquidity", "volume momentum", "volume signal", "volume leg", "volume component")) and not has_volume:
        return True
    if ("market-cap scaling" in text or "market cap scaling" in text or "cap-scaled" in text) and not has_cap:
        return True
    if any(token in text for token in ("group neutraliz", "sector neutraliz", "industry neutraliz")) and not any(item.startswith("group_") for item in functions):
        return True
    if "ts_decay_linear" in text and "ts_decay_linear" not in functions:
        return True
    has_revision = any(
        "revision" in field or re.search(r"(?:^|_)rev(?:_|$)", field)
        for field in fields
    )
    if "revision leg" in text and not has_revision:
        return True
    return False


def audit(
    root: Path,
    queue_path: Path,
    schema_path: Path,
    *,
    created_after: str,
    min_sample_size: int = 20,
) -> dict[str, Any]:
    from alpha_mining.generation.snapshots import load_local_snapshots

    # The operator queue is written with a UTF-8 BOM, so the first column arrives as
    # "﻿candidate_id" under plain utf-8.  utf-8-sig strips it and keeps candidate
    # ids resolvable, which is what makes every gate failure traceable to a candidate.
    with queue_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            row for row in csv.DictReader(handle)
            if row.get("queue_status") == "PENDING_SIMULATION" and str(row.get("created_at") or "") >= created_after
        ]
    snapshots = load_local_snapshots(root=root, queue_path=queue_path, allow_partial_offline=True)
    contract = SimulationSettingsContract.load(schema_path)
    validator = LocalExpressionValidator(snapshots.catalog, allow_stale_catalog=True)
    legality_failures: list[str] = []
    contradictions: list[str] = []
    exact = Counter()
    normalized = Counter()
    structures = Counter()
    behaviors = Counter()
    datasets = Counter()
    fields = Counter()
    scores: list[float] = []

    for row in rows:
        candidate_id = row.get("candidate_id", "")
        expression = row.get("expression", "")
        used_fields = tuple(extract_fields(expression))
        used_functions = set()
        try:
            from alpha_mining.domain.expression_normalization import extract_functions

            used_functions = set(extract_functions(expression))
            setting_values = {key: row.get(key) for key in ("alpha_type", "region", "universe", "delay", "decay", "neutralization", "truncation", "language")}
            setting_values["delay"] = int(str(setting_values["delay"]))
            setting_values["decay"] = int(str(setting_values["decay"]))
            setting_values["truncation"] = float(str(setting_values["truncation"]))
            contract.prepare(setting_values)
            expected_dataset = snapshots.catalog.fields[used_fields[0]].dataset_id if used_fields else None
            if validator.validate(expression, expected_dataset_id=expected_dataset):
                raise ValueError("expression validation failed")
            if len({snapshots.catalog.fields[field].dataset_id for field in used_fields}) != 1:
                raise ValueError("cross dataset")
            if row.get("degraded", "").lower() != "false":
                raise ValueError("degraded")
        except (KeyError, ValueError) as exc:
            legality_failures.append(f"{candidate_id}:{exc}")
        if _claim_contradiction(row, set(used_fields), used_functions):
            contradictions.append(candidate_id)
        exact[row.get("exact_hash", expression)] += 1
        normalized[row.get("normalized_hash", "")] += 1
        structures[structure_signature(expression)] += 1
        behaviors[behavior_signature(expression)] += 1
        for dataset in _json_list(row.get("datasets", "")):
            datasets[dataset] += 1
        for field in used_fields:
            fields[field] += 1
        try:
            scores.append(float(row.get("local_quality_score") or 0))
        except ValueError:
            legality_failures.append(f"{candidate_id}:invalid local quality score")

    duplicate_counts = {
        "exact": sum(count - 1 for count in exact.values() if count > 1),
        "normalized": sum(count - 1 for count in normalized.values() if count > 1),
        "structure": sum(count - 1 for count in structures.values() if count > 1),
        "behavior": sum(count - 1 for count in behaviors.values() if count > 1),
    }
    size = len(rows)
    sample_pass = _sample_size_passes(size, min_sample_size)
    dominant_dataset = max(datasets.values(), default=0) / size if size else 0.0
    dominant_field = max(fields.values(), default=0) / size if size else 0.0
    coverage_required = size >= 20 and len(snapshots.catalog.datasets) >= 3
    score_distribution = {
        "min": min(scores) if scores else None,
        "median": statistics.median(scores) if scores else None,
        "mean": statistics.mean(scores) if scores else None,
        "max": max(scores) if scores else None,
        "p25": statistics.quantiles(scores, n=4, method="inclusive")[0] if len(scores) >= 2 else None,
        "p75": statistics.quantiles(scores, n=4, method="inclusive")[2] if len(scores) >= 2 else None,
    }
    result = {
        "created_after": created_after,
        "sample_size": size,
        "gate_sample": {"pass": sample_pass, "minimum": max(1, int(min_sample_size))},
        "gate_a": {"pass": not legality_failures, "failures": legality_failures},
        "gate_b": {"pass": not any(duplicate_counts.values()), "duplicates": duplicate_counts},
        "gate_c": {"pass": not contradictions, "contradictions": contradictions},
        "gate_d": {
            "pass": not coverage_required or (dominant_dataset <= 0.5 and dominant_field <= 0.4 and len(datasets) >= 3),
            "required": coverage_required,
            "dataset_distribution": dict(sorted(datasets.items())),
            "field_frequency": dict(sorted(fields.items())),
        },
        "gate_e": {
            "score_distribution": score_distribution
        },
    }
    result["acceptance_pass"] = all(
        result[key]["pass"] for key in ("gate_sample", "gate_a", "gate_b", "gate_c", "gate_d")
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--settings-schema", type=Path, required=True)
    parser.add_argument("--created-after", required=True, help="inclusive UTC ISO boundary for this fresh batch")
    parser.add_argument("--min-sample-size", type=int, default=20)
    args = parser.parse_args()
    result = audit(
        args.root,
        args.queue,
        args.settings_schema,
        created_after=args.created_after,
        min_sample_size=args.min_sample_size,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["acceptance_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
