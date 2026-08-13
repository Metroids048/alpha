"""A/B: _render_vector_fields old vs new on the real 89,768-field catalog."""

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
        {"id": r.get("id"), "_ds": r.get("_ds"), "userCount": r.get("userCount"),
         "type": r.get("type"), "description": r.get("description", "")}
        for r in payload.get("rows") or []
    ]
    spec = importlib.util.spec_from_file_location("v50m", ROOT / "auto_alpha_pipeline_rebuilt_v50.py")
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules["v50m"] = m
    spec.loader.exec_module(m)

    catalog = m.FieldCatalog.from_df(pd.DataFrame(rows))
    factory = m.ExpressionFactory(
        m.PipelineConfig(username="u", password="p"), catalog, m.PreflightValidator(catalog)
    )
    n_vec = sum(1 for t in catalog.field_type.values() if t == "VECTOR")
    print(f"catalog fields={len(catalog.field_type)} VECTOR={n_vec}", flush=True)

    def old_render(expr: str) -> str:
        rendered = m._sig(expr)
        for field_name, field_type in catalog.field_type.items():
            if field_type != "VECTOR":
                continue
            pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(field_name)}(?![A-Za-z0-9_])")

            def reduce_vector(match):
                prefix = rendered[: match.start()]
                if re.search(r"\bvec_[a-z0-9_]+\s*\(\s*$", prefix, flags=re.I):
                    return match.group(0)
                return f"vec_avg({field_name})"

            rendered = pattern.sub(reduce_vector, rendered)
        return m._sig(rendered)

    vec_name = next(n for n, t in catalog.field_type.items() if t == "VECTOR")
    mat_name = next(n for n, t in catalog.field_type.items() if t == "MATRIX")
    samples = [
        f"group_neutralize(ts_zscore({vec_name},63),market)",
        f"group_neutralize(ts_zscore(vec_avg({vec_name}),63),market)",
        f"group_neutralize(ts_zscore({mat_name}/close,126),sector)",
        f"ts_mean({vec_name},63)/ts_mean({mat_name},63)",
    ]
    for s in samples:
        o, nw = old_render(s), factory._render_vector_fields(s)
        assert o == nw, f"DIVERGED\n old={o}\n new={nw}"
    print(f"semantics: OLD == NEW on {len(samples)} real-catalog expressions ok", flush=True)

    expr = samples[0]
    t0 = time.perf_counter(); old_render(expr); old = time.perf_counter() - t0

    for _ in range(50):
        factory._render_vector_fields(expr)
    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        factory._render_vector_fields(expr)
    new = (time.perf_counter() - t0) / n

    print(f"\nOLD {old * 1e3:10.2f} ms/candidate   ({n_vec:,} re.compile each)")
    print(f"NEW {new * 1e3:10.5f} ms/candidate   speedup {old / new:,.0f}x")
    for label, k in (("5,000", 5000), ("20,000", 20000)):
        print(f"  {label:>6} candidates: OLD {old * k / 60:8.1f} min   NEW {new * k:6.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
