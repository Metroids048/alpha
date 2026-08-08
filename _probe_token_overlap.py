"""Decompose the proxy-similarity token overlap. No LLM, no platform.

_similarity tokenizes behavior_signature with r"[a-z_]+|\\d+" and takes Jaccard.
That puts operator names and numeric windows into the same bag as field names,
so two candidates on unrelated datasets collide on shared windows alone.
"""

import re
from collections import Counter

from alpha_mining.domain.expression_normalization import behavior_signature
from alpha_mining.generation.high_quality import _similarity
from alpha_mining.generation.snapshots import load_local_snapshots

snapshots = load_local_snapshots(root=".", allow_partial_offline=True, offline_max_age_hours=10_000)
existing = [i.expression for i in snapshots.feedback.records if i.grounded and i.expression]
existing += list(snapshots.inventory.expressions)


def tokens(expression: str) -> set[str]:
    return set(re.findall(r"[a-z_]+|\d+", behavior_signature(expression).lower()))


A = "ts_delta(sector_12mo_marketcap_percent,63) / ts_std_dev(sector_12mo_marketcap_percent,126)"
B = "ts_zscore(ts_delta(social_pillar_composite_score,63),126)"

ta, tb = tokens(A), tokens(B)
print("A = %s" % A)
print("  signature: %s" % behavior_signature(A))
print("  tokens   : %s" % sorted(ta))
print("\nB = %s   (nearest history item)" % B)
print("  signature: %s" % behavior_signature(B))
print("  tokens   : %s" % sorted(tb))
print("\nshared tokens driving the score : %s" % sorted(ta & tb))
print("A-only                          : %s" % sorted(ta - tb))
print("B-only                          : %s" % sorted(tb - ta))
print("jaccard = %d/%d = %.3f   (_similarity=%.3f)" % (
    len(ta & tb), len(ta | tb), len(ta & tb) / len(ta | tb), _similarity(A, B),
))
print("\nDifferent dataset, different field, different operator topology.")
print("cost of sim=%.3f against the 5.00-point margin: %.2f points" % (
    _similarity(A, B), 26.0 * _similarity(A, B),
))

# How crowded is the token space the prompt steers every candidate into?
print("\n--- collision surface across %d existing expressions ---" % len(existing))
counts: Counter[str] = Counter()
for item in existing:
    counts.update(tokens(item))
print("most common tokens in history/inventory:")
for token, n in counts.most_common(12):
    kind = "window" if token.isdigit() else "name-fragment"
    print("  %-28s %3d/%d  (%s)" % (token, n, len(existing), kind))

windows = {t for t in counts if t.isdigit()}
print("\ndistinct numeric windows already present: %s" % sorted(windows, key=int))
print("prompt mandates windows >= 21 (>= 42 for ts_corr), which concentrates every")
print("candidate onto the same conventional set, guaranteeing token collisions.")
