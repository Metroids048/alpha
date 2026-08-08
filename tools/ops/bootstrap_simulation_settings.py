"""Produce ``.alpha_simulation_settings_cache.json`` from verifiable evidence.

P-001 shipped a reader (``SimulationSettingsContract``) with no producer, so every
generation cycle stopped at ``SIMULATION_SETTINGS_UNAVAILABLE``.  This tool is that
missing producer.  It never invents an enum: every allowed value carries a
provenance record naming the evidence it came from.

Evidence sources, in descending authority:

1. ``PLATFORM_CATALOG`` - the synchronized catalog cache context written by
   ``PlatformCatalogSynchronizer.sync()``.  Authoritative for region/universe/delay
   because the catalog itself was fetched under exactly those coordinates.
2. ``PLATFORM_ACCEPTED_ALPHA`` - settings echoed back by the platform on alphas it
   actually created (``legacy_alphas.settings_json``).  Authoritative for
   neutralization/decay/truncation.
3. ``PLATFORM_REQUIRED_FIELD`` - request-only fields the platform rejects a
   simulation without (HTTP 400 "This field is required."), recorded in
   ``alpha_mining/simulate/settings_optimizer.py`` and corroborated by the
   complete simulation requests in ``simulation_requests``.

Run::

    python tools/ops/bootstrap_simulation_settings.py            # write cache
    python tools/ops/bootstrap_simulation_settings.py --dry-run  # inspect only
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SCHEMA_VERSION = "simulation-settings-v1"
CACHE_NAME = ".alpha_simulation_settings_cache.json"

# Request-only fields.  These never appear in the settings the platform echoes back
# on a created alpha, but the simulation POST is rejected without them.  Values are
# the ones carried by every complete simulation request on record.
_REQUEST_ONLY_DEFAULTS: dict[str, Any] = {
    "language": "FASTEXPR",
    "pasteurization": "ON",
    "unitHandling": "VERIFY",
    "nanHandling": "ON",
    "visualization": False,
    "alpha_type": "REGULAR",
}
_REQUEST_ONLY_ALLOWED: dict[str, tuple[Any, ...]] = {
    "language": ("FASTEXPR",),
    "pasteurization": ("ON", "OFF"),
    "unitHandling": ("VERIFY",),
    "nanHandling": ("ON", "OFF"),
    "visualization": (False, True),
    "alpha_type": ("REGULAR",),
}

# Observed on platform-created alphas but excluded from the enum: float artifacts
# such as 0.0800002 are storage noise, not distinct operator choices.  Truncation is
# rounded to 3 decimal places before aggregation.
_TRUNCATION_PRECISION = 3


class EvidenceError(RuntimeError):
    """Raised when the local evidence cannot justify a required allowed-value set."""


def _read_catalog_context(catalog_dir: Path) -> dict[str, Any]:
    """Return region/universe/delay from the synchronized catalog cache."""
    for name in (".alpha_datasets_cache.json", ".alpha_datafields_cache.json",
                 ".alpha_operators_cache.json"):
        path = catalog_dir / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        region = str(payload.get("region") or "").strip()
        universe = str(payload.get("universe") or "").strip()
        delay = payload.get("delay")
        if region and universe and delay is not None:
            return {
                "region": region,
                "universe": universe,
                "delay": int(delay),
                "cached_at": payload.get("cached_at"),
                "evidence_file": name,
            }
    raise EvidenceError(
        "no synchronized catalog cache carries region/universe/delay; run the catalog "
        "sync first (tools/ops/refresh_catalog_and_reset.py)"
    )


def _accepted_settings_rows(database: Path) -> list[dict[str, Any]]:
    """Return settings dicts the platform echoed back on alphas it created."""
    if not database.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    rows: list[dict[str, Any]] = []
    try:
        table = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='legacy_alphas'"
        ).fetchone()
        if not table:
            return []
        for (text,) in con.execute(
            "SELECT settings_json FROM legacy_alphas "
            "WHERE settings_json IS NOT NULL AND settings_json LIKE '%neutralization%'"
        ):
            try:
                parsed = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict) and parsed:
                rows.append(parsed)
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return rows


def _tally(rows: list[dict[str, Any]], key: str) -> Counter:
    counter: Counter = Counter()
    for row in rows:
        if key not in row:
            continue
        value = row[key]
        if value is None or value == "":
            continue
        counter[value] += 1
    return counter


def _string_enum(rows: list[dict[str, Any]], key: str, *, minimum: int = 1) -> tuple[Any, ...]:
    counter = _tally(rows, key)
    values = [str(value).strip().upper() for value in counter if str(value).strip()]
    unique = tuple(dict.fromkeys(values))
    if len(unique) < minimum:
        raise EvidenceError(f"no platform-accepted values observed for {key}")
    return unique


def _int_enum(rows: list[dict[str, Any]], key: str) -> tuple[int, ...]:
    seen: set[int] = set()
    for row in rows:
        try:
            seen.add(int(float(row[key])))
        except (KeyError, TypeError, ValueError):
            continue
    if not seen:
        raise EvidenceError(f"no platform-accepted values observed for {key}")
    return tuple(sorted(seen))


def _float_enum(rows: list[dict[str, Any]], key: str) -> tuple[float, ...]:
    seen: set[float] = set()
    for row in rows:
        try:
            seen.add(round(float(row[key]), _TRUNCATION_PRECISION))
        except (KeyError, TypeError, ValueError):
            continue
    if not seen:
        raise EvidenceError(f"no platform-accepted values observed for {key}")
    return tuple(sorted(seen))


def _most_common(rows: list[dict[str, Any]], key: str, cast) -> Any:
    counter = _tally(rows, key)
    if not counter:
        raise EvidenceError(f"no platform-accepted values observed for {key}")
    ordered = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    return cast(ordered[0][0])


def build_payload(
    *,
    catalog_dir: Path,
    database: Path,
    now: float | None = None,
) -> dict[str, Any]:
    context = _read_catalog_context(catalog_dir)
    rows = _accepted_settings_rows(database)
    if not rows:
        raise EvidenceError(
            f"no platform-accepted alpha settings found in {database}; the settings enum "
            "cannot be derived from local evidence alone"
        )
    scoped = [
        row
        for row in rows
        if str(row.get("region") or "").strip().upper() == context["region"].upper()
    ]
    evidence_rows = scoped or rows

    neutralization = _string_enum(evidence_rows, "neutralization")
    decay = _int_enum(evidence_rows, "decay")
    truncation = _float_enum(evidence_rows, "truncation")

    defaults: dict[str, Any] = {
        "region": context["region"],
        "universe": context["universe"],
        "delay": int(context["delay"]),
        "neutralization": _most_common(evidence_rows, "neutralization", lambda v: str(v).upper()),
        "decay": _most_common(evidence_rows, "decay", lambda v: int(float(v))),
        "truncation": _most_common(
            evidence_rows, "truncation", lambda v: round(float(v), _TRUNCATION_PRECISION)
        ),
        **_REQUEST_ONLY_DEFAULTS,
    }
    allowed: dict[str, Any] = {
        "region": [context["region"]],
        "universe": [context["universe"]],
        "delay": [int(context["delay"])],
        "neutralization": list(neutralization),
        "decay": list(decay),
        "truncation": list(truncation),
        **{key: list(values) for key, values in _REQUEST_ONLY_ALLOWED.items()},
    }
    # A default must itself be an allowed value, or the contract is self-inconsistent.
    for key, value in defaults.items():
        if value not in allowed[key]:
            allowed[key] = sorted(
                {*allowed[key], value}, key=lambda item: (str(type(item)), str(item))
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "fetched_at": time.time() if now is None else float(now),
        "context": {
            "region": context["region"],
            "universe": context["universe"],
            "delay": int(context["delay"]),
        },
        "defaults": defaults,
        "allowed_values": allowed,
        "provenance": {
            "region": f"PLATFORM_CATALOG:{context['evidence_file']}",
            "universe": f"PLATFORM_CATALOG:{context['evidence_file']}",
            "delay": f"PLATFORM_CATALOG:{context['evidence_file']}",
            "neutralization": f"PLATFORM_ACCEPTED_ALPHA:{database.name}:legacy_alphas",
            "decay": f"PLATFORM_ACCEPTED_ALPHA:{database.name}:legacy_alphas",
            "truncation": (
                f"PLATFORM_ACCEPTED_ALPHA:{database.name}:legacy_alphas"
                f" (rounded to {_TRUNCATION_PRECISION}dp)"
            ),
            **{key: "PLATFORM_REQUIRED_FIELD:simulation_400_required" for key in _REQUEST_ONLY_ALLOWED},
            "evidence_rows": len(evidence_rows),
            "evidence_rows_region_scoped": bool(scoped),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-dir", default=str(_ROOT))
    parser.add_argument(
        "--database",
        default=str(_ROOT / "research_memory_quality.sqlite"),
        help="SQLite database holding platform-accepted alpha settings",
    )
    parser.add_argument("--out", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    catalog_dir = Path(args.catalog_dir)
    out = Path(args.out) if args.out else catalog_dir / CACHE_NAME
    try:
        payload = build_payload(catalog_dir=catalog_dir, database=Path(args.database))
    except EvidenceError as exc:
        print(f"BLOCKED: {exc}")
        return 2

    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"schema_version : {payload['schema_version']}")
    print(f"context        : {payload['context']}")
    print(f"evidence rows  : {payload['provenance']['evidence_rows']}")
    for key in sorted(payload["allowed_values"]):
        values = payload["allowed_values"][key]
        shown = values if len(values) <= 12 else [*values[:12], f"... (+{len(values) - 12})"]
        print(f"  {key:<16} default={payload['defaults'][key]!r:<12} allowed={shown}")
    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    temporary = out.with_name(out.name + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(out)
    print(f"\nwrote {out}")

    from alpha_mining.platform.simulation_contract import SimulationSettingsContract

    contract = SimulationSettingsContract.load(out)
    prepared = contract.prepare({})
    print(f"contract loads  : OK ({len(contract.allowed_values)} keys)")
    print(f"prepare({{}})     : {json.dumps(prepared, ensure_ascii=False, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
