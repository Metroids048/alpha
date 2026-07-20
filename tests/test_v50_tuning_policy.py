import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "auto_alpha_pipeline_rebuilt_v50.py"
SPEC = importlib.util.spec_from_file_location(
    "auto_alpha_pipeline_rebuilt_v50", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def payload(family: str, score: float = 1.0, source: str = "pass_first") -> dict:
    return {
        "type": "REGULAR",
        "regular": f"group_neutralize(ts_zscore({family}_field/cap,126),market)",
        "settings": {},
        "meta": {
            "family": family,
            "source": source,
            "candidate_score": score,
            "variant": 0,
        },
    }


class V50TuningPolicyTests(unittest.TestCase):
    def test_default_fast_quality_batch_config(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")

        self.assertEqual(cfg.min_simulate_batch, 180)
        self.assertEqual(cfg.target_simulate_batch, 180)
        self.assertEqual(cfg.max_simulate_batch_per_run, 240)
        self.assertEqual(cfg.diversity_mode, "quality_diverse")
        self.assertEqual(cfg.recheck_postbatch_max_items, 4)
        self.assertEqual(cfg.recheck_postbatch_quick_timeout_seconds, 90.0)
        self.assertEqual(cfg.recheck_postbatch_wall_budget_seconds, 180.0)
        self.assertEqual(cfg.max_concurrent_simulations, 6)
        self.assertEqual(cfg.max_concurrent_simulation_posts, 1)

    def test_family_quality_penalty_demotes_bad_sample_families(self) -> None:
        good = payload("near_pass_variant", score=1.0, source="near_pass")
        bad_delta = payload("pass_fundamental_delta", score=9.0)
        bad_template = payload(
            "alpha_models_template", score=9.0, source="Alpha Models.csv"
        )
        rates = {"near_pass_variant": 0.35, "pass_fundamental": 0.03, "template": 0.0}
        quality = {
            "near_pass_variant": {
                "pass_proxy_rate": 0.35,
                "bad_metric_rate": 0.0,
                "hard_fail_rate": 0.0,
            },
            "pass_fundamental_delta": {
                "pass_proxy_rate": 0.03,
                "bad_metric_rate": 0.45,
                "hard_fail_rate": 0.95,
            },
            "alpha_models_template": {
                "pass_proxy_rate": 0.0,
                "bad_metric_rate": 0.25,
                "hard_fail_rate": 0.95,
            },
        }

        ordered = sorted(
            [bad_delta, bad_template, good],
            key=lambda p: MODULE._payload_fine_rank_key(p, rates, quality),
        )

        self.assertEqual(ordered[0]["meta"]["family"], "near_pass_variant")
        self.assertEqual(ordered[-1]["meta"]["family"], "alpha_models_template")

    def test_fine_rank_prefers_simpler_expression_when_quality_is_equal(self) -> None:
        simple = payload("near_pass_variant", score=1.0, source="near_pass")
        simple["regular"] = "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),market)"
        complex_payload = payload("near_pass_variant", score=1.0, source="near_pass")
        complex_payload["regular"] = (
            "group_neutralize(ts_zscore(((group_rank(ts_delta(fnd6_test_ebit,252)/cap,sector)-0.5)"
            "*rank(ts_mean(volume,63)/adv20)),126),market)"
        )

        ordered = sorted(
            [complex_payload, simple],
            key=lambda p: MODULE._payload_fine_rank_key(p, {}, {}),
        )

        self.assertIs(ordered[0], simple)

    def test_validator_allows_conservative_trade_when_without_comparators(self) -> None:
        catalog = MODULE.FieldCatalog(
            df=None,
            ids={"fnd6_test_ebit"},
            by_ds={},
            fund=["fnd6_test_ebit"],
            analyst=[],
            model=[],
            sent=[],
            pv=[],
            other=[],
        )
        validator = MODULE.PreflightValidator(catalog)

        ok, reason = validator.validate(
            "trade_when(rank(ts_mean(volume,63)/adv20), group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),market), -1)"
        )

        self.assertTrue(ok, reason)

    def test_near_pass_amplifier_does_not_emit_over_complex_variants(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        catalog = MODULE.FieldCatalog(
            df=None,
            ids={"fnd6_test_ebit"},
            by_ds={},
            fund=["fnd6_test_ebit"],
            analyst=[],
            model=[],
            sent=[],
            pv=[],
            other=[],
        )
        validator = MODULE.PreflightValidator(catalog)
        amplifier = MODULE.NearPassAmplifier(cfg, catalog, validator)
        complex_seed = (
            "group_neutralize(ts_zscore(((group_rank(ts_delta(fnd6_test_ebit,252)/cap,sector)-0.5)"
            "*rank(ts_mean(volume,63)/adv20)),126),market)"
        )

        amplified = amplifier.amplify(
            [{"expression": complex_seed, "sharpe": 1.10}],
            tried_exact=set(),
        )

        self.assertGreater(len(amplified), 0)
        self.assertTrue(
            all(
                MODULE.PreSimulationScreener._nesting_depth(c.expression)
                <= cfg.prescreen_max_nesting_depth
                for c in amplified
            )
        )
        self.assertTrue(
            all(
                MODULE.PreSimulationScreener._function_calls(c.expression)
                <= cfg.prescreen_max_function_calls
                for c in amplified
            )
        )

    def test_near_pass_amplifier_keeps_safe_original_for_settings_retry(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        catalog = MODULE.FieldCatalog(
            df=None,
            ids={"fnd6_test_ebit"},
            by_ds={},
            fund=["fnd6_test_ebit"],
            analyst=[],
            model=[],
            sent=[],
            pv=[],
            other=[],
        )
        validator = MODULE.PreflightValidator(catalog)
        amplifier = MODULE.NearPassAmplifier(cfg, catalog, validator)
        seed = "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),market)"

        amplified = amplifier.amplify(
            [{"expression": seed, "sharpe": 1.12}],
            tried_exact={seed},
        )

        self.assertIn(seed, {c.expression for c in amplified})

    def test_allocator_keeps_near_pass_majority_and_drops_low_quality_templates(
        self,
    ) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.min_simulate_batch = 300
        cfg.near_pass_batch_quota = 180
        cfg.min_near_pass_batch_share = 0.60
        cfg.alpha_models_batch_quota = 0
        cfg.pass_first_batch_quota = 60
        cfg.pass_fundamental_ts_max_per_batch = 30
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        pipeline._family_pass_rates = {
            "near_pass_variant": 0.35,
            "pass_fundamental": 0.03,
            "template": 0.0,
        }
        pipeline._family_quality_stats = {
            "near_pass_variant": {
                "pass_proxy_rate": 0.35,
                "bad_metric_rate": 0.0,
                "hard_fail_rate": 0.0,
            },
            "pass_fundamental_delta": {
                "pass_proxy_rate": 0.03,
                "bad_metric_rate": 0.45,
                "hard_fail_rate": 0.95,
            },
            "alpha_models_template": {
                "pass_proxy_rate": 0.0,
                "bad_metric_rate": 0.25,
                "hard_fail_rate": 0.95,
            },
        }
        payloads = (
            [payload("near_pass_variant", source="near_pass") for _ in range(220)]
            + [
                payload("alpha_models_template", source="Alpha Models.csv")
                for _ in range(120)
            ]
            + [payload("pass_fundamental_delta") for _ in range(160)]
            + [payload("pass_fundamental_ts") for _ in range(80)]
        )

        selected, stats = pipeline._allocate_payload_budget(payloads, 300)
        families = [p["meta"]["family"] for p in selected]

        self.assertGreaterEqual(families.count("near_pass_variant"), 180)
        self.assertEqual(families.count("alpha_models_template"), 0)
        self.assertLessEqual(families.count("pass_fundamental_delta"), 60)
        self.assertEqual(stats["selected_total"], 300)

    def test_diverse_exploration_preset_has_bounded_pilot_policy(self) -> None:
        cfg = MODULE.PipelineConfig(
            username="u", password="p", preset="diverse_exploration"
        )
        MODULE.WorldQuantAlphaPipeline(cfg)

        self.assertEqual(cfg.diversity_mode, "quality_diverse")
        self.assertEqual(cfg.min_simulate_batch, 60)
        self.assertEqual(cfg.target_simulate_batch, 60)
        self.assertEqual(cfg.near_pass_batch_quota, 21)
        self.assertEqual(cfg.arch_explore_batch_quota, 9)
        self.assertFalse(cfg.prescreen_relax_to_hit_min_batch)

    def test_diverse_allocator_rotates_least_sampled_archetypes_with_shared_cap(
        self,
    ) -> None:
        cfg = MODULE.PipelineConfig(
            username="u", password="p", preset="diverse_exploration"
        )
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        pipeline._family_simulation_counts = MODULE.Counter(
            {"arch_a": 100, "arch_b": 2, "arch_c": 1}
        )
        payloads = (
            [payload("near_pass_variant", source="near_pass") for _ in range(40)]
            + [payload("pass_fundamental_ts") for _ in range(25)]
            + [payload("pass_pv") for _ in range(5)]
            + [payload("arch_a") for _ in range(10)]
            + [payload("arch_b") for _ in range(10)]
            + [payload("arch_c") for _ in range(10)]
        )

        selected, stats = pipeline._allocate_payload_budget(payloads, 60)
        families = [p["meta"]["family"] for p in selected]

        self.assertEqual(len(selected), 60)
        self.assertEqual(sum(f.startswith("arch_") for f in families), 9)
        self.assertEqual(families.count("near_pass_variant"), 21)
        self.assertGreaterEqual(families.count("pass_fundamental_ts"), 14)
        self.assertEqual(stats["arch_explore"], 9)
        self.assertGreaterEqual(stats["archetype:arch_c"], stats["archetype:arch_a"])

    def test_toxic_similarity_thresholds_are_family_specific(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")

        self.assertEqual(
            MODULE._toxic_similarity_cap_for_family(cfg, "near_pass_variant"), 0.55
        )
        self.assertEqual(
            MODULE._toxic_similarity_cap_for_family(cfg, "pass_fundamental_ts"), 0.70
        )
        self.assertEqual(
            MODULE._toxic_similarity_cap_for_family(cfg, "arch_hybrid_delta_pv"), 0.85
        )

    def test_phase_two_field_diversity_controls_default(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        # v50.7: cross-field near-pass variants are enabled by default — parameter-only
        # variants collide with their own seed on _behavior_signature and are dropped by
        # the batch-level throughput_behavior_block gate before ever reaching simulate.
        self.assertTrue(cfg.enable_near_pass_cross_field_variants)
        # v50.7: field selection no longer rewards popular (high userCount) fields by
        # default — that pushed candidates toward the fields most likely to already be
        # crowded/self-correlated on the platform.
        self.assertTrue(cfg.prefer_underused_fields)
        self.assertEqual(cfg.underused_field_share, 0.15)

    def test_payload_fingerprint_ignores_expression_whitespace(self) -> None:
        compact = "group_neutralize(ts_zscore(fnd6_sales/cap,126),market)"
        spaced = "group_neutralize( ts_zscore( fnd6_sales / cap, 126 ), market )"
        settings = {"neutralization": "MARKET", "decay": 4, "truncation": 0.05}

        self.assertEqual(
            MODULE._payload_fingerprint(compact, settings),
            MODULE._payload_fingerprint(spaced, settings),
        )

    def test_diverse_preset_disables_near_pass_settings_resimulation(self) -> None:
        cfg = MODULE.PipelineConfig(
            username="u", password="p", preset="diverse_exploration"
        )
        MODULE.WorldQuantAlphaPipeline(cfg)

        self.assertFalse(cfg.prescreen_allow_near_pass_settings_retry)

    def test_diverse_prescreen_blocks_whitespace_variant_of_tried_expression(
        self,
    ) -> None:
        cfg = MODULE.PipelineConfig(
            username="u", password="p", preset="diverse_exploration"
        )
        MODULE.WorldQuantAlphaPipeline(cfg)
        tried = "group_neutralize(ts_zscore(fnd6_sales/cap,126),market)"
        spaced = "group_neutralize( ts_zscore( fnd6_sales / cap, 126 ), market )"
        screener = MODULE.PreSimulationScreener(
            cfg,
            tried_exact={tried},
            tried_payload_keys=set(),
            near_pass_expressions={tried},
            failed_cluster={},
            history_pools=MODULE.HistorySimilarityPools(),
            top_field_lookup=lambda _expr: None,
            tried_metrics={},
        )

        kept, reasons, _ = screener.screen(
            [
                {
                    "regular": spaced,
                    "settings": {
                        "neutralization": "MARKET",
                        "decay": 8,
                        "truncation": 0.05,
                    },
                    "meta": {"family": "near_pass_variant", "source": "near_pass"},
                }
            ]
        )

        self.assertEqual(kept, [])
        self.assertEqual(reasons["already_simulated_expr"], 1)

    def test_validator_rejects_adv20_plus_dimensionless_constant(self) -> None:
        catalog = MODULE.FieldCatalog(
            df=None,
            ids={"fnd6_sales"},
            by_ds={},
            fund=["fnd6_sales"],
            analyst=[],
            model=[],
            sent=[],
            pv=[],
            other=[],
        )
        validator = MODULE.PreflightValidator(catalog)

        ok, reason = validator.validate(
            "group_neutralize(ts_zscore(fnd6_sales/cap,126),market)*rank(ts_mean(volume,63)/(1+adv20))"
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "dimensioned_constant_addition:adv20")

    def test_diverse_allocator_does_not_fill_with_unproven_templates(self) -> None:
        cfg = MODULE.PipelineConfig(
            username="u", password="p", preset="diverse_exploration"
        )
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        payloads = (
            [payload("near_pass_variant", source="near_pass") for _ in range(30)]
            + [payload("pass_fundamental_ts") for _ in range(14)]
            + [payload("arch_a") for _ in range(9)]
            + [
                payload("alpha_models_template", source="Alpha Models.csv")
                for _ in range(40)
            ]
        )

        selected, _ = pipeline._allocate_payload_budget(payloads, 60)
        families = [p["meta"]["family"] for p in selected]

        self.assertEqual(families.count("near_pass_variant"), 21)
        self.assertEqual(families.count("pass_fundamental_ts"), 14)
        self.assertEqual(families.count("arch_a"), 9)
        self.assertNotIn("alpha_models_template", families)
        self.assertEqual(len(selected), 44)

    def test_cross_field_variants_require_opt_in_and_change_dataset(self) -> None:
        catalog = MODULE.FieldCatalog(
            df=None,
            ids={"fnd6_sales", "fundamental65_sales"},
            by_ds={
                "fundamental6": ["fnd6_sales"],
                "fundamental65": ["fundamental65_sales"],
            },
            fund=["fnd6_sales", "fundamental65_sales"],
            analyst=[],
            model=[],
            sent=[],
            pv=[],
            other=[],
            field_dataset={
                "fnd6_sales": "fundamental6",
                "fundamental65_sales": "fundamental65",
            },
        )
        validator = MODULE.PreflightValidator(catalog)
        seed = "group_neutralize(ts_zscore(fnd6_sales/cap,126),market)"
        disabled_cfg = MODULE.PipelineConfig(
            username="u", password="p", enable_near_pass_cross_field_variants=False
        )
        disabled = MODULE.NearPassAmplifier(disabled_cfg, catalog, validator)
        self.assertNotIn("fundamental65_sales", " ".join(disabled._variants_for(seed)))

        # v50.7: enabled by default (no explicit opt-in needed).
        cfg = MODULE.PipelineConfig(username="u", password="p")
        enabled = MODULE.NearPassAmplifier(cfg, catalog, validator)
        self.assertIn("fundamental65_sales", " ".join(enabled._variants_for(seed)))

    def test_underused_field_preference_is_default(self) -> None:
        fields = MODULE.pd.DataFrame(
            [
                {
                    "id": "fnd6_sales",
                    "_ds": "fundamental6",
                    "coverage": 1.0,
                    "dateCoverage": 1.0,
                    "userCount": 100,
                },
                {
                    "id": "fundamental65_sales",
                    "_ds": "fundamental65",
                    "coverage": 1.0,
                    "dateCoverage": 1.0,
                    "userCount": 0,
                },
            ]
        )
        # v50.7: prefer_underused_fields defaults True — top_fields() no longer
        # rewards crowded (high userCount) fields by default. Rewarding popularity
        # pushed candidate generation toward the same fields everyone else already
        # mines, which is exactly the neighbourhood most likely to fail platform
        # SELF_CORRELATION.
        default_cfg = MODULE.PipelineConfig(username="u", password="p", field_top_n=1)
        default_pipeline = MODULE.WorldQuantAlphaPipeline(default_cfg)
        self.assertEqual(
            default_pipeline.top_fields(fields).iloc[0]["id"], "fundamental65_sales"
        )

        popularity_cfg = MODULE.PipelineConfig(
            username="u", password="p", field_top_n=1, prefer_underused_fields=False
        )
        popularity_pipeline = MODULE.WorldQuantAlphaPipeline(popularity_cfg)
        self.assertEqual(
            popularity_pipeline.top_fields(fields).iloc[0]["id"], "fnd6_sales"
        )

    def test_near_pass_ignores_generated_near_clone_gate(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.diversity_mode = "pass_rate"
        generated_pool = MODULE.HistorySimilarityPools()
        expr = "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),market)"
        generated_pool.append_tokens(expr, "generated")
        screener = MODULE.PreSimulationScreener(
            cfg,
            tried_exact=set(),
            tried_payload_keys=set(),
            near_pass_expressions=set(),
            failed_cluster={},
            history_pools=generated_pool,
            top_field_lookup=lambda _e: None,
            tried_metrics={},
        )

        kept, reasons, _ = screener.screen(
            [
                {
                    "regular": expr,
                    "settings": {},
                    "meta": {"family": "near_pass_variant", "source": "near_pass"},
                }
            ]
        )

        self.assertEqual(len(kept), 1)
        self.assertFalse(any(k.startswith("generated_near_clone") for k in reasons))

    def test_behavior_signature_collapses_sign_offset_and_settings_only_variants(
        self,
    ) -> None:
        expr = "group_neutralize(ts_rank(fnd6_test_ebit/cap,126)-0.5,market)"

        sig = MODULE._behavior_signature(expr)

        self.assertEqual(MODULE._behavior_signature(f"-({expr})"), sig)
        self.assertEqual(MODULE._behavior_signature(f"({expr})*-1"), sig)
        self.assertEqual(
            MODULE._behavior_signature(
                "group_neutralize(ts_rank(fnd6_test_ebit/cap,126),market)"
            ),
            sig,
        )

    def test_quality_diverse_near_pass_blocks_generated_near_clones(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.diversity_mode = "quality_diverse"
        generated_pool = MODULE.HistorySimilarityPools()
        expr = "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),market)"
        generated_pool.append_tokens(expr, "generated")
        screener = MODULE.PreSimulationScreener(
            cfg,
            tried_exact=set(),
            tried_payload_keys=set(),
            near_pass_expressions=set(),
            failed_cluster={},
            history_pools=generated_pool,
            top_field_lookup=lambda _e: None,
            tried_metrics={},
        )

        kept, reasons, _ = screener.screen(
            [
                {
                    "regular": expr,
                    "settings": {},
                    "meta": {"family": "near_pass_variant", "source": "near_pass"},
                }
            ]
        )

        self.assertEqual(kept, [])
        self.assertTrue(any(k.startswith("generated_near_clone") for k in reasons))

    def test_quality_diverse_payload_selector_does_not_expand_settings_only_variants(
        self,
    ) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.diversity_mode = "quality_diverse"
        selector = MODULE.ProfileSelector(cfg)
        candidates = [
            MODULE.ExpressionCandidate(
                "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),market)",
                "near_pass_variant",
                "near_pass",
                4.0,
            )
        ]

        payloads = selector.payloads_for(candidates, max_payloads=10)

        self.assertEqual(len(payloads), 1)

    def test_quality_diverse_fine_selection_prefers_lower_behavior_similarity(
        self,
    ) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.diversity_mode = "quality_diverse"
        cfg.behavior_similarity_cap = 0.95
        screener = MODULE.PreSimulationScreener(
            cfg,
            tried_exact=set(),
            tried_payload_keys=set(),
            near_pass_expressions=set(),
            failed_cluster={},
            history_pools=MODULE.HistorySimilarityPools(),
            top_field_lookup=lambda _e: None,
            tried_metrics={},
        )
        base = {
            "regular": "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),market)",
            "settings": {},
            "meta": {
                "family": "near_pass_variant",
                "source": "near_pass",
                "candidate_score": 5.0,
            },
        }
        near_clone = {
            "regular": "group_neutralize(ts_zscore(fnd6_test_ebit/cap,252),market)",
            "settings": {},
            "meta": {
                "family": "near_pass_variant",
                "source": "near_pass",
                "candidate_score": 4.9,
            },
        }
        diverse = {
            "regular": "group_neutralize(ts_corr(rank(returns),rank(volume),63),sector)",
            "settings": {},
            "meta": {
                "family": "near_pass_variant",
                "source": "near_pass",
                "candidate_score": 4.0,
            },
        }

        selected, _, _ = screener.select_diverse_for_simulate(
            [base, near_clone, diverse], 2
        )

        self.assertEqual(
            [p["regular"] for p in selected], [base["regular"], diverse["regular"]]
        )

    def test_self_correlation_risk_pool_blocks_related_candidates(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.diversity_mode = "quality_diverse"
        pools = MODULE.HistorySimilarityPools()
        expr = "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),market)"
        pools.append_tokens(expr, "self_corr_risk")
        screener = MODULE.PreSimulationScreener(
            cfg,
            tried_exact=set(),
            tried_payload_keys=set(),
            near_pass_expressions=set(),
            failed_cluster={},
            history_pools=pools,
            top_field_lookup=lambda _e: None,
            tried_metrics={},
        )

        kept, reasons, _ = screener.screen(
            [
                {
                    "regular": expr,
                    "settings": {},
                    "meta": {"family": "near_pass_variant", "source": "near_pass"},
                }
            ]
        )

        self.assertEqual(kept, [])
        self.assertTrue(any(k.startswith("self_corr_risk") for k in reasons))

    def test_underfilled_allocator_caps_low_yield_arch_payloads(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.min_simulate_batch = 300
        cfg.min_near_pass_batch_share = 0.70
        cfg.max_arch_explore_batch_share = 0.03
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        payloads = (
            [payload("near_pass_variant", source="near_pass") for _ in range(8)]
            + [payload("arch_hybrid_z_pv", source="proven") for _ in range(4)]
            + [payload("arch_hybrid_delta_pv", source="proven") for _ in range(3)]
            + [payload("arch_vol_scaled", source="proven") for _ in range(20)]
            + [payload("arch_analyst_ts", source="proven") for _ in range(20)]
        )

        selected, stats = pipeline._allocate_payload_budget(payloads, 300)
        families = [p["meta"]["family"] for p in selected]
        low_yield_arch = [
            fam
            for fam in families
            if fam.startswith("arch_")
            and not MODULE._is_priority_arch_quality_family(fam)
        ]

        self.assertEqual(families.count("near_pass_variant"), 8)
        self.assertLessEqual(
            len(low_yield_arch), int(300 * cfg.max_arch_explore_batch_share)
        )
        self.assertLess(stats["low_yield_underfill_included"], 40)
        self.assertLess(stats["selected_total"], 55)

    def test_platform_sync_demotes_hybrid_arch_and_templates_from_quality_candidates(
        self,
    ) -> None:
        self.assertFalse(
            MODULE._is_quality_simulate_family("arch_hybrid_z_pv", "proven")
        )
        self.assertFalse(
            MODULE._is_quality_simulate_family("arch_hybrid_delta_pv", "proven")
        )
        self.assertFalse(
            MODULE._is_quality_simulate_family("arch_vol_scaled", "proven")
        )
        self.assertFalse(
            MODULE._is_quality_simulate_family("arch_analyst_ts", "proven")
        )
        self.assertFalse(
            MODULE._is_quality_simulate_family(
                "alpha_models_template", "Alpha Models.csv"
            )
        )

    def test_salvage_topup_prefers_near_pass_then_allows_controlled_explore(
        self,
    ) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.diversity_mode = "pass_rate"
        cfg.max_arch_explore_batch_share = 0.30
        screener = MODULE.PreSimulationScreener(
            cfg,
            tried_exact=set(),
            tried_payload_keys=set(),
            near_pass_expressions=set(),
            failed_cluster={},
            history_pools=MODULE.HistorySimilarityPools(),
            top_field_lookup=lambda _e: None,
            tried_metrics={},
        )
        near = [payload("near_pass_variant", source="near_pass") for _ in range(2)]
        quality = [payload("pass_fundamental_delta") for _ in range(2)]
        explore = [payload("arch_vol_scaled", source="proven") for _ in range(8)]
        for i, item in enumerate(near + quality + explore):
            fam = item["meta"]["family"]
            item["regular"] = (
                f"group_neutralize(ts_zscore({fam}_{i}_field/cap,126),market)"
            )

        topup, stats = screener.select_salvage_topup(
            near + quality + explore,
            already=[],
            need=10,
            target_n=10,
            history_similarity_cap=0.88,
        )
        families = [p["meta"]["family"] for p in topup]

        self.assertEqual(families[:2], ["near_pass_variant", "near_pass_variant"])
        self.assertEqual(families.count("arch_vol_scaled"), 3)
        self.assertEqual(stats["salvage_topup"], 7)
        self.assertEqual(stats["salvage_arch"], 3)
        self.assertEqual(stats["salvage_near_pass"], 2)

    def test_near_pass_tried_expression_can_retry_new_settings_payload(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        expr = "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),market)"
        payload_key = MODULE._payload_fingerprint(expr, {"decay": 4})
        screener = MODULE.PreSimulationScreener(
            cfg,
            tried_exact={expr},
            tried_payload_keys={payload_key},
            near_pass_expressions={expr},
            failed_cluster={},
            history_pools=MODULE.HistorySimilarityPools(),
            top_field_lookup=lambda _e: None,
            tried_metrics={},
        )

        kept, reasons, _ = screener.screen(
            [
                {
                    "regular": expr,
                    "settings": {"decay": 6},
                    "meta": {"family": "near_pass_variant", "source": "near_pass"},
                }
            ]
        )

        self.assertEqual(len(kept), 1)
        self.assertNotIn("already_simulated_expr", reasons)

    def test_salvage_topup_allows_same_expression_with_different_settings(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.diversity_mode = "pass_rate"
        screener = MODULE.PreSimulationScreener(
            cfg,
            tried_exact=set(),
            tried_payload_keys=set(),
            near_pass_expressions=set(),
            failed_cluster={},
            history_pools=MODULE.HistorySimilarityPools(),
            top_field_lookup=lambda _e: None,
            tried_metrics={},
        )
        expr = "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),market)"
        coarse = [
            {
                "regular": expr,
                "settings": {"decay": decay},
                "meta": {
                    "family": "near_pass_variant",
                    "source": "near_pass",
                    "candidate_score": 1.0,
                },
            }
            for decay in (4, 6, 8)
        ]

        topup, stats = screener.select_salvage_topup(
            coarse,
            already=[],
            need=3,
            target_n=3,
            history_similarity_cap=0.88,
        )

        self.assertEqual(len(topup), 3)
        self.assertEqual(stats["salvage_near_pass"], 3)

    def test_batch_diagnostics_repairs_legacy_header(self) -> None:
        import csv

        legacy_fields = [
            "utc_iso",
            "pipeline_version",
            "target_simulate_batch",
            "min_simulate_batch",
            "candidates",
            "raw_payloads",
            "prescreen_kept",
            "selected",
            "novelty_strictness",
            "prescreen_similarity",
            "intrabatch_similarity",
            "kind",
            "name",
            "count",
            "sample",
        ]
        expected_fields = [
            "utc_iso",
            "pipeline_version",
            "preset",
            "target_simulate_batch",
            "min_simulate_batch",
            "candidates",
            "raw_payloads",
            "prescreen_coarse_kept",
            "prescreen_kept",
            "selected",
            "novelty_strictness",
            "prescreen_similarity",
            "intrabatch_similarity",
            "kind",
            "name",
            "count",
            "sample",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            diag = Path(tmp) / "diag.csv"
            with diag.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=legacy_fields)
                writer.writeheader()
            cfg = MODULE.PipelineConfig(
                username="u",
                password="p",
                batch_diagnostics_filename=str(diag),
            )
            pipeline = MODULE.WorldQuantAlphaPipeline(cfg)

            pipeline._write_batch_diagnostics(
                candidates_count=1,
                raw_payloads_count=1,
                kept_count=1,
                selected_count=1,
                reasons=MODULE.Counter(),
                family_pre=MODULE.Counter(),
                family_post=MODULE.Counter(),
                family_selected=MODULE.Counter(),
                samples=[],
                allocator_stats={"near_pass_fine_goal": 1},
                coarse_kept_count=1,
            )

            with diag.open("r", newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                header = next(reader)
                row = next(reader)

        self.assertEqual(header, expected_fields)
        self.assertEqual(len(row), len(expected_fields))

    def test_poll_only_cleanup_prioritizes_metric_pass_rows(self) -> None:
        rows = [
            {"alpha_id": "weak", "Sharpe": "0.22", "Fitness": "0.10"},
            {
                "alpha_id": "strong",
                "Sharpe": "1.40",
                "Fitness": "1.08",
                "metric_gate_pass": "True",
                "platform_non_self_pass": "True",
            },
            {
                "alpha_id": "nonself",
                "Sharpe": "1.20",
                "Fitness": "0.95",
                "platform_non_self_pass": "True",
            },
        ]

        ordered = sorted(
            rows, key=MODULE.WorldQuantAlphaPipeline._feedback_cleanup_priority_key
        )

        self.assertEqual(
            [r["alpha_id"] for r in ordered], ["strong", "nonself", "weak"]
        )

    def test_self_correlation_pending_is_not_hard_fail(self) -> None:
        detail = {
            "is": {
                "checks": [
                    {"name": "LOW_SHARPE", "result": "PASS"},
                    {"name": "LOW_FITNESS", "result": "PASS"},
                    {"name": "SELF_CORRELATION", "result": "PENDING"},
                ]
            }
        }

        self.assertTrue(MODULE._self_correlation_pending(detail))
        self.assertTrue(MODULE._non_self_checks_all_pass(detail))
        self.assertEqual(MODULE._hard_fail_checks(detail), [])

    def test_metric_pass_self_correlation_pending_stays_in_recheck_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = MODULE.PipelineConfig(
                username="u",
                password="p",
                hopeful_queue_filename=str(Path(tmp) / "hopeful.jsonl"),
                submission_results_filename=str(Path(tmp) / "submissions.jsonl"),
            )
            pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
            row = {
                "alpha_id": "alpha_pending",
                "status": "needs_recheck",
                "expression": "group_neutralize(ts_zscore(sales/cap,126),market)",
                "metrics": {"sharpe": 1.41, "fitness": 1.08},
                "meta": {"family": "near_pass_variant"},
            }
            detail = {
                "is": {
                    "sharpe": 1.41,
                    "fitness": 1.08,
                    "checks": [
                        {"name": "LOW_SHARPE", "result": "PASS"},
                        {"name": "LOW_FITNESS", "result": "PASS"},
                        {"name": "SELF_CORRELATION", "result": "PENDING"},
                    ],
                }
            }

            pipeline._append_hopeful_recheck_snapshot(
                row,
                detail,
                check_passed=None,
                note="metric_pass:self_correlation_pending",
                queue_status="needs_recheck",
            )

            [latest] = pipeline.queue.load()
            self.assertEqual(latest["status"], "needs_recheck")
            self.assertEqual(
                latest["check_note"], "metric_pass:self_correlation_pending"
            )

    def test_metric_pass_check_timeout_enters_recheck_queue_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = MODULE.PipelineConfig(
                username="u",
                password="p",
                hopeful_queue_filename=str(Path(tmp) / "hopeful.jsonl"),
                submission_results_filename=str(Path(tmp) / "submissions.jsonl"),
            )
            pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
            pl = payload("near_pass_variant")
            detail = {
                "is": {
                    "sharpe": 1.36,
                    "fitness": 1.04,
                    "turnover": 0.22,
                    "returns": 0.03,
                    "drawdown": 0.12,
                    "margin": 0.01,
                }
            }

            status, entry = pipeline.queue_decision(
                pl,
                "alpha_timeout",
                detail,
                check_passed=None,
                check_note="check_timeout:pending",
            )

            self.assertEqual(status, "needs_recheck")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["status"], "needs_recheck")
            self.assertEqual(pipeline.queue.load()[0]["status"], "needs_recheck")

    def test_run_recheck_queue_respects_cooldown_for_recent_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = MODULE.PipelineConfig(
                username="u",
                password="p",
                hopeful_queue_filename=str(Path(tmp) / "hopeful.jsonl"),
                submission_results_filename=str(Path(tmp) / "submissions.jsonl"),
            )
            cfg.queue_recheck_seconds = 3 * 3600
            pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
            pipeline.queue.append(
                {
                    "alpha_id": "alpha_recent",
                    "status": "needs_recheck",
                    "queued_at": MODULE._utc(),
                    "expression": "group_neutralize(ts_zscore(sales/cap,126),market)",
                    "settings": {},
                    "meta": {"family": "near_pass_variant"},
                    "metrics": {"sharpe": 1.7, "fitness": 1.2},
                }
            )

            calls: list[str] = []

            def fail_if_called(*_args, **_kwargs):
                calls.append("called")
                raise AssertionError(
                    "check_alpha should not run while cooldown is active"
                )

            pipeline.authenticate = lambda: None
            pipeline.check_alpha = fail_if_called

            df = pipeline.run_recheck_queue(do_auth=False)

            self.assertTrue(df.empty)
            self.assertEqual(calls, [])

    def test_run_recheck_queue_bypass_cooldown_for_standalone_recheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = MODULE.PipelineConfig(
                username="u",
                password="p",
                hopeful_queue_filename=str(Path(tmp) / "hopeful.jsonl"),
                submission_results_filename=str(Path(tmp) / "submissions.jsonl"),
            )
            cfg.queue_recheck_seconds = 3 * 3600
            pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
            pipeline.queue.append(
                {
                    "alpha_id": "alpha_recent",
                    "status": "needs_recheck",
                    "queued_at": MODULE._utc(),
                    "expression": "group_neutralize(ts_zscore(sales/cap,126),market)",
                    "settings": {},
                    "meta": {"family": "near_pass_variant"},
                    "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.2},
                    "checks": [],
                }
            )

            pipeline.authenticate = lambda: None
            pipeline.check_alpha = lambda *_args, **_kwargs: (
                None,
                None,
                "check_timeout:pending",
            )

            df = pipeline.run_recheck_queue(do_auth=False, bypass_cooldown=True)

            self.assertEqual(len(df), 1)
            self.assertEqual(df.iloc[0]["alpha_id"], "alpha_recent")

    def test_metric_pass_timeout_without_platform_checks_keeps_sim_metrics_for_recheck(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = MODULE.PipelineConfig(
                username="u",
                password="p",
                hopeful_queue_filename=str(Path(tmp) / "hopeful.jsonl"),
                submission_results_filename=str(Path(tmp) / "submissions.jsonl"),
            )
            pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
            pl = payload("near_pass_variant", source="near_pass")
            result = {
                "id": "sim_timeout",
                "alpha": "alpha_timeout",
                "is": {
                    "sharpe": 2.13,
                    "fitness": 1.68,
                    "turnover": 0.1687,
                    "returns": 0.12,
                    "drawdown": 0.06,
                    "margin": 0.002,
                },
            }

            status, entry = pipeline.queue_decision(
                pl,
                "alpha_timeout",
                result,
                check_passed=None,
                check_note="check_timeout:pending",
            )

            self.assertEqual(status, "needs_recheck")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["metrics"]["sharpe"], 2.13)
            self.assertEqual(entry["metrics"]["fitness"], 1.68)
            self.assertEqual(entry["check_note"], "check_timeout:pending")
            self.assertEqual(pipeline.queue.load()[0]["status"], "needs_recheck")

    def test_incompatible_unit_result_does_not_enter_recheck_queue(self) -> None:
        import csv

        with tempfile.TemporaryDirectory() as tmp:
            feedback_path = Path(tmp) / "feedback.csv"
            cfg = MODULE.PipelineConfig(
                username="u",
                password="p",
                hopeful_queue_filename=str(Path(tmp) / "hopeful.jsonl"),
                submission_results_filename=str(Path(tmp) / "submissions.jsonl"),
                feedback_ledger_filename=str(feedback_path),
            )
            pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
            row = {k: "" for k in MODULE.FEEDBACK_FIELDS}
            row.update(
                {
                    "alpha_id": "alpha_timeout",
                    "queue_status": "poll_only:not_checked",
                    "Sharpe": "2.13",
                    "Fitness": "1.68",
                    "Turnover": "0.1687",
                    "Returns": "0.12",
                    "Drawdown": "0.06",
                    "Margin": "0.002",
                }
            )
            with feedback_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=list(MODULE.FEEDBACK_FIELDS))
                writer.writeheader()
                writer.writerow(row)
            warning_result = {
                "id": "sim_warning",
                "alpha": "alpha_timeout",
                "status": "WARNING",
                "message": "Incompatible unit",
            }

            status, entry = pipeline.queue_decision(
                payload("near_pass_variant", source="near_pass"),
                "alpha_timeout",
                warning_result,
                check_passed=None,
                check_note="check_timeout:pending",
            )

            self.assertEqual(status, "not_queued:incompatible_unit")
            self.assertIsNone(entry)
            self.assertEqual(pipeline.queue.load(), [])

    def test_feedback_timeout_metric_pass_marks_platform_check_pending(self) -> None:
        fields = MODULE._feedback_analysis_fields(
            {"sharpe": 2.13, "fitness": 1.68, "turnover": 0.1687},
            {},
            check_passed=None,
            check_note="check_timeout:pending",
            queue_status="needs_recheck",
        )

        self.assertEqual(fields["metric_gate_pass"], True)
        self.assertEqual(fields["blocked_reason"], "self_or_platform_check_pending")

    def test_feedback_analysis_marks_incompatible_unit_separately(self) -> None:
        fields = MODULE._feedback_analysis_fields(
            {"sharpe": 2.13, "fitness": 1.68, "turnover": 0.1687},
            {"status": "WARNING", "message": "Incompatible unit"},
            check_passed=None,
            check_note="check_timeout:pending",
            queue_status="not_queued:incompatible_unit",
        )

        self.assertEqual(fields["metric_gate_pass"], True)
        self.assertEqual(fields["submission_candidate"], False)
        self.assertEqual(fields["blocked_reason"], "incompatible_unit")
        self.assertEqual(fields["platform_gate_reason"], "Incompatible unit")

    def test_async_metric_snapshot_merges_feedback_metrics_when_check_json_is_empty(
        self,
    ) -> None:
        from alpha_mining.common import merge_feedback_metrics_snapshot

        class Pipeline:
            def _feedback_metrics_for_alpha(self, alpha_id):
                self.seen = alpha_id
                return {
                    "sharpe": 1.95,
                    "fitness": 1.69,
                    "turnover": 0.1026,
                    "returns": 0.08,
                    "drawdown": 0.05,
                    "margin": 0.001,
                }

        pipeline = Pipeline()
        merged = {"id": "sim_warning", "alpha": "alpha_timeout", "status": "WARNING"}

        out = merge_feedback_metrics_snapshot(pipeline, "alpha_timeout", merged)

        self.assertEqual(pipeline.seen, "alpha_timeout")
        self.assertEqual(out["is"]["sharpe"], 1.95)
        self.assertEqual(out["is"]["fitness"], 1.69)
        self.assertEqual(out["is"]["turnover"], 0.1026)

    def test_initial_simulate_check_wait_prioritizes_metric_pass_candidates(
        self,
    ) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.simulate_check_poll_seconds = 60.0
        cfg.simulate_quality_check_poll_seconds = 600.0
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        strong = {
            "is": {
                "sharpe": 1.38,
                "fitness": 1.07,
                "turnover": 0.18,
                "returns": 0.03,
                "drawdown": 0.10,
                "margin": 0.01,
            }
        }
        weak = {"is": {"sharpe": 0.42, "fitness": 0.18, "turnover": 0.30}}
        negative = {"is": {"sharpe": -0.31, "fitness": -0.05, "turnover": 0.22}}

        self.assertEqual(pipeline._initial_simulate_check_wait(strong), 600.0)
        self.assertEqual(pipeline._initial_simulate_check_wait(weak), 0.0)
        self.assertEqual(pipeline._initial_simulate_check_wait(negative), 0.0)

    def test_metric_gate_uses_strict_simulate_only_candidate_thresholds(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)

        self.assertEqual(
            pipeline._metric_gate({"sharpe": 1.25, "fitness": 1.01, "turnover": 0.10})[
                0
            ],
            False,
        )
        self.assertEqual(
            pipeline._metric_gate({"sharpe": 1.26, "fitness": 1.0, "turnover": 0.10})[
                0
            ],
            False,
        )
        self.assertEqual(
            pipeline._metric_gate({"sharpe": 1.26, "fitness": 1.01, "turnover": 0.70})[
                0
            ],
            False,
        )
        self.assertEqual(
            pipeline._metric_gate({"sharpe": 1.26, "fitness": 1.01, "turnover": 0.01})[
                0
            ],
            True,
        )

    def test_feedback_fields_block_pending_self_correlation_candidate_and_platform_pass(
        self,
    ) -> None:
        detail = {
            "is": {
                "checks": [
                    {"name": "LOW_SHARPE", "result": "PASS"},
                    {"name": "LOW_FITNESS", "result": "PASS"},
                    {
                        "name": "LOW_TURNOVER",
                        "result": "PASS",
                        "limit": 0.01,
                        "value": 0.12,
                    },
                    {
                        "name": "HIGH_TURNOVER",
                        "result": "PASS",
                        "limit": 0.70,
                        "value": 0.12,
                    },
                    {"name": "LOW_SUB_UNIVERSE_SHARPE", "result": "PASS"},
                    {"name": "CONCENTRATED_WEIGHT", "result": "PASS"},
                    {"name": "SELF_CORRELATION", "result": "PENDING"},
                ],
            }
        }
        metrics = {"sharpe": 1.31, "fitness": 1.08, "turnover": 0.12}

        fields = MODULE._feedback_analysis_fields(
            metrics,
            detail,
            check_passed=None,
            check_note="metric_pass:self_correlation_pending",
            queue_status="needs_recheck",
        )

        self.assertEqual(fields["metric_gate_pass"], True)
        self.assertEqual(fields["platform_non_self_pass"], True)
        self.assertEqual(fields["self_correlation_status"], "PENDING")
        self.assertEqual(fields["submission_candidate"], False)
        self.assertEqual(fields["platform_pass_evidence"], False)
        self.assertIn("Sharpe 1.31 > 1.25", fields["pass_proxy_reason"])
        self.assertEqual(fields["blocked_reason"], "self_correlation_pending")

    def test_feedback_fields_block_self_correlation_fail(self) -> None:
        detail = {
            "is": {
                "checks": [
                    {"name": "LOW_SHARPE", "result": "PASS"},
                    {"name": "LOW_FITNESS", "result": "PASS"},
                    {"name": "LOW_TURNOVER", "result": "PASS"},
                    {"name": "HIGH_TURNOVER", "result": "PASS"},
                    {"name": "SELF_CORRELATION", "result": "FAIL"},
                ],
            }
        }
        metrics = {"sharpe": 1.8, "fitness": 1.4, "turnover": 0.22}

        fields = MODULE._feedback_analysis_fields(metrics, detail, check_passed=False)

        self.assertEqual(fields["metric_gate_pass"], True)
        self.assertEqual(fields["self_correlation_status"], "FAIL")
        self.assertEqual(fields["submission_candidate"], False)
        self.assertEqual(fields["platform_pass_evidence"], False)
        self.assertEqual(fields["blocked_reason"], "SELF_CORRELATION")

    def test_feedback_learning_summary_persists_strategy_iteration_rates(self) -> None:
        import csv
        import json

        with tempfile.TemporaryDirectory() as tmp:
            feedback_path = Path(tmp) / "feedback.csv"
            summary_csv = Path(tmp) / "summary.csv"
            summary_json = Path(tmp) / "summary.json"
            row = {k: "" for k in MODULE.FEEDBACK_FIELDS}
            row.update(
                {
                    "expression": "group_neutralize(ts_zscore(fnd6_eps/cap,126),subindustry)",
                    "family": "near_pass_variant",
                    "source": "near_pass",
                    "Neutralization": "MARKET",
                    "Decay": "4",
                    "Truncation": "0.05",
                    "Sharpe": "1.31",
                    "Fitness": "1.08",
                    "Turnover": "0.12",
                    "metric_gate_pass": "True",
                    "self_correlation_status": "PENDING",
                    "blocked_reason": "self_correlation_pending",
                    "platform_check_json": json.dumps(
                        {
                            "is": {
                                "checks": [
                                    {
                                        "name": "LOW_SUB_UNIVERSE_SHARPE",
                                        "result": "PASS",
                                    },
                                    {"name": "CONCENTRATED_WEIGHT", "result": "PASS"},
                                    {"name": "SELF_CORRELATION", "result": "PENDING"},
                                    {"name": "PROD_CORRELATION", "result": "FAIL"},
                                ]
                            }
                        }
                    ),
                }
            )
            with feedback_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=list(MODULE.FEEDBACK_FIELDS))
                writer.writeheader()
                writer.writerow(row)

            cfg = MODULE.PipelineConfig(
                username="u",
                password="p",
                feedback_ledger_filename=str(feedback_path),
                feedback_learning_summary_csv=str(summary_csv),
                feedback_learning_summary_json=str(summary_json),
                feedback_check_distribution_csv=str(Path(tmp) / "checks.csv"),
            )
            pipeline = MODULE.WorldQuantAlphaPipeline(cfg)

            df = pipeline.write_feedback_learning_summary()

            self.assertTrue(summary_csv.is_file())
            self.assertTrue(summary_json.is_file())
            self.assertTrue((Path(tmp) / "checks.csv").is_file())
            self.assertEqual(len(df), 1)
            self.assertEqual(float(df.iloc[0]["metric_gate_pass_rate"]), 1.0)
            self.assertEqual(float(df.iloc[0]["self_correlation_pending_rate"]), 1.0)
            self.assertEqual(float(df.iloc[0]["prod_correlation_fail_rate"]), 1.0)
            self.assertEqual(
                df.iloc[0]["top_blocked_reason"], "self_correlation_pending"
            )

    def test_batch_guard_warns_near_pass_zero_but_allows_quality_batches(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.min_simulate_batch = 300
        cfg.min_near_pass_batch_share = 0.52
        cfg.max_arch_explore_batch_share = 0.50
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        selected = [
            payload("alpha_models_template", source="Alpha Models.csv")
            for _ in range(25)
        ] + [payload("pass_fundamental_ts") for _ in range(25)]
        raw_family = MODULE.Counter({"near_pass_variant": 627, "arch_vol_scaled": 128})

        ok, violations = pipeline._batch_guard_allows_simulation(selected, raw_family)

        self.assertTrue(ok, violations)
        self.assertIn("near_pass_share_below_min", pipeline._last_batch_guard_warnings)
        self.assertIn("near_pass_dropped_to_zero", pipeline._last_batch_guard_warnings)

    def test_batch_guard_still_blocks_arch_heavy_no_quality_batches(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.min_simulate_batch = 300
        cfg.max_arch_explore_batch_share = 0.03
        cfg.batch_guard_allow_underfill = True
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        # Upstream had near_pass but allocator dropped them — must still block.
        selected = [payload("arch_vol_scaled", source="proven") for _ in range(84)]
        raw_family = MODULE.Counter({"near_pass_variant": 40, "arch_vol_scaled": 128})

        ok, violations = pipeline._batch_guard_allows_simulation(selected, raw_family)

        self.assertFalse(ok)
        self.assertIn("low_yield_arch_share_above_cap", violations)
        self.assertIn("quality_family_share_below_min", violations)
        self.assertIn("no_quality_family_selected", violations)

    def test_batch_guard_underfill_allows_arch_pilot_when_raw_has_no_quality(
        self,
    ) -> None:
        """Empty quality pool upstream must not deadlock the loop forever."""
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.diversity_mode = "quality_diverse"
        cfg.min_simulate_batch = 60
        cfg.max_arch_explore_batch_share = 0.15
        cfg.min_quality_batch_share = 0.22
        cfg.batch_guard_allow_underfill = True
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        selected = [payload("arch_analyst", source="proven") for _ in range(8)]
        raw_family = MODULE.Counter(
            {"arch_pv_slow": 15, "arch_spread": 12, "arch_analyst": 8}
        )

        ok, violations = pipeline._batch_guard_allows_simulation(selected, raw_family)

        self.assertTrue(ok, violations)
        self.assertEqual(violations, [])
        self.assertIn("no_quality_family_selected", pipeline._last_batch_guard_warnings)
        self.assertIn(
            "quality_family_share_below_min", pipeline._last_batch_guard_warnings
        )

    def test_batch_guard_passes_balanced_quality_batch(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.min_simulate_batch = 300
        cfg.min_near_pass_batch_share = 0.52
        cfg.max_arch_explore_batch_share = 0.03
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        selected = (
            [payload("near_pass_variant", source="near_pass") for _ in range(180)]
            + [payload("pass_fundamental_ts") for _ in range(70)]
            + [
                payload("alpha_models_template", source="Alpha Models.csv")
                for _ in range(40)
            ]
            + [payload("arch_vol_scaled", source="proven") for _ in range(9)]
        )
        raw_family = MODULE.Counter({"near_pass_variant": 220, "arch_vol_scaled": 30})

        ok, violations = pipeline._batch_guard_allows_simulation(selected, raw_family)

        self.assertTrue(ok, violations)

    def test_batch_guard_passes_underfilled_near_pass_majority(self) -> None:
        """Quality pool underfill must not deadlock simulate when near_pass dominates actual batch."""
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.min_simulate_batch = 300
        cfg.min_near_pass_batch_share = 0.52
        cfg.max_arch_explore_batch_share = 0.03
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        selected = (
            [payload("near_pass_variant", source="near_pass") for _ in range(147)]
            + [
                payload("alpha_models_template", source="Alpha Models.csv")
                for _ in range(15)
            ]
            + [payload("arch_hybrid_z_pv", source="proven") for _ in range(2)]
        )
        raw_family = MODULE.Counter(
            {"near_pass_variant": 162, "arch_regime_shift": 128}
        )

        ok, violations = pipeline._batch_guard_allows_simulation(selected, raw_family)

        self.assertTrue(ok, violations)

    def test_default_diversity_mode_is_quality_diverse(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")

        self.assertEqual(cfg.diversity_mode, "quality_diverse")
        self.assertFalse(cfg.alpha_models_enabled)
        self.assertEqual(cfg.alpha_models_batch_quota, 0)
        self.assertEqual(cfg.min_template_batch_share, 0.0)

    def test_low_yield_bucket_key_uses_family_source_window_and_settings(self) -> None:
        pl = {
            "regular": "group_neutralize(ts_zscore(fnd6_sales/cap,126),market)",
            "settings": {"neutralization": "MARKET", "decay": 6, "truncation": 0.05},
            "meta": {"family": "arch_vol_scaled", "source": "proven"},
        }

        key = MODULE._low_yield_bucket_key_for_payload(pl)

        self.assertEqual(
            key, ("arch_vol_scaled", "proven", "126", "MARKET", "6", "0.05")
        )

    def test_prescreener_blocks_low_yield_bucket_after_cap(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        screener = MODULE.PreSimulationScreener(
            cfg,
            tried_exact=set(),
            tried_payload_keys=set(),
            near_pass_expressions=set(),
            failed_cluster={},
            history_pools=MODULE.HistorySimilarityPools(),
            top_field_lookup=lambda _e: None,
            tried_metrics={},
        )
        screener.family_low_yield_buckets = {
            ("arch_vol_scaled", "proven", "126", "MARKET", "6", "0.05"): {
                "count": 50,
                "metric_gate_pass_rate": 0.02,
                "top_blocked_reason": "missing_core_metrics",
            }
        }
        payloads = []
        for i in range(4):
            payloads.append(
                {
                    "regular": f"group_neutralize(ts_zscore(fnd6_sales_{i}/cap,126),market)",
                    "settings": {
                        "neutralization": "MARKET",
                        "decay": 6,
                        "truncation": 0.05,
                    },
                    "meta": {
                        "family": "arch_vol_scaled",
                        "source": "proven",
                        "candidate_score": 1.0,
                    },
                }
            )

        kept, reasons, _ = screener.select_diverse_for_simulate(payloads, 4)

        self.assertEqual(len(kept), 2)
        self.assertGreaterEqual(reasons["fine_low_yield_bucket_cap"], 1)

    def test_initial_simulate_check_wait_skips_missing_core_metrics(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)

        wait = pipeline._initial_simulate_check_wait(
            {"is": {"sharpe": 1.4, "fitness": 1.1}}
        )

        self.assertEqual(wait, 0.0)

    def test_initial_simulate_check_wait_skips_metric_fail(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)

        wait = pipeline._initial_simulate_check_wait(
            {"is": {"sharpe": 0.9, "fitness": 1.1, "turnover": 0.2}}
        )

        self.assertEqual(wait, 0.0)

    def test_initial_simulate_check_wait_keeps_short_wait_for_metric_pass(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)

        wait = pipeline._initial_simulate_check_wait(
            {"is": {"sharpe": 1.4, "fitness": 1.1, "turnover": 0.2}}
        )

        self.assertEqual(wait, 300.0)

    def test_allocator_does_not_shrink_quality_pool_to_single_digits(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.min_simulate_batch = 300
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        payloads = (
            [payload("pass_fundamental_ts") for _ in range(90)]
            + [payload("near_pass_variant", source="near_pass") for _ in range(40)]
            + [
                payload("alpha_models_template", source="Alpha Models.csv")
                for _ in range(40)
            ]
            + [payload("arch_hybrid_delta_pv", source="proven") for _ in range(20)]
        )

        selected, stats = pipeline._allocate_payload_budget(payloads, 300)

        self.assertGreaterEqual(len(selected), 80)
        self.assertGreaterEqual(stats["quality_family_selected"], 80)

    def test_allocator_keeps_floor_when_fine_pool_has_enough_quality_payloads(
        self,
    ) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.min_simulate_batch = 300
        cfg.near_pass_batch_quota = 180
        cfg.min_near_pass_batch_share = 0.38
        cfg.alpha_models_batch_quota = 0
        cfg.max_arch_explore_batch_share = 0.05
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        payloads = (
            [payload("near_pass_variant", source="near_pass") for _ in range(147)]
            + [
                payload("alpha_models_template", source="Alpha Models.csv")
                for _ in range(15)
            ]
            + [payload("pass_fundamental_ts", source="pass_first") for _ in range(80)]
            + [
                payload("pass_fundamental_delta", source="pass_first")
                for _ in range(220)
            ]
            + [payload("arch_hybrid_delta_pv", source="proven") for _ in range(70)]
        )

        selected, stats = pipeline._allocate_payload_budget(payloads, 502)

        self.assertGreaterEqual(len(selected), 300)
        self.assertGreaterEqual(stats["quality_family_selected"], 300)
        self.assertEqual(stats["selected_total"], len(selected))

    def test_allocator_tops_up_underfilled_near_pass_batch_without_arch_flood(
        self,
    ) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.min_simulate_batch = 300
        cfg.near_pass_batch_quota = 180
        cfg.alpha_models_batch_quota = 24
        cfg.max_arch_explore_batch_share = 0.05
        pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
        payloads = (
            [payload("near_pass_variant", source="near_pass") for _ in range(147)]
            + [
                payload("alpha_models_template", source="Alpha Models.csv")
                for _ in range(15)
            ]
            + [payload("arch_delta_ts_rank", source="proven") for _ in range(8)]
            + [
                payload("pass_fundamental_delta", source="pass_first")
                for _ in range(260)
            ]
        )

        selected, stats = pipeline._allocate_payload_budget(payloads, 430)
        families = [p["meta"]["family"] for p in selected]

        self.assertGreaterEqual(len(selected), 300)
        self.assertLessEqual(families.count("arch_delta_ts_rank"), 15)
        self.assertGreaterEqual(families.count("pass_fundamental_delta"), 130)
        self.assertEqual(stats["selected_total"], len(selected))

    def test_cleanup_poll_only_has_wall_budget(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")

        self.assertLessEqual(cfg.cleanup_poll_only_max_per_run, 20)
        self.assertLessEqual(cfg.cleanup_poll_only_wall_budget_seconds, 300.0)

    def test_fetch_datafields_skips_429_dataset_and_keeps_later_dataset(self) -> None:
        class FakeResponse:
            def __init__(self, data):
                self._data = data

            def json(self):
                return self._data

        class DatafieldPipeline(MODULE.WorldQuantAlphaPipeline):
            def _dataset_ids(self):
                return ["rate_limited_ds", "good_ds"]

            def _retry(
                self,
                method,
                url,
                *,
                params=None,
                json_body=None,
                timeout=None,
                max_attempts=None,
            ):
                if params and params.get("dataset.id") == "rate_limited_ds":
                    resp = MODULE.requests.Response()
                    resp.status_code = 429
                    err = MODULE.requests.HTTPError("429")
                    err.response = resp
                    raise err
                return FakeResponse(
                    {
                        "count": 1,
                        "results": [
                            {
                                "id": "fnd6_sales",
                                "type": "MATRIX",
                                "coverage": 1.0,
                                "dateCoverage": 1.0,
                            }
                        ],
                    }
                )

        cfg = MODULE.PipelineConfig(username="u", password="p")
        cfg.submit_429_min_sleep = 0.0
        cfg.enable_fields_disk_cache = False
        pipeline = DatafieldPipeline(cfg)
        pipeline._dynamic_submit_sleep = 0.0

        df = pipeline.fetch_datafields()

        self.assertEqual(list(df["id"]), ["fnd6_sales"])
        self.assertEqual(list(df["_ds"]), ["good_ds"])

    def test_near_pass_amplifier_does_not_sign_flip(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        catalog = MODULE.FieldCatalog(
            df=None,
            ids={"vwap", "close", "volume", "adv20"},
            by_ds={},
            fund=[],
            analyst=[],
            model=[],
            sent=[],
            pv=["vwap", "close", "volume", "adv20"],
            other=[],
        )
        validator = MODULE.PreflightValidator(catalog)
        amplifier = MODULE.NearPassAmplifier(cfg, catalog, validator)
        seed = "group_neutralize(ts_rank(vwap/close, 126)-0.5, sector)"

        amplified = amplifier.amplify(
            [{"expression": seed, "sharpe": 1.15}],
            tried_exact=set(),
        )

        for c in amplified:
            self.assertFalse(
                c.expression.startswith("-("),
                f"sign-flip variant must not be emitted: {c.expression[:80]}",
            )

    def test_near_pass_pure_vwap_close_leg_is_toxic(self) -> None:
        expr = "group_neutralize(ts_rank(vwap/close, 126)-0.5, sector)"

        self.assertTrue(MODULE._has_toxic_near_pass_price_leg(expr))

    def test_batch_diagnostics_include_family_drop_and_guard_rows(self) -> None:
        import csv

        with tempfile.TemporaryDirectory() as tmp:
            diag = Path(tmp) / "diag.csv"
            cfg = MODULE.PipelineConfig(
                username="u",
                password="p",
                batch_diagnostics_filename=str(diag),
            )
            pipeline = MODULE.WorldQuantAlphaPipeline(cfg)

            pipeline._write_batch_diagnostics(
                candidates_count=2,
                raw_payloads_count=2,
                kept_count=1,
                selected_count=1,
                reasons=MODULE.Counter({"already_simulated_payload": 1}),
                family_pre=MODULE.Counter(
                    {"near_pass_variant": 1, "arch_vol_scaled": 1}
                ),
                family_post=MODULE.Counter({"arch_vol_scaled": 1}),
                family_selected=MODULE.Counter({"arch_vol_scaled": 1}),
                samples=[],
                allocator_stats={},
                family_drop_counts=MODULE.Counter(
                    {("near_pass_variant", "already_simulated_payload"): 1}
                ),
                batch_guard=MODULE.Counter({"near_pass_dropped_to_zero": 1}),
            )

            with diag.open("r", newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))

        self.assertIn(
            ("family_drop", "near_pass_variant:already_simulated_payload"),
            {(r["kind"], r["name"]) for r in rows},
        )
        self.assertIn(
            ("batch_guard", "near_pass_dropped_to_zero"),
            {(r["kind"], r["name"]) for r in rows},
        )

    def test_run_full_returns_diagnostic_rows_when_batch_guard_blocks(self) -> None:
        class GuardedPipeline(MODULE.WorldQuantAlphaPipeline):
            def __init__(self, cfg):
                super().__init__(cfg)
                self.simulated = False

            def _cleanup_stale_poll_only(self):
                return None

            def ensure_authenticated(self, *, force=False):
                return True

            def run_recheck_queue(self, **_kwargs):
                return MODULE.pd.DataFrame()

            def generate_candidates(self):
                rows = [
                    MODULE.ExpressionCandidate(
                        f"group_neutralize(ts_zscore(near_field_{i}/cap,126),market)",
                        "near_pass_variant",
                        "near_pass",
                        3.0,
                    )
                    for i in range(20)
                ] + [
                    MODULE.ExpressionCandidate(
                        f"group_neutralize(ts_zscore(arch_field_{i}/cap,126),market)",
                        "arch_vol_scaled",
                        "proven",
                        2.0,
                    )
                    for i in range(50)
                ]
                return rows, None

            def _prescreen_until_target(self, payloads):
                arch_only = [
                    p
                    for p in payloads
                    if str((p.get("meta") or {}).get("family") or "").startswith(
                        "arch_vol_scaled"
                    )
                ]
                self._last_prescreen_coarse_count = len(arch_only)
                self._last_prescreen_diagnostic_stats = {
                    "near_pass_coarse": 0,
                    "near_pass_fine_goal": 0,
                    "near_pass_fine_selected": 0,
                }
                return (
                    arch_only,
                    MODULE.Counter(
                        {"forced_test_drop": len(payloads) - len(arch_only)}
                    ),
                    [],
                )

            def run_batch_simulation(self, _payloads, *, force_sequential=False):
                self.simulated = True
                raise AssertionError("batch guard should skip platform simulation")

        cfg = MODULE.PipelineConfig(
            username="u",
            password="p",
            batch_diagnostics_filename="guard_diag.csv",
        )
        cfg.recheck_skip_prebatch = True
        cfg.recheck_skip_postbatch = True
        with tempfile.TemporaryDirectory() as tmp:
            cfg.batch_diagnostics_filename = str(Path(tmp) / "guard_diag.csv")
            cfg.output_prefix = str(Path(tmp) / "alpha_pipeline")
            pipeline = GuardedPipeline(cfg)

            df = pipeline.run_full()

            self.assertFalse(pipeline.simulated)
            self.assertEqual(set(df["status"]), {"skipped:batch_guard"})
            self.assertIn("near_pass_dropped_to_zero", set(df["reason"]))
            results_path = Path(tmp) / "alpha_pipeline_results.csv"
            self.assertTrue(results_path.is_file())
            persisted = MODULE.pd.read_csv(results_path)
            self.assertEqual(set(persisted["status"]), {"skipped:batch_guard"})

    def test_cli_defaults_skip_prebatch_recheck_unless_explicit(self) -> None:
        cfg = MODULE.PipelineConfig(username="u", password="p")
        self.assertTrue(cfg.recheck_skip_prebatch)

    def test_queue_probe_default_similarity_matches_v50(self) -> None:
        from alpha_mining.scheduler import queue_probe

        row = {
            "alpha_id": "abc",
            "status": "ready",
            "metrics": {"sharpe": 1.3, "fitness": 1.1},
            "similarity_to_winners": 0.75,
            "checks": [{"name": "SELF_CORRELATION", "result": "PASS"}],
        }

        self.assertFalse(queue_probe.is_submit_eligible(row, set()))


if __name__ == "__main__":
    unittest.main()
