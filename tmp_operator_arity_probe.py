"""TEMPORARY operator arity probe.  UNTRACKED / NOT_FOR_COMMIT.

catalog.py:390 raised "platform operator metadata has no verifiable arity",
which means _normalise_operator_record() returned None for at least one live
record.  Runs #2-#6 all died in data-fields, so this stage had never executed
against the real platform before.

Costs exactly ONE request.  Read-only: writes nothing, touches no checkpoint
and no cache.  Prints operator names and signatures only -- these are public
platform metadata, no headers/cookies/JWT.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

from alpha_mining.platform.access import PlatformAccessController
from alpha_mining.platform.catalog import _arity_from_signature, _normalise_operator_record
from alpha_mining.platform.client import ReadOnlyPlatformClient

DB = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
LOCK = _ROOT / "worldquant_api.lock"
STATE = _ROOT / ".wq_auth_state.json"
CONTEXT = {"instrumentType": "EQUITY", "region": "USA", "universe": "TOP3000", "delay": 1}


def main() -> int:
    print("=== provenance ===")
    print(f"  EFFECTIVE_ROOT       = {_ROOT}")
    print(f"  EFFECTIVE_DATABASE   = {DB}")
    controller = PlatformAccessController(str(DB), str(LOCK))
    status = controller.status()
    print(f"  circuit before       = {status.state} attempts={status.recovery_attempts}/{status.max_auto_recoveries}")
    if status.state != "CLOSED":
        print("BLOCKED: circuit is not CLOSED; refusing to send a request")
        return 3
    print()

    client = ReadOnlyPlatformClient(
        state_path=str(STATE), database=str(DB), lock_path=str(LOCK),
        min_interval=3.0, timeout=60.0,
    )
    client.authenticate()
    rows = client.list_operators(dict(CONTEXT))

    print(f"operators returned      = {len(rows)}  (type={type(rows).__name__})")
    keys: collections.Counter[str] = collections.Counter()
    for row in rows:
        keys.update(row.keys())
    print("keys present across records:")
    for key, n in keys.most_common():
        print(f"  {key:22s} {n}/{len(rows)}")
    print()

    failures = []
    for row in rows:
        if _normalise_operator_record(row) is None:
            failures.append(row)

    print(f"=== records that fail normalisation: {len(failures)}/{len(rows)} ===")
    for row in failures:
        name = str(row.get("name") or row.get("id") or "").strip()
        signature = str(row.get("signature") or row.get("definition") or "").strip()
        raw_arity = row.get("arity", "<absent>")
        derived = _arity_from_signature(signature)
        reason = []
        if not name:
            reason.append("empty name")
        if raw_arity == "<absent>":
            reason.append("no arity field")
        if derived is None:
            if not signature:
                reason.append("empty signature")
            else:
                for token in ("...", "*", "[", "]"):
                    if token in signature:
                        reason.append(f"signature contains {token!r}")
                if "(" not in signature or ")" not in signature:
                    reason.append("signature is not name(args)")
                if not any(r.startswith("signature contains") for r in reason):
                    reason.append("regex did not match")
        print(f"  {name:24s} arity={raw_arity!r:10s} derived={derived!r}")
        print(f"      signature = {signature[:160]!r}")
        print(f"      reason    = {', '.join(reason) or 'unknown'}")

    print()
    print("=== token histogram over ALL signatures ===")
    tokens: collections.Counter[str] = collections.Counter()
    for row in rows:
        signature = str(row.get("signature") or row.get("definition") or "")
        for token in ("...", "*", "[", "]", "=", "<", ">"):
            if token in signature:
                tokens[token] += 1
    for token, n in tokens.most_common():
        print(f"  {token!r:8s} appears in {n}/{len(rows)} signatures")

    print()
    print(f"  circuit after        = {controller.status().state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
