"""READ-ONLY platform close-out: re-pull checks for the two alphas that have one.

GET only. No simulation POST, no submission. Verifies the feedback-ingestion
path end to end and records the result with evidence-derived provenance.
"""
import json
import sqlite3
from pathlib import Path

from alpha_mining.common import load_workspace_env

load_workspace_env()
CANON = Path("数据/本地运行产物/数据库/research_memory.sqlite")
ALPHAS = {
    "N1bqeYEo": "rank(anl10_ebify2_smart_ests_v0_2247)",
    "ZYEqQ5J0": "ts_std_dev(anl10_ebifq1_pred_surps_v2_2230,126)",
}

from alpha_mining.platform.gateway import PlatformGateway  # noqa: E402

gateway = PlatformGateway(
    state_path=".wq_auth_state.json", database=CANON,
    lock_path="worldquant_api.lock", min_interval=3.0,
)

out = {}
for alpha_id, expression in ALPHAS.items():
    print(f"\n=== {alpha_id}  {expression}")
    try:
        cur = gateway.refresh_alpha_checks(alpha_id)
    except Exception as exc:
        print(f"  FETCH FAILED: {type(exc).__name__}: {exc}")
        out[alpha_id] = {"error": f"{type(exc).__name__}: {exc}"}
        continue
    metrics, checks = cur.get("metrics") or {}, cur.get("checks") or []
    print(f"  metrics : {json.dumps(metrics, sort_keys=True)}")
    buckets: dict[str, list[str]] = {}
    for item in checks:
        if isinstance(item, dict):
            v = str(item.get("result") or item.get("status") or "?").upper()
            buckets.setdefault(v, []).append(str(item.get("name") or "?"))
    for v in sorted(buckets):
        print(f"    {v:9} ({len(buckets[v])}) {', '.join(sorted(buckets[v]))}")
    out[alpha_id] = {"metrics": metrics, "checks": checks}

Path("_platform_close_result.json").write_text(
    json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

print("\n=== does the mandatory Sharpe gate pass for either? ===")
for alpha_id, data in out.items():
    if "error" in data:
        print(f"  {alpha_id}: unavailable ({data['error'][:60]})")
        continue
    fails = [
        f"{c.get('name')}={c.get('value')}(limit {c.get('limit')})"
        for c in data["checks"]
        if isinstance(c, dict) and str(c.get("result")).upper() in {"FAIL", "FAILED"}
    ]
    print(f"  {alpha_id}: sharpe={data['metrics'].get('sharpe')} "
          f"fitness={data['metrics'].get('fitness')}")
    print(f"    FAILED checks ({len(fails)}): {'; '.join(fails) or 'none'}")

print("\n=== circuit state after the calls ===")
with sqlite3.connect(f"file:{CANON}?mode=ro", uri=True) as con:
    cols = [c[1] for c in con.execute("PRAGMA table_info(platform_access_state)")]
    for row in con.execute("SELECT * FROM platform_access_state"):
        d = dict(zip(cols, row))
        for k in ("state", "retry_after_until", "last_429", "reason"):
            print(f"  {k:20} {d.get(k)}")
    print(f"\n  candidate_outcomes rows = "
          f"{con.execute('SELECT COUNT(*) FROM candidate_outcomes').fetchone()[0]} (was 33)")
