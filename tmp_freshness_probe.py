"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT.

Classifies each VAL_ROOT queue row as PRE_FIX or FRESH relative to the frozen
fix commit, comparing created_at against the committer timestamp.  Read-only:
opens the queue CSV for reading and shells out to `git show -s` only.
"""

from __future__ import annotations

import csv
import subprocess
from datetime import datetime, timezone
from pathlib import Path

QUEUE = Path(".validation_workspace/待提交Alpha列表.csv")
# Round 2 draft gate, Round 2 plan gate, Round 3 near-variant gate.  The last is
# the frozen SHA under validation, so it is the freshness boundary.
FIXES = ("127640b", "c07c166", "939ef53")


def parse_iso(text: object) -> datetime | None:
    if not text:
        return None
    raw = str(text).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def commit_time(sha: str) -> datetime | None:
    out = subprocess.run(
        ["git", "show", "-s", "--format=%cI", sha],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    return parse_iso(out.stdout.strip())


def main() -> int:
    times = {sha: commit_time(sha) for sha in FIXES}
    print("=== fix commit times (UTC) ===")
    for sha, moment in times.items():
        print(f"  {sha} = {moment.isoformat() if moment else 'UNKNOWN'}")
    frozen = times[FIXES[-1]]
    print(f"\nFROZEN_BOUNDARY = {frozen.isoformat() if frozen else 'UNKNOWN'}")

    rows = list(csv.DictReader(QUEUE.open(encoding="utf-8-sig")))
    print(f"\n=== {len(rows)} queue rows ===")
    fresh: list[str] = []
    pre: list[str] = []
    for index, row in enumerate(rows, start=1):
        cid = str(row.get("candidate_id") or "")
        created = parse_iso(row.get("created_at"))
        is_fresh = bool(frozen and created and created >= frozen)
        (fresh if is_fresh else pre).append(cid)
        mark = "FRESH" if is_fresh else "PRE  "
        stamp = created.isoformat() if created else "?"
        print(
            f"{index:2d}. {mark} {cid[:16]}  created={stamp}  "
            f"ds={str(row.get('datasets') or '?')[:32]:32s} "
            f"src={row.get('generator_source') or '?'}"
        )
    print(f"\nFRESH_POST_939ef53 = {len(fresh)}")
    print(f"PRE_FIX            = {len(pre)}")
    print(f"NEED_MORE_FOR_20   = {max(0, 20 - len(fresh))}")
    print(f"TARGET_FOR_20_FRESH = pending_total {len(pre) + 20}")
    print(f"TARGET_FOR_40_FRESH = pending_total {len(pre) + 40}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
