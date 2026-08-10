"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT.

Deterministic LOCAL renormalization of VAL_ROOT/.alpha_operators_cache.json.

Sends ZERO WorldQuant requests. The data source does not change: the platform's
own verbatim `signature` string, already stored in the cache, is re-interpreted
by the frozen commit's `_normalise_operator_record()`. `cached_at` is preserved
byte-identically, because the snapshot was not refetched.

Note `_normalise_operator_record` prefers an explicit row["arity"] over
re-deriving from the signature (catalog.py:446-449), so the stored derived
arity is dropped before re-normalising -- otherwise this would be a no-op.

Writes a temp file and atomically replaces the cache ONLY when every hard
acceptance check passes. Any unexpected diff => no write, exit 3.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_mining.platform.catalog import _normalise_operator_record  # noqa: E402

VAL_ROOT = Path(__file__).resolve().parent / ".validation_workspace"
CACHE = VAL_ROOT / ".alpha_operators_cache.json"
TEMP = VAL_ROOT / ".alpha_operators_cache.json.renorm.tmp"

# The one business change this renormalization is authorized to produce.
EXPECTED_ARITY_DIFF = {"max": (3, 2)}

# Operators the generator/validator gate on. Asserted explicitly after the swap.
REQUIRED_ARITY = {
    "add": 2, "multiply": 2, "max": 2, "min": 2,
    "ts_rank": 2, "ts_delta": 2, "ts_mean": 2, "ts_zscore": 2,
    "group_neutralize": 2,
}

PRESERVED_CONTEXT_KEYS = ("source", "region", "universe", "delay", "cached_at")


def fail(code: str, detail: str) -> None:
    print(f"\nSTOP: {code}")
    print(detail)
    if TEMP.exists():
        TEMP.unlink()
    raise SystemExit(3)


def main() -> int:
    print(f"EFFECTIVE_ROOT={VAL_ROOT}")
    print(f"CACHE_FILE={CACHE}")
    print("NETWORK_REQUESTS_ISSUED=0")

    if not CACHE.exists():
        fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", f"cache absent: {CACHE}")

    before = json.loads(CACHE.read_text(encoding="utf-8"))
    before_records = before["records"]
    before_by_name = {row["name"]: row for row in before_records}

    # --- renormalize: re-derive arity from the stored platform signature ------
    after_records: list[dict] = []
    unrepresentable_now: list[str] = []
    for row in before_records:
        source = {
            "name": row.get("name"),
            "signature": row.get("signature"),
            "description": row.get("description"),
        }
        record = _normalise_operator_record(source)
        if record is None:
            unrepresentable_now.append(str(row.get("name")))
            continue
        after_records.append(record)

    after_by_name = {row["name"]: row for row in after_records}

    # --- hard acceptance -----------------------------------------------------
    print("\n=== COUNT ===")
    print(f"before_records={len(before_records)}  after_records={len(after_records)}")
    if unrepresentable_now:
        fail(
            "CACHE_RENORMALIZATION_UNEXPECTED_DIFF",
            f"re-derivation lost operators: {unrepresentable_now}",
        )
    if len(after_records) != len(before_records):
        fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", "record count moved")

    before_names = [row["name"] for row in before_records]
    after_names = [row["name"] for row in after_records]
    print(f"names_identical={before_names == after_names}")
    if before_names != after_names:
        missing = sorted(set(before_names) - set(after_names))
        added = sorted(set(after_names) - set(before_names))
        fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", f"missing={missing} added={added}")

    if before_names != before["operators"]:
        fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", "stored operators list disagrees with records")

    # --- structured field-by-field diff --------------------------------------
    arity_diff: dict[str, tuple[int, int]] = {}
    other_diff: list[str] = []
    for name in before_names:
        old, new = before_by_name[name], after_by_name[name]
        if old.get("signature") != new.get("signature"):
            other_diff.append(f"{name}.signature: {old.get('signature')!r} -> {new.get('signature')!r}")
        if old.get("description") != new.get("description"):
            other_diff.append(f"{name}.description changed")
        if int(old["arity"]) != int(new["arity"]):
            arity_diff[name] = (int(old["arity"]), int(new["arity"]))

    print("\n=== BUSINESS DIFF (before -> after) ===")
    if not arity_diff:
        print("  (no arity change)")
    for name, (old_a, new_a) in sorted(arity_diff.items()):
        print(f"  {name}.arity: {old_a} -> {new_a}   signature={after_by_name[name]['signature']!r}")
    print("=== NON-ARITY DIFF ===")
    for line in other_diff:
        print(f"  {line}")
    if not other_diff:
        print("  (none)")

    if other_diff:
        fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", "name/signature/description must not move")
    if arity_diff != EXPECTED_ARITY_DIFF:
        fail(
            "CACHE_RENORMALIZATION_UNEXPECTED_DIFF",
            f"expected exactly {EXPECTED_ARITY_DIFF}, observed {arity_diff}",
        )

    # --- required gated arities ---------------------------------------------
    print("\n=== REQUIRED ARITY ===")
    bad: list[str] = []
    for name, expected in sorted(REQUIRED_ARITY.items()):
        got = after_by_name.get(name, {}).get("arity")
        mark = "OK " if got == expected else "BAD"
        print(f"  {mark} {name}: expected={expected} got={got}")
        if got != expected:
            bad.append(name)
    if bad:
        fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", f"gated operators wrong: {bad}")

    # --- preserved snapshot context -----------------------------------------
    after = dict(before)
    after["records"] = after_records
    print("\n=== PRESERVED CONTEXT ===")
    for key in PRESERVED_CONTEXT_KEYS:
        same = before.get(key) == after.get(key)
        print(f"  {key}={before.get(key)!r} unchanged={same}")
        if not same:
            fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", f"{key} moved")
    if set(after) != set(before):
        fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", "top-level key set moved")
    if after.get("excluded_unrepresentable") != before.get("excluded_unrepresentable"):
        fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", "exclusion list moved")

    # --- temp write, verify, atomic replace ---------------------------------
    TEMP.write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
    reread = json.loads(TEMP.read_text(encoding="utf-8"))
    if reread != after:
        fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", "temp file did not round-trip")
    if reread["records"][[r["name"] for r in reread["records"]].index("max")]["arity"] != 2:
        fail("CACHE_RENORMALIZATION_UNEXPECTED_DIFF", "temp file max arity is not 2")
    os.replace(TEMP, CACHE)

    final = json.loads(CACHE.read_text(encoding="utf-8"))
    final_by_name = {row["name"]: row for row in final["records"]}
    print("\n=== POST-SWAP READBACK ===")
    print(f"  records={len(final['records'])}  cached_at={final['cached_at']!r}")
    print(f"  max.arity={final_by_name['max']['arity']}  min.arity={final_by_name['min']['arity']}")
    print(f"  excluded_unrepresentable={final.get('excluded_unrepresentable')}")
    print("\nCATALOG_LOCAL_NORMALIZATION_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
