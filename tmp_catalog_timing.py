"""Read-only timing probe: locate the pre-generation cost. Changes no production code."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".validation_workspace" / ".alpha_datafields_cache.json"


def stamp(label: str, t0: float) -> float:
    dt = time.perf_counter() - t0
    print(f"{label:46s} {dt:8.2f}s", flush=True)
    return time.perf_counter()


def pick(row: dict, *names: str, default=None):
    for name in names:
        if name in row:
            return row[name]
    return default


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"cache={CACHE.name} size_mb={CACHE.stat().st_size / 1048576:.1f}", flush=True)

    t = time.perf_counter()
    raw = CACHE.read_text(encoding="utf-8")
    t = stamp("1. read_text (49.6MB)", t)

    payload = json.loads(raw)
    t = stamp("2. json.loads", t)

    rows_src = payload.get("rows") or []
    print(f"   rows={len(rows_src)}", flush=True)
    if rows_src:
        print("   first row keys:", ",".join(sorted(rows_src[0].keys()))[:280], flush=True)

    rows = [
        {
            "id": pick(r, "id", "field_id"),
            "_ds": pick(r, "_ds", "dataset_id", "dataset"),
            "coverage": pick(r, "coverage", default=float("nan")),
            "dateCoverage": pick(r, "dateCoverage", "date_coverage", default=float("nan")),
            "userCount": pick(r, "userCount", "user_count", default=float("nan")),
            "description": pick(r, "description", default=""),
            "type": pick(r, "type", "field_type", default="UNKNOWN"),
        }
        for r in rows_src
    ]
    t = stamp("3. build row dicts (v50_kernel shape)", t)

    import pandas as pd

    t = stamp("4. import pandas", t)

    df = pd.DataFrame(rows)
    t = stamp("5. pd.DataFrame(rows)", t)

    spec = importlib.util.spec_from_file_location(
        "auto_alpha_pipeline_rebuilt_v50", ROOT / "auto_alpha_pipeline_rebuilt_v50.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    t = stamp("6. import v50 module", t)

    catalog = module.FieldCatalog.from_df(df)
    t = stamp("7. FieldCatalog.from_df (df.iterrows)", t)
    print(f"   catalog.ids={len(catalog.ids)} by_ds={len(catalog.by_ds)}", flush=True)
    if len(catalog.ids) < 80000:
        print("   !! WARNING: row shaping lost ids, timing 7 is NOT representative", flush=True)

    validator = module.PreflightValidator(catalog)
    t = stamp("8. PreflightValidator.__init__ (cache build)", t)
    print(f"   allowed_identifiers={len(validator._static_allowed_identifiers)}", flush=True)

    expr = f"group_neutralize(ts_zscore({next(iter(catalog.ids)).lower()},63),market)"
    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        validator._unknown_identifiers(expr)
    dt = time.perf_counter() - t0
    print(f"9. _unknown_identifiers x{n}: {dt:.2f}s ({dt / n * 1e6:.1f} us/call)", flush=True)

    m = 200
    t0 = time.perf_counter()
    for _ in range(m):
        validator.validate(expr)
    dt2 = time.perf_counter() - t0
    print(f"10. full validate() x{m}: {dt2:.2f}s ({dt2 / m * 1e3:.2f} ms/call)", flush=True)
    for label, count in (("5k candidates", 5000), ("20k candidates", 20000)):
        print(f"    projected validate() for {label}: {dt2 / m * count:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
