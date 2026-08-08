"""Measure the real proxy-similarity distribution against the gate arithmetic.

No LLM, no platform. Uses the actual history/inventory the pipeline reads, and
candidate expressions built from the visible catalog exactly as the prompt asks
(change + normalizer, ratio of two fields, peer-relative shapes).

The two gates disagree by construction:
  similarity gate : reject when sim >= 0.65 (cycle) / 0.72 (history, inventory)
  quality gate    : score = field + 60 - 26*sim - cplx, need >= 75, field <= 20
                    => sim > 0.192 can never clear 75, even with a perfect field
So any candidate landing in 0.192 < sim < 0.72 passes the similarity gate and is
then killed by LOW_LOCAL_QUALITY, reported as a quality failure.
"""

from alpha_mining.generation.high_quality import _similarity, _field_quality_component
from alpha_mining.generation.snapshots import load_local_snapshots

snapshots = load_local_snapshots(root=".", allow_partial_offline=True, offline_max_age_hours=10_000)

history = [i.expression for i in snapshots.feedback.records if i.grounded and i.expression]
inventory = list(snapshots.inventory.expressions)
existing = history + inventory
print("history=%d inventory=%d existing=%d" % (len(history), len(inventory), len(existing)))

# Realistic candidates: shapes the prompt explicitly steers toward, on real fields.
candidates = [
    "ts_delta(sector_12mo_marketcap_percent,63) / ts_std_dev(sector_12mo_marketcap_percent,126)",
    "rank(ts_delta(sector_fy3_marketcap_percent,63)) - rank(ts_delta(sector_18mo_marketcap_percent,63))",
    "ts_zscore(sector_government_bond_yield,252)",
    "ts_rank(industry_fy2_marketcap_percent,126) / ts_rank(industry_fy3_marketcap_percent,126)",
    "ts_delta(industry_government_bond_yield,42) / ts_std_dev(industry_government_bond_yield,252)",
]

print("\n%-72s %6s %6s %s" % ("candidate", "maxsim", "score", "verdict"))
print("-" * 104)
for expression in candidates:
    sim = max((_similarity(expression, item) for item in existing), default=0.0)
    fields = tuple(
        f for f in snapshots.catalog.fields
        if f in expression
    )
    field_score = _field_quality_component(fields, snapshots) if fields else 0.0
    score = min(field_score + 60.0 - 26.0 * sim, 85.0)
    passes_sim = sim < 0.72
    passes_quality = score >= 75.0
    verdict = (
        "ENQUEUE" if passes_sim and passes_quality
        else ("LOW_LOCAL_QUALITY (sim passed!)" if passes_sim else "SIMILARITY")
    )
    print("%-72s %6.3f %6.2f %s" % (expression[:72], sim, score, verdict))

# Where does the killing similarity come from?
worst = candidates[0]
ranked = sorted(((_similarity(worst, item), item) for item in existing), reverse=True)[:3]
print("\nnearest existing expressions to candidate #1 (sim=%.3f):" % ranked[0][0] if ranked else "no history")
for sim, item in ranked:
    print("  %.3f  %s" % (sim, item[:96]))

# The dead band: passes similarity, cannot pass quality.
print("\n--- gate consistency ---")
print("similarity gate rejects at   : sim >= 0.65 (cycle) / 0.72 (history, inventory)")
print("quality gate becomes unreachable at: sim > %.3f  (field=20.00 max, cplx=0)" % ((20.0 + 60.0 - 75.0) / 26.0))
print("dead band 0.192 < sim < 0.720 : similarity says OK, quality is arithmetically impossible")

band = [
    (sim, e) for e, sim in (
        (e, max((_similarity(e, i) for i in existing), default=0.0)) for e in candidates
    ) if 0.192 < sim < 0.72
]
print("candidates landing in the dead band: %d/%d" % (len(band), len(candidates)))
