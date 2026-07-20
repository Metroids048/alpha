import argparse
import unittest

import run_pipeline_loop as LOOP


def args(**overrides):
    base = {
        "passthrough": [],
        "execute_submit": False,
        "dry_run_submit": False,
        "no_prebatch_recheck": False,
        "inline_recheck": False,
        "recheck_every_cycles": 0,
        "skip_submit": False,
        "submit_drain": False,
        "strategy_preset": "diverse_exploration",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class PipelineLoopSimulateOnlyTests(unittest.TestCase):
    def test_default_loop_skips_prebatch_and_forwards_bounded_postbatch(self) -> None:
        parsed = args()

        self.assertEqual(
            LOOP._build_passthrough_args(parsed),
            [
                "--preset",
                "diverse_exploration",
                "--no-prebatch-recheck",
                "--recheck-postbatch-max-items",
                "4",
                "--recheck-postbatch-wall-budget-seconds",
                "180.0",
            ],
        )
        self.assertFalse(LOOP._should_run_submit_drain(parsed))

    def test_explicit_passthrough_preset_overrides_loop_default(self) -> None:
        pt = LOOP._build_passthrough_args(args(passthrough=["--preset", "mixed"]))

        self.assertEqual(pt.count("--preset"), 1)
        self.assertEqual(pt[pt.index("--preset") + 1], "mixed")

    def test_inline_recheck_restores_old_passthrough_behavior(self) -> None:
        parsed = args(inline_recheck=True)

        pt = LOOP._build_passthrough_args(parsed)
        self.assertNotIn("--no-prebatch-recheck", pt)
        self.assertIn("--recheck-postbatch-max-items", pt)

    def test_no_prebatch_recheck_is_still_forwarded_when_explicit(self) -> None:
        parsed = args(no_prebatch_recheck=True, inline_recheck=True)

        self.assertIn("--no-prebatch-recheck", LOOP._build_passthrough_args(parsed))

    def test_recheck_schedule_runs_only_on_configured_cycles(self) -> None:
        parsed = args(recheck_every_cycles=4)

        self.assertFalse(LOOP._should_run_recheck_cycle(parsed, 1))
        self.assertTrue(LOOP._should_run_recheck_cycle(parsed, 4))
        self.assertFalse(
            LOOP._should_run_recheck_cycle(args(recheck_every_cycles=0), 4)
        )

    def test_submit_drain_requires_explicit_submit_intent(self) -> None:
        self.assertTrue(LOOP._should_run_submit_drain(args(submit_drain=True)))
        self.assertTrue(LOOP._should_run_submit_drain(args(execute_submit=True)))
        self.assertFalse(
            LOOP._should_run_submit_drain(args(submit_drain=True, skip_submit=True))
        )


if __name__ == "__main__":
    unittest.main()
