"""A/B: old per-candidate catalog rebuild vs cached allow-list. Read-only."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".validation_workspace" / ".alpha_datafields_cache.json"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import pandas as pd

    payload = json.loads(CACHE.read_text(encoding="utf-8"))
    rows = [
        {
            "id": r.get("id"),
            "_ds": r.get("_ds"),
            "userCount": r.get("userCount"),
            "type": r.get("type"),
            "description": r.get("description", ""),
        }
        for r in payload.get("rows") or []
    ]
    spec = importlib.util.spec_from_file_location(
        "v50mod", ROOT / "auto_alpha_pipeline_rebuilt_v50.py"
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules["v50mod"] = m
    spec.loader.exec_module(m)

    catalog = m.FieldCatalog.from_df(pd.DataFrame(rows))
    print(f"catalog.ids={len(catalog.ids)} base_vars={len(catalog.base_vars)}", flush=True)
    validator = m.PreflightValidator(catalog)
    low = f"group_neutralize(ts_zscore({next(iter(catalog.ids)).lower()},63),market)"

    def old_unknown(text: str) -> list[str]:
        """Verbatim pre-fix implementation (rebuilds the whole allow-list)."""
        allowed = {x.lower() for x in catalog.ids} | {x.lower() for x in catalog.base_vars}
        allowed |= m.FUNCTIONS | m.GROUPS | validator._assigned_names(text)
        out = []
        for ident in sorted(set(re.findall(r"\b[a-z_][a-z0-9_]*\b", text))):
            if ident in allowed:
                continue
            if ident in {"true", "false", "nan", "inf", "rettype", "range"}:
                continue
            out.append(ident)
        return out

    assert old_unknown(low) == validator._unknown_identifiers(low), "semantics diverged"
    print("semantics: OLD == NEW ok", flush=True)

    n_old = 30
    t0 = time.perf_counter()
    for _ in range(n_old):
        old_unknown(low)
    old = (time.perf_counter() - t0) / n_old

    n_new = 2000
    t0 = time.perf_counter()
    for _ in range(n_new):
        validator._unknown_identifiers(low)
    new = (time.perf_counter() - t0) / n_new

    print(f"OLD {old * 1e3:9.3f} ms/call")
    print(f"NEW {new * 1e3:9.5f} ms/call    speedup {old / new:,.0f}x")
    for label, k in (("5,000", 5000), ("20,000", 20000)):
        print(f"  {label:>6} candidates: OLD {old * k / 60:7.1f} min   NEW {new * k:6.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
