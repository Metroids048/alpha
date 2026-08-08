"""Which branch of MECHANISM_FIELD_MISMATCH fires, and on what text.

_mechanism_issue returns MECHANISM_FIELD_MISMATCH from two places:
  1. claimed_fields != set(fields)        - role table disagrees with expression
  2. rationale mentions a catalog field not in fields  - scans all 5697 field IDs
Branch 2 searches every catalog field ID against free prose, so a common English
word that happens to be a field ID would convict any rationale containing it.
"""

import json
import re

from alpha_mining.generation import high_quality as hq
from alpha_mining.generation.production import main

records: list[dict] = []
_orig = hq._mechanism_issue


def _traced(row, expression, fields, functions, snapshots):
    verdict = _orig(row, expression, fields, functions, snapshots)
    if verdict == "MECHANISM_FIELD_MISMATCH":
        field_roles = row.get("field_roles")
        claimed = sorted(
            str(i.get("field_id") or "").strip()
            for i in (field_roles if isinstance(field_roles, list) else [])
            if isinstance(i, dict) and str(i.get("role") or "").strip()
        )
        rationale = str(row.get("economic_rationale") or "")
        mentioned = sorted(
            f for f in snapshots.catalog.fields
            if re.search(rf"(?<![a-z0-9_]){re.escape(f)}(?![a-z0-9_])", rationale, flags=re.IGNORECASE)
        )
        leaked = sorted(set(mentioned) - set(fields))
        records.append({
            "expression": str(expression),
            "expression_fields": sorted(fields),
            "claimed_fields": claimed,
            "branch": "role_table" if set(claimed) != set(fields) else "rationale_mentions_other_field",
            "rationale": rationale,
            "rationale_field_hits": mentioned,
            "offending_field_ids": leaked,
        })
    return verdict


hq._mechanism_issue = _traced
code = main(["--once", "--offline-catalog-max-age-hours", "10000"])

print("\n===== %d MECHANISM_FIELD_MISMATCH hits =====" % len(records))
for item in records:
    print(json.dumps(item, ensure_ascii=False, indent=2))

branches: dict[str, int] = {}
for item in records:
    branches[item["branch"]] = branches.get(item["branch"], 0) + 1
print("\nbranch counts: %s" % branches)
offenders: dict[str, int] = {}
for item in records:
    for f in item["offending_field_ids"]:
        offenders[f] = offenders.get(f, 0) + 1
print("field IDs convicting a rationale: %s" % sorted(offenders.items(), key=lambda kv: -kv[1])[:12])
print("exit=%s" % code)
