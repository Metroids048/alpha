"""TEMPORARY one-shot validation driver.  Delete after fresh-alpha validation.

Wires existing production classes together with the correct project root.  It
implements no HTTP, no auth, no catalog parsing and no settings enumeration of
its own, and it never writes platform_access_state / pipeline_loop_state.

STEP 2: one authenticate + identity probe through the existing access controller.
STEP 3: full catalog sync (only if identity really succeeded).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

import os

from alpha_mining.platform.access import PlatformAccessController
from alpha_mining.platform.catalog import PlatformCatalogSynchronizer
from alpha_mining.platform.client import ReadOnlyPlatformClient

# Business/validation state is isolated under VAL_ROOT, but the platform access
# circuit is NOT: platform_access_state / platform_request_events are an
# account-level protection.  Pointing them at a fresh VAL_ROOT database would
# create a second circuit breaker seeded CLOSED with last_429=NULL
# (storage/migrations.py:225), silently discarding tonight's real 429 history.
VAL_ROOT = _ROOT / ".validation_workspace"
DB = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
LOCK = _ROOT / "worldquant_api.lock"
STATE = _ROOT / ".wq_auth_state.json"


def _state_line(tag: str, controller: PlatformAccessController) -> None:
    s = controller.status()
    print(
        f"  [{tag}] state={s.state} retry_after={s.retry_after_until or 'none'} "
        f"attempts={s.recovery_attempts}/{s.max_auto_recoveries}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true", help="run catalog sync after identity passes")
    args = parser.parse_args()

    if not os.environ.get("WQ_USERNAME") or not os.environ.get("WQ_PASSWORD"):
        print("BLOCKED: WQ_USERNAME / WQ_PASSWORD absent from project-root .env")
        return 2

    controller = PlatformAccessController(str(DB), str(LOCK))
    print("STEP 2 - authenticate + single identity recovery probe")
    _state_line("before", controller)

    client = ReadOnlyPlatformClient(
        state_path=str(STATE),
        database=str(DB),
        lock_path=str(LOCK),
        # 3.0 is empirically 429-free: a prior run sustained 792 consecutive
        # catalog GETs at this spacing.  1.0 was rate-limited at request #120,
        # so the speedup has to come from page_size (fewer requests), never
        # from a tighter interval (higher request rate).
        min_interval=3.0,
        timeout=60.0,
    )

    try:
        client.authenticate()
    except Exception as exc:  # noqa: BLE001 - classification is the whole point
        print(f"  authenticate FAILED: {type(exc).__name__}: {exc}")
        _state_line("after", controller)
        return 3

    # RATE_LIMITED with an expired interval permits exactly one explicit GET
    # recovery probe (alpha_mining/platform/access.py:213).  Let the existing
    # state machine decide; never pre-empt it by rewriting the row.
    state = controller.status()
    recovery_probe = state.state == "RATE_LIMITED"
    print(f"  recovery_probe={recovery_probe} (driven by current state, not forced)")

    try:
        identity = client.fetch_identity(recovery_probe=recovery_probe)
    except Exception as exc:  # noqa: BLE001
        print(f"  identity FAILED: {type(exc).__name__}: {exc}")
        _state_line("after", controller)
        return 4

    who = identity.get("id") or identity.get("username") or identity.get("email") or "<no id field>"
    print(f"  identity OK: {who}")
    print(f"  identity keys: {sorted(identity)[:12]}")
    _state_line("after", controller)

    if not args.sync:
        print("\n--sync not passed: stopping after identity probe")
        return 0

    print("\nSTEP 3 - catalog sync (region=USA universe=TOP3000 delay=1)")
    print(f"  CODE_ROOT                  {_ROOT}")
    print(f"  CACHE_ROOT                 {VAL_ROOT}")
    print(f"  PLATFORM_ACCESS_DATABASE   {DB}")
    print(f"  AUTH_STATE_PATH            {STATE}")
    print("  page_size                  50")
    print("  min_interval               3.0")
    print("  resume                     True (INFRA-REL-001 opt-in)")
    print(f"  CHECKPOINT_DIR             {VAL_ROOT / '.alpha_catalog_sync_checkpoint'}")
    # page_size: 50 is the production default and the platform's hard ceiling.
    #
    # resume=True is the validation-harness opt-in for INFRA-REL-001.  A full
    # sync costs ~1900 requests over ~100min; measured run#5 died on a 429 at
    # request 1513 and, before checkpointing, discarded all of it.  Completed
    # datasets now survive in VAL_ROOT/.alpha_catalog_sync_checkpoint/fields/,
    # so a second 429 is an EXPECTED RECOVERABLE FAILURE: stop, wait out the
    # cooldown recorded in platform_access_state, resume.  Production callers
    # keep resume=False and are unaffected.
    #
    # Cache lands in VAL_ROOT, never the repo root: this run is isolated fresh
    # validation, not production state promotion.  The repo-root caches stay on
    # their old snapshot until a separate PROMOTE_VALIDATED_RUNTIME_STATE step.
    syncer = PlatformCatalogSynchronizer(VAL_ROOT, page_size=50, resume=True)
    counts = None
    for attempt in range(1, 4):
        try:
            counts = syncer.sync(client, region="USA", universe="TOP3000", delay=1)
            break
        except Exception as exc:  # noqa: BLE001
            transient = "ProxyError" in type(exc).__name__ or "proxy" in str(exc).lower()
            print(f"  SYNC ATTEMPT {attempt}/3 FAILED: {type(exc).__name__}: {str(exc)[:200]}")
            _state_line(f"after-attempt-{attempt}", controller)
            if not transient or attempt == 3:
                return 5
            print("  -> transient local proxy failure; retrying")
    if counts is None:
        return 5
    print(f"  counts: {json.dumps(counts, sort_keys=True)}")
    _state_line("after-sync", controller)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
