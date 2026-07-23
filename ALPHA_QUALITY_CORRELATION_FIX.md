# Alpha Quality Correlation Fix

## Phase 1 safety boundary

- Status: **PASS**
- Ledger authority: `be3f8a22124ddf09f7b95f6a`; rows: `10000`.
- Blocker: `none`.
- PATCH endpoint calls: 0
- Submit endpoint calls: 0

Current-ledger eligibility is kept separate from legacy observations; missing or non-authoritative platform evidence remains blocked.

## Key components

- Generator: `BaselineFirstGenerator`
- Correlation zones: `FAR_FAIL`, `NEAR_PASS`
- Strategy: `OFAT` (One Factor At a Time)
- Arm tracking: `ResearchArmTracker`, `research_arm_metrics`
- Identity: `ResearchIdentity`
- Quotas: `BehaviorRoundQuota`, `GenerationQuota`
- Cluster management: `cluster_disposition`, `rank_parents`
