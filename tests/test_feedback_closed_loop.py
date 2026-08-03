from __future__ import annotations

from alpha_mining.scheduler.arm_metrics import ArmDimensions, ArmState, ResearchArmTracker
from alpha_mining.storage.migrations import migrate


def _arm() -> ArmDimensions:
    return ArmDimensions("momentum", "ds", "price", "trend", "rank", "USA", "TOP3000", "1")


def test_observations_flush_the_real_twenty_sample_window(tmp_path) -> None:
    database = tmp_path / "feedback.sqlite"
    migrate(database)
    tracker = ResearchArmTracker(database)
    arm = _arm()

    for value in range(20):
        tracker.record_observation(
            arm,
            base_pass=value == 19,
            sharpe=value / 10,
            fitness=value / 20,
            near_pass=value == 18,
        )

    stats = tracker.stats(arm)
    assert stats.simulation_count == 20
    assert stats.median_sharpe == 0.95
    assert stats.near_pass_rate == 0.05


def test_arm_freeze_states_change_actual_next_cycle_quota(tmp_path) -> None:
    database = tmp_path / "freeze.sqlite"
    migrate(database)
    tracker = ResearchArmTracker(database)
    arm = _arm()

    yellow = tracker.stats(arm)
    assert yellow.state is ArmState.YELLOW
    assert tracker.next_cycle_quota(arm, normal_quota=3) == 1

    tracker.record_window(
        arm,
        sharpes=[0.5] * 8,
        fitnesses=[0.4] * 8,
        base_passes=[False] * 8,
        near_passes=[False] * 8,
        self_corr_passes=0,
        prod_corr_passes=0,
        final_submits=0,
    )
    assert tracker.stats(arm).state is ArmState.DEAD
    assert tracker.next_cycle_quota(arm, normal_quota=3) == 0

    tracker.record_window(
        arm,
        sharpes=[1.6],
        fitnesses=[1.1],
        base_passes=[True],
        near_passes=[True],
        self_corr_passes=1,
        prod_corr_passes=1,
        final_submits=1,
    )
    assert tracker.stats(arm).state is ArmState.GREEN
    assert tracker.next_cycle_quota(arm, normal_quota=3) == 3
