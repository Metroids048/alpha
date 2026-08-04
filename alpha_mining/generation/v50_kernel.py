"""A deliberately narrow, offline-only adapter around v50 generation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

from alpha_mining.domain.expression_normalization import expression_identity

if TYPE_CHECKING:
    from alpha_mining.generation.snapshots import LocalSnapshots


@dataclass(frozen=True)
class V50SeedBatch:
    candidates: tuple[Any, ...]
    catalog: Any
    history_pools: Any
    novelty_index: Any


class V50Kernel:
    """Use v50 as a seed generator without constructing its platform pipeline.

    This module imports only v50's pure classes.  In particular it never
    creates ``WorldQuantAlphaPipeline`` and therefore cannot authenticate,
    fetch a catalog, simulate, check, or submit.
    """

    def __init__(self, *, seed_pool_size: int = 80, correlation_ceiling: float = 0.65, history_ceiling: float = 0.72) -> None:
        self.seed_pool_size = max(12, int(seed_pool_size))
        self.correlation_ceiling = float(correlation_ceiling)
        self.history_ceiling = float(history_ceiling)

    def generate(self, snapshots: "LocalSnapshots") -> list[Any]:
        return list(self.generate_batch(snapshots).candidates)

    def generate_batch(self, snapshots: "LocalSnapshots") -> V50SeedBatch:
        import auto_alpha_pipeline_rebuilt_v50 as v50

        rows = [
            {
                "id": field.field_id,
                "_ds": field.dataset_id,
                "coverage": field.coverage if field.coverage is not None else float("nan"),
                "dateCoverage": field.date_coverage if field.date_coverage is not None else float("nan"),
                "userCount": field.user_count if field.user_count is not None else float("nan"),
                "description": field.description,
            }
            for field in snapshots.catalog.fields.values()
        ]
        catalog = v50.FieldCatalog.from_df(pd.DataFrame(rows))
        config = v50.PipelineConfig(
            username="",
            password="",
            mode="generate",
            region=str(snapshots.catalog.info.get("region") or "USA"),
            universe=str(snapshots.catalog.info.get("universe") or "TOP3000"),
            delay=int(snapshots.catalog.info.get("delay") or 1),
            budget=self.seed_pool_size,
            field_top_n=len(rows),
            candidate_multiplier=1,
            min_candidates_floor=1,
            alpha_models_enabled=False,
            generate_template_rescue=False,
            fallback_disable_library_skeleton_dedup=False,
            fallback_disable_history_skeleton_dedup=False,
            max_history_similarity=self.history_ceiling,
            behavior_similarity_cap=self.correlation_ceiling,
            generated_near_clone_similarity=self.history_ceiling,
            sync_platform_tried_before_simulate=False,
            phase3_llm_grammar_enabled=False,
            phase5_judge_enabled=False,
        )
        validator = v50.PreflightValidator(catalog, min_ts_corr_window=config.min_ts_corr_window)
        pools = v50.HistorySimilarityPools()
        novelty = v50.NoveltyIndex()
        history_seen: set[str] = set()
        history_skeletons: set[str] = set()
        for record in snapshots.feedback.records:
            expression = record.expression
            if not expression:
                continue
            history_seen.add(expression)
            history_skeletons.add(expression_identity(expression).field_skeleton)
            novelty.add(expression)
            tier = "self_corr_risk" if record.self_corr_risk else "near_pass" if record.outcome.upper() == "NEAR_PASS" else "passed" if record.outcome.upper() in {"PASS", "READY_TO_SUBMIT"} else "weak_fail"
            pools.append_behavior_tokens(expression, tier) if tier == "self_corr_risk" else pools.append_tokens(expression, tier)
        factory = v50.ExpressionFactory(config, catalog, validator)
        candidates = factory.generate(
            history_seen,
            history_skeletons,
            pools,
            set(),
            tried_exact=history_seen,
        )
        # Near-pass amplification is strictly additive and remains subject to
        # the downstream hard gates.  Its absence is a valid zero-output case.
        near_records = [
            {"expression": item.expression, "sharpe": 1.1}
            for item in snapshots.feedback.near_pass
            if item.expression
        ]
        if near_records:
            amplifier = v50.NearPassAmplifier(config, catalog, validator)
            candidates = amplifier.amplify(near_records, history_seen | {item.expression for item in candidates}) + candidates
        return V50SeedBatch(tuple(candidates[: self.seed_pool_size]), catalog, pools, novelty)
