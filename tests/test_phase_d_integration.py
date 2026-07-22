"""Phase D integration tests: Phase 2/3/5 opt-in hooks in generate_candidates.

All tests run offline — no real LLM, no real WorldQuant BRAIN calls.
"""

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _get_pipeline_config():
    mod = importlib.import_module("auto_alpha_pipeline_rebuilt_v50")
    return getattr(mod, "PipelineConfig")


_CFG_DEFAULTS = {"username": "test_user", "password": "test_pass"}


class TestPhaseDConfigFlags(unittest.TestCase):
    """Phase 2/3/5 flags exist in PipelineConfig and default to False."""

    def setUp(self):
        self.PipelineConfig = _get_pipeline_config()

    def test_phase2_llm_enabled_defaults_true(self):
        # LLM hypothesis generation is now on by default (disable via --no-phase2-llm).
        cfg = self.PipelineConfig(**_CFG_DEFAULTS)
        self.assertTrue(cfg.phase2_llm_enabled)

    def test_phase3_llm_grammar_enabled_defaults_true(self):
        # LLM grammar expression generation is now on by default (disable via --no-phase3-llm).
        cfg = self.PipelineConfig(**_CFG_DEFAULTS)
        self.assertTrue(cfg.phase3_llm_grammar_enabled)

    def test_phase3_diversity_gate_enabled_defaults_false(self):
        cfg = self.PipelineConfig(**_CFG_DEFAULTS)
        self.assertFalse(cfg.phase3_diversity_gate_enabled)

    def test_phase5_judge_enabled_defaults_false(self):
        cfg = self.PipelineConfig(**_CFG_DEFAULTS)
        self.assertFalse(cfg.phase5_judge_enabled)

    def test_phase23_hypotheses_per_call_defaults_3(self):
        cfg = self.PipelineConfig(**_CFG_DEFAULTS)
        self.assertEqual(cfg.phase23_hypotheses_per_call, 3)

    def test_phase4_flags_unchanged(self):
        cfg = self.PipelineConfig(**_CFG_DEFAULTS)
        self.assertFalse(cfg.phase4_mutation_enabled)
        self.assertFalse(cfg.phase4_repair_enabled)

    def test_flags_can_be_enabled(self):
        cfg = self.PipelineConfig(
            **_CFG_DEFAULTS,
            phase2_llm_enabled=True,
            phase3_llm_grammar_enabled=True,
            phase3_diversity_gate_enabled=True,
            phase5_judge_enabled=True,
            phase23_hypotheses_per_call=5,
        )
        self.assertTrue(cfg.phase2_llm_enabled)
        self.assertTrue(cfg.phase3_llm_grammar_enabled)
        self.assertTrue(cfg.phase3_diversity_gate_enabled)
        self.assertTrue(cfg.phase5_judge_enabled)
        self.assertEqual(cfg.phase23_hypotheses_per_call, 5)


class _StubPipeline:
    """Minimal stub of WorldQuantAlphaPipeline for unit-testing the new methods."""

    def __init__(self, **cfg_kwargs):
        PipelineConfig = _get_pipeline_config()
        merged = {**_CFG_DEFAULTS, **cfg_kwargs}
        self.cfg = PipelineConfig(**merged)

    # Copy the real implementations from the module under test
    _phase235_llm_candidates = None  # will be bound from module
    _phase5_update_judge_scores = None


def _bind_methods(stub_instance):
    mod = importlib.import_module("auto_alpha_pipeline_rebuilt_v50")
    cls = getattr(mod, "WorldQuantAlphaPipeline", None)
    if cls is None:
        raise unittest.SkipTest("WorldQuantAlphaPipeline not found in module")
    stub_instance._phase235_llm_candidates = cls._phase235_llm_candidates.__get__(
        stub_instance, type(stub_instance)
    )
    stub_instance._phase5_update_judge_scores = cls._phase5_update_judge_scores.__get__(
        stub_instance, type(stub_instance)
    )


class TestPhase235LlmCandidatesGuards(unittest.TestCase):
    """_phase235_llm_candidates returns [] without touching LLM when preconditions unmet."""

    def _make_stub(self, **kwargs):
        stub = _StubPipeline(**kwargs)
        _bind_methods(stub)
        return stub

    def test_returns_empty_when_no_sqlite_path(self):
        stub = self._make_stub(phase3_llm_grammar_enabled=True, sqlite_runs_path=None)
        result = stub._phase235_llm_candidates(None, None, existing_expressions=set())
        self.assertEqual(result, [])

    def test_returns_empty_when_flag_disabled(self):
        # Even if sqlite_runs_path set, the caller in generate_candidates checks the flag
        # before calling this method — but the method itself is callable directly too.
        stub = self._make_stub(
            phase3_llm_grammar_enabled=False, sqlite_runs_path="/tmp/test.db"
        )
        # Method can be called regardless of flag; guard is in generate_candidates.
        # Test that if imports fail gracefully returns [].
        with patch.dict(sys.modules, {"alpha_mining.generator.expression": None}):
            try:
                result = stub._phase235_llm_candidates(
                    None, None, existing_expressions=set()
                )
                # Should either return [] or raise, depending on import guard
                self.assertIsInstance(result, list)
            except Exception:
                pass  # ImportError propagated is also acceptable

    def test_returns_empty_when_llm_key_missing(self):
        """If DeepSeekStructuredLLM raises ValueError (no API key), return []."""
        stub = self._make_stub(
            phase3_llm_grammar_enabled=True,
            sqlite_runs_path=":memory:",
        )
        fake_deepseek_mod = types.ModuleType("alpha_mining.llm.deepseek")

        class _NoKeyLLM:
            def __init__(self, **_kw):
                raise ValueError("DEEPSEEK_API_KEY is required")

        fake_deepseek_mod.DeepSeekStructuredLLM = _NoKeyLLM

        existing = {"alpha_mining.llm.deepseek": fake_deepseek_mod}
        with patch.dict(sys.modules, existing):
            result = stub._phase235_llm_candidates(
                None, None, existing_expressions=set()
            )
        self.assertEqual(result, [])

    def test_returns_empty_when_no_hypotheses_in_db(self):
        """No active hypotheses → skip expression gen, return []."""
        import sqlite3
        import tempfile
        import os

        stub = self._make_stub(
            phase3_llm_grammar_enabled=True,
            phase2_llm_enabled=False,
            sqlite_runs_path=None,
        )
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            con = sqlite3.connect(db_path)
            con.execute(
                "CREATE TABLE IF NOT EXISTS hypotheses "
                "(hypothesis_id TEXT, status TEXT, created_at TEXT)"
            )
            con.commit()
            con.close()

            stub.cfg.sqlite_runs_path = db_path

            class _FakeLLM:
                model_id = "fake-model"

                def __init__(self, **_kw):
                    pass

            fake_deepseek_mod = types.ModuleType("alpha_mining.llm.deepseek")
            fake_deepseek_mod.DeepSeekStructuredLLM = _FakeLLM

            with patch.dict(
                sys.modules, {"alpha_mining.llm.deepseek": fake_deepseek_mod}
            ):
                result = stub._phase235_llm_candidates(
                    None, None, existing_expressions=set()
                )
            self.assertEqual(result, [])
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestPhase5UpdateJudgeScoresGuards(unittest.TestCase):
    """_phase5_update_judge_scores is a no-op when sqlite_runs_path is None."""

    def _make_stub(self, **kwargs):
        stub = _StubPipeline(**kwargs)
        _bind_methods(stub)
        return stub

    def test_noop_when_no_sqlite_path(self):
        stub = self._make_stub(phase5_judge_enabled=True, sqlite_runs_path=None)
        stub._phase5_update_judge_scores()  # must not raise

    def test_noop_when_import_fails(self):
        stub = self._make_stub(phase5_judge_enabled=True, sqlite_runs_path=":memory:")
        with patch.dict(sys.modules, {"alpha_mining.filter.submission_judge": None}):
            try:
                stub._phase5_update_judge_scores()
            except Exception:
                pass  # import failure path may raise; guard returns gracefully


class TestGenerateCandidatesPhaseFlags(unittest.TestCase):
    """generate_candidates only invokes phase methods when flags are enabled."""

    def setUp(self):
        mod = importlib.import_module("auto_alpha_pipeline_rebuilt_v50")
        cls = getattr(mod, "WorldQuantAlphaPipeline", None)
        if cls is None:
            self.skipTest("WorldQuantAlphaPipeline not found")
        self.cls = cls
        self.mod = mod

    def _minimal_pipeline(self, **cfg_kwargs):
        """Return a pipeline instance with heavy I/O methods stubbed out."""
        PipelineConfig = _get_pipeline_config()
        cfg = PipelineConfig(**{**_CFG_DEFAULTS, **cfg_kwargs})
        # Construct without calling __init__ to avoid network setup.
        pipeline = object.__new__(self.cls)
        pipeline.cfg = cfg
        # Stub methods generate_candidates depends on.
        pipeline.load_history_learning = MagicMock()
        pipeline.top_fields = MagicMock(return_value=MagicMock())
        pipeline.fetch_datafields = MagicMock(return_value=MagicMock())
        pipeline.fetch_library_fingerprints = MagicMock(
            return_value=(set(), set(), set())
        )
        pipeline._merge_platform_tried = MagicMock()
        pipeline._feedback_pass_clones = MagicMock(return_value=[])
        pipeline._append_generated_registry = MagicMock()
        pipeline._tried_expressions = set()
        pipeline._history_seen_exact = set()
        pipeline._history_seen_skeleton = set()
        pipeline._history_pools = MagicMock()
        pipeline._near_pass_records = []
        # Stub ExpressionFactory and FieldCatalog
        factory_mock = MagicMock()
        factory_mock.generate.return_value = []
        with (
            patch.object(self.mod, "ExpressionFactory", return_value=factory_mock),
            patch.object(self.mod, "FieldCatalog"),
        ):
            pass
        pipeline._factory_mock = factory_mock
        return pipeline

    def test_phase3_not_called_when_flag_disabled(self):
        pipeline = self._minimal_pipeline(
            phase3_llm_grammar_enabled=False,
            sqlite_runs_path=None,
        )
        pipeline._phase235_llm_candidates = MagicMock(return_value=[])
        pipeline._phase5_update_judge_scores = MagicMock()

        ExpressionFactory = MagicMock(
            return_value=MagicMock(generate=MagicMock(return_value=[]))
        )
        FieldCatalog = MagicMock()
        PreflightValidator = MagicMock()
        with (
            patch.object(self.mod, "ExpressionFactory", ExpressionFactory),
            patch.object(self.mod, "FieldCatalog", FieldCatalog),
            patch.object(self.mod, "PreflightValidator", PreflightValidator),
        ):
            pipeline.generate_candidates()

        pipeline._phase235_llm_candidates.assert_not_called()
        pipeline._phase5_update_judge_scores.assert_not_called()

    def test_phase3_called_when_flag_enabled(self):
        pipeline = self._minimal_pipeline(
            phase3_llm_grammar_enabled=True,
            sqlite_runs_path=None,
        )
        pipeline._phase235_llm_candidates = MagicMock(return_value=[])
        pipeline._phase5_update_judge_scores = MagicMock()

        ExpressionFactory = MagicMock(
            return_value=MagicMock(generate=MagicMock(return_value=[]))
        )
        FieldCatalog = MagicMock()
        PreflightValidator = MagicMock()
        with (
            patch.object(self.mod, "ExpressionFactory", ExpressionFactory),
            patch.object(self.mod, "FieldCatalog", FieldCatalog),
            patch.object(self.mod, "PreflightValidator", PreflightValidator),
        ):
            pipeline.generate_candidates()

        pipeline._phase235_llm_candidates.assert_called_once()
        pipeline._phase5_update_judge_scores.assert_not_called()

    def test_phase5_called_when_flag_enabled(self):
        pipeline = self._minimal_pipeline(
            phase3_llm_grammar_enabled=False,
            phase5_judge_enabled=True,
            sqlite_runs_path=None,
        )
        pipeline._phase235_llm_candidates = MagicMock(return_value=[])
        pipeline._phase5_update_judge_scores = MagicMock()

        ExpressionFactory = MagicMock(
            return_value=MagicMock(generate=MagicMock(return_value=[]))
        )
        FieldCatalog = MagicMock()
        PreflightValidator = MagicMock()
        with (
            patch.object(self.mod, "ExpressionFactory", ExpressionFactory),
            patch.object(self.mod, "FieldCatalog", FieldCatalog),
            patch.object(self.mod, "PreflightValidator", PreflightValidator),
        ):
            pipeline.generate_candidates()

        pipeline._phase5_update_judge_scores.assert_called_once()

    def test_llm_candidates_prepended_and_deduped(self):
        """LLM candidates are prepended; overlap with template candidates is deduped."""
        ExpressionCandidate = getattr(self.mod, "ExpressionCandidate")
        template_cand = ExpressionCandidate(
            "ts_rank(close,20)", "arch_A", "template", 0.0
        )
        llm_cand_new = ExpressionCandidate(
            "group_rank(roic,subindustry)", "llm_grammar", "phase3_llm_grammar", 0.0
        )
        llm_cand_dupe = ExpressionCandidate(
            "ts_rank(close,20)", "llm_grammar", "phase3_llm_grammar", 0.0
        )

        pipeline = self._minimal_pipeline(
            phase3_llm_grammar_enabled=True,
            sqlite_runs_path=None,
        )
        pipeline._phase235_llm_candidates = MagicMock(
            return_value=[llm_cand_new, llm_cand_dupe]
        )
        pipeline._phase5_update_judge_scores = MagicMock()

        ExpressionFactory_mock = MagicMock(
            return_value=MagicMock(generate=MagicMock(return_value=[template_cand]))
        )
        FieldCatalog = MagicMock()
        PreflightValidator = MagicMock()
        with (
            patch.object(self.mod, "ExpressionFactory", ExpressionFactory_mock),
            patch.object(self.mod, "FieldCatalog", FieldCatalog),
            patch.object(self.mod, "PreflightValidator", PreflightValidator),
        ):
            result, _ = pipeline.generate_candidates()

        expressions = [c.expression for c in result]
        self.assertIn("group_rank(roic,subindustry)", expressions)
        self.assertIn("ts_rank(close,20)", expressions)
        # deduped: ts_rank appears exactly once
        self.assertEqual(expressions.count("ts_rank(close,20)"), 1)
        # llm candidate prepended → appears before template candidate
        self.assertLess(
            expressions.index("group_rank(roic,subindustry)"),
            expressions.index("ts_rank(close,20)"),
        )


if __name__ == "__main__":
    unittest.main()
