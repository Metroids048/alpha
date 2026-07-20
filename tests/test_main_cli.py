"""Tests for alpha_mining.main CLI entry point."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _seed_db(path: str) -> None:
    """Create minimal schema needed for CLI tests."""
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    SqliteRunLog(path).initialize_schema()


class TestMainCLIHelp(unittest.TestCase):
    def test_help_exits_zero(self):
        from alpha_mining.main import main

        with self.assertRaises(SystemExit) as ctx:
            main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_no_command_exits_nonzero(self):
        from alpha_mining.main import main

        with self.assertRaises(SystemExit) as ctx:
            main([])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_subcommand_help_exits_zero(self):
        from alpha_mining.main import main

        for cmd in (
            "install-topics",
            "run-evolution",
            "backfill",
            "observe-feedback",
            "pipeline",
        ):
            with self.subTest(cmd=cmd):
                with self.assertRaises(SystemExit) as ctx:
                    main([cmd, "--help"])
                self.assertEqual(ctx.exception.code, 0)


class TestInstallTopicsCommand(unittest.TestCase):
    def test_delegates_to_install_seed_topics(self):
        from alpha_mining.main import main

        with patch(
            "alpha_mining.knowledge.ontology.install_seed_topics", return_value=20
        ) as mock:
            rc = main(["install-topics", "--database", "fake.sqlite"])
        self.assertEqual(rc, 0)
        mock.assert_called_once()
        called_db = mock.call_args[0][0]
        self.assertEqual(Path(called_db).name, "fake.sqlite")

    def test_default_database_name(self):
        from alpha_mining.main import main

        with patch(
            "alpha_mining.knowledge.ontology.install_seed_topics", return_value=5
        ) as mock:
            main(["install-topics"])
        args = mock.call_args[0]
        self.assertEqual(Path(args[0]).name, "research_memory.sqlite")


class TestRunEvolutionCommand(unittest.TestCase):
    def test_runs_evolution_engine(self):
        from alpha_mining.main import main

        engine_mock = MagicMock()
        engine_mock.run.return_value = {"stats_updated": 10, "weights_updated": 10}
        with patch(
            "alpha_mining.scheduler.evolution.EvolutionEngine", return_value=engine_mock
        ):
            rc = main(["run-evolution", "--database", "fake.sqlite"])
        self.assertEqual(rc, 0)
        engine_mock.run.assert_called_once()

    def test_exploration_bonus_forwarded(self):
        from alpha_mining.main import main

        engine_mock = MagicMock()
        engine_mock.run.return_value = {"stats_updated": 0, "weights_updated": 0}
        with patch(
            "alpha_mining.scheduler.evolution.EvolutionEngine", return_value=engine_mock
        ) as cls_mock:
            main(
                [
                    "run-evolution",
                    "--database",
                    "fake.sqlite",
                    "--exploration-bonus",
                    "2.0",
                ]
            )
        _db, kwargs = cls_mock.call_args[0], cls_mock.call_args[1]
        self.assertAlmostEqual(kwargs["exploration_bonus"], 2.0)


class TestBackfillCommand(unittest.TestCase):
    def test_calls_backfill_csvs_with_empty_sources(self):
        from alpha_mining.main import main

        summary_mock = MagicMock()
        summary_mock.by_strategy = {}
        summary_mock.rows_scanned = 0
        summary_mock.rows_imported = 0
        summary_mock.duplicates_skipped = 0
        summary_mock.blank_expressions = 0

        with (
            patch(
                "alpha_mining.storage.backfill_from_csv.backfill_csvs",
                return_value=summary_mock,
            ) as mock_bf,
            patch(
                "alpha_mining.storage.backfill_from_csv.format_summary",
                return_value="ok",
            ),
        ):
            rc = main(["backfill", "--database", "fake.sqlite"])
        self.assertEqual(rc, 0)
        mock_bf.assert_called_once()

    def test_source_files_forwarded(self):
        from alpha_mining.main import main

        summary_mock = MagicMock()
        summary_mock.by_strategy = {}
        summary_mock.rows_scanned = 0
        summary_mock.rows_imported = 0
        summary_mock.duplicates_skipped = 0
        summary_mock.blank_expressions = 0

        with (
            patch(
                "alpha_mining.storage.backfill_from_csv.backfill_csvs",
                return_value=summary_mock,
            ) as mock_bf,
            patch(
                "alpha_mining.storage.backfill_from_csv.format_summary",
                return_value="ok",
            ),
        ):
            rc = main(
                [
                    "backfill",
                    "--database",
                    "fake.sqlite",
                    "--source",
                    "a.csv",
                    "--source",
                    "b.csv",
                ]
            )
        self.assertEqual(rc, 0)
        call_args = mock_bf.call_args
        sources_arg = call_args[0][1]
        self.assertEqual(len(sources_arg), 2)


class TestObserveFeedbackCommand(unittest.TestCase):
    def test_replays_configured_source_without_pipeline_delegation(self):
        from alpha_mining.main import main

        summary = MagicMock(
            rows_scanned=3,
            rows_observed=2,
            descriptions_generated=1,
            failure_category_counts={},
        )
        with patch(
            "alpha_mining.submitter.observation.observe_feedback_csv",
            return_value=summary,
        ) as replay:
            rc = main(
                [
                    "observe-feedback",
                    "--database",
                    "research.sqlite",
                    "--source",
                    "feedback.csv",
                    "--description-limit",
                    "4",
                ]
            )

        self.assertEqual(rc, 0)
        args, kwargs = replay.call_args
        self.assertEqual(Path(args[0].path).name, "research.sqlite")
        self.assertEqual(Path(args[1]).name, "feedback.csv")
        self.assertEqual(kwargs["description_limit"], 4)


class TestPipelineCommand(unittest.TestCase):
    def test_delegates_to_compatibility_subprocess(self):
        from alpha_mining.main import main

        completed = MagicMock(returncode=0)
        with patch("alpha_mining.main.subprocess.run", return_value=completed) as run:
            rc = main(["pipeline"])
        self.assertEqual(rc, 0)
        self.assertTrue(str(run.call_args.args[0][1]).endswith("run_pipeline_cycle.py"))

    def test_propagates_compatibility_exit_code(self):
        from alpha_mining.main import main

        with patch(
            "alpha_mining.main.subprocess.run", return_value=MagicMock(returncode=3)
        ):
            rc = main(["pipeline"])
        self.assertEqual(rc, 3)


class TestMainModuleEntry(unittest.TestCase):
    def test_main_is_importable_without_v34(self):
        """Confirm the old v34 import is gone."""
        import importlib
        import alpha_mining.main as m

        importlib.reload(m)
        src = Path(m.__file__).read_text(encoding="utf-8")
        self.assertNotIn("v34", src)

    def test_module_runnable_as_script(self):
        from alpha_mining.main import main

        self.assertTrue(callable(main))

    def test_package_has_module_entry_point(self):
        import alpha_mining

        entry_point = Path(alpha_mining.__file__).with_name("__main__.py")
        self.assertTrue(entry_point.is_file())


if __name__ == "__main__":
    unittest.main()
