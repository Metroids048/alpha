"""Static reachability probe for the LOW_LOCAL_QUALITY gate. No LLM, no platform.

score = field + feedback + novelty + mechanism + knowledge + risk, capped by
evidence_cap. Every observed cycle reports positive=0 near_pass=0, so
feedback_component is 0 and evidence_cap is 85 rather than 100. That fixes every
term except field_component, so the gate reduces to a field-selection question:

    score = field + 60 - 26*similarity - complexity_penalty   (need >= 75)
"""

from statistics import mean, median

from alpha_mining.generation.high_quality import (
    HighQualityGenerator,
    _field_quality_component,
)
from alpha_mining.generation.snapshots import load_local_snapshots

snapshots = load_local_snapshots(root=".", allow_partial_offline=True, offline_max_age_hours=10_000)
threshold = 75.0

print("feedback: positive=%d near_pass=%d -> feedback_component=%.1f, evidence_cap=%.0f" % (
    len(snapshots.feedback.positive),
    len(snapshots.feedback.near_pass),
    min(20.0, len(snapshots.feedback.positive) * 10.0 + len(snapshots.feedback.near_pass) * 6.0),
    100.0 if (snapshots.feedback.positive or snapshots.feedback.near_pass) else 85.0,
))

visible = HighQualityGenerator._research_field_ids(snapshots, [])
scored = sorted(
    ((_field_quality_component((f,), snapshots), f) for f in visible), reverse=True
)
values = [s for s, _ in scored]

print("\nvisible fields: %d" % len(values))
print("field_quality  max=%.2f  p90=%.2f  median=%.2f  min=%.2f  mean=%.2f" % (
    max(values),
    sorted(values)[int(len(values) * 0.9)],
    median(values),
    min(values),
    mean(values),
))
print("\ntop 8 visible fields by field_quality:")
for score, field_id in scored[:8]:
    meta = snapshots.catalog.fields[field_id]
    print("  %6.2f  %-40s ds=%-10s cov=%-6s date_cov=%-6s users=%s" % (
        score, field_id, meta.dataset_id, meta.coverage, meta.date_coverage, meta.user_count,
    ))

# Best case the pipeline can actually reach: perfect novelty, no complexity penalty.
best_field = max(values)
best_total = min(best_field + 60.0, 85.0)
print("\n--- best case (similarity=0, complexity_penalty=0) ---")
print("field=%.2f + 60.00 = %.2f  (cap 85) -> %.2f   threshold %.1f -> %s" % (
    best_field, best_field + 60.0, best_total, threshold,
    "PASS" if best_total >= threshold else "FAIL",
))

print("\nfield_component needed to clear 75 at a given similarity (complexity_penalty=0):")
for sim in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5):
    need = 15.0 + 26.0 * sim
    ok = sum(1 for v in values if v >= need)
    print("  sim=%.1f -> need field>=%5.2f -> %d/%d visible fields qualify" % (
        sim, need, ok, len(values),
    ))

# Whole catalog, in case the visible quota is the limiting factor rather than the catalog.
all_values = [_field_quality_component((f,), snapshots) for f in snapshots.catalog.fields]
print("\nwhole catalog (%d fields): max=%.2f  p99=%.2f  median=%.2f" % (
    len(all_values), max(all_values), sorted(all_values)[int(len(all_values) * 0.99)], median(all_values),
))
print("fields in catalog with field_quality >= 15.00: %d" % sum(1 for v in all_values if v >= 15.0))
print("fields in catalog with field_quality >= 20.00: %d" % sum(1 for v in all_values if v >= 20.0))

# Metadata presence: coverage/user_count default to 0.75-weighted fallbacks when absent.
missing_cov = sum(1 for f in snapshots.catalog.fields.values() if getattr(f, "coverage", None) is None)
missing_users = sum(1 for f in snapshots.catalog.fields.values() if getattr(f, "user_count", None) is None)
print("\nmetadata absent: coverage=%d/%d  user_count=%d/%d" % (
    missing_cov, len(snapshots.catalog.fields), missing_users, len(snapshots.catalog.fields),
))
