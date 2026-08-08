"""Compare circuit state in root vs canonical DB, then probe the canonical one."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alpha_mining.common import load_workspace_env

load_workspace_env()

ROOT_DB = Path("research_memory.sqlite")
CANON = Path("数据/本地运行产物/数据库/research_memory.sqlite")

for label, db in (("ROOT   ", ROOT_DB), ("CANON  ", CANON)):
    if not db.exists():
        print(f"{label} {db} MISSING")
        continue
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        cols = [c[1] for c in con.execute("PRAGMA table_info(platform_access_state)")]
        row = con.execute("SELECT * FROM platform_access_state").fetchone()
        d = dict(zip(cols, row)) if row else {}
    print(f"{label} state={d.get('state')} retry_after={d.get('retry_after_until')} "
          f"attempts={d.get('recovery_attempts')}/{d.get('max_auto_recoveries')}")

print(f"\nnow(UTC) = {datetime.now(timezone.utc).isoformat()}")
print("=> ops/refresh_catalog_and_reset.py hardcodes the ROOT db, so it would")
print("   report the ROOT circuit and never touch the one the pipeline uses.\n")

from alpha_mining.platform.access import PlatformAccessController, _parse_time  # noqa: E402

controller = PlatformAccessController(CANON, "worldquant_api.lock")
state = controller.status()
until = _parse_time(state.retry_after_until)
now = datetime.now(timezone.utc)
expired = until is not None and now >= until
print(f"canonical circuit: state={state.state} expired={expired} "
      f"attempts={state.recovery_attempts}/{state.max_auto_recoveries}")

if state.state != "RATE_LIMITED" or not expired:
    print("no probe issued (either already usable or window still active)")
    raise SystemExit(0)

from alpha_mining.platform.client import ReadOnlyPlatformClient  # noqa: E402

client = ReadOnlyPlatformClient(
    state_path=".wq_auth_state.json", database=CANON,
    lock_path="worldquant_api.lock", min_interval=3.0, timeout=60.0,
)
print("\nissuing ONE explicit recovery probe (GET identity)...")
try:
    identity = client.fetch_identity(recovery_probe=True)
    print(f"  probe OK: {identity.get('username') or identity.get('email') or 'ok'}")
except Exception as exc:
    print(f"  probe FAILED: {type(exc).__name__}: {exc}")

state = controller.status()
print(f"\ncircuit after probe: state={state.state} retry_after={state.retry_after_until} "
      f"attempts={state.recovery_attempts}/{state.max_auto_recoveries}")
