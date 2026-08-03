from __future__ import annotations

import sqlite3


def test_tune_plan_is_staged_ofat_and_decay_coarse_to_fine() -> None:
    from alpha_mining.simulate.settings_optimizer import SettingsOptimizer, TuneStage

    optimizer = SettingsOptimizer(max_local_trials=4, per_candidate_budget=4)
    plan = optimizer.tune_plan({"decay": 0, "neutralization": "SUBINDUSTRY"}, candidate_id="parent")
    stability = optimizer.stage_trials(TuneStage.STABILITY, plan.base_settings)
    coarse = optimizer.stage_trials(TuneStage.DECAY_COARSE, stability[0].settings)
    fine = optimizer.stage_trials(TuneStage.DECAY_FINE, coarse[0].settings)

    assert plan.stages == (TuneStage.STABILITY, TuneStage.DECAY_COARSE, TuneStage.DECAY_FINE)
    assert len(stability) == 1
    assert [item.settings["decay"] for item in coarse] == [2, 8]
    assert len(fine) == 1
    assert all(len(item.parameter_delta) == 1 for item in [*stability, *coarse, *fine])


def test_tune_reservation_persists_lineage_and_enforces_rolling_budget(tmp_path) -> None:
    from alpha_mining.simulate.settings_optimizer import SettingTrial, SettingsOptimizer

    database = tmp_path / "tune.sqlite"
    trial = SettingTrial("decay_coarse_2", {"decay": 2}, {"decay": 2}, stage="DECAY_COARSE")
    first = SettingsOptimizer.reserve_trial(
        database, candidate_id="parent", parent_candidate_id="parent", trial=trial, rolling_limit=1
    )
    second = SettingsOptimizer.reserve_trial(
        database, candidate_id="parent", parent_candidate_id="parent", trial=trial, rolling_limit=1
    )

    assert first
    assert second is None
    SettingsOptimizer.complete_reserved_trial(
        database, trial_id=first, request_hash="request-1", outcome="NEAR_PASS", metrics={"sharpe": 1.5}
    )
    with sqlite3.connect(database) as con:
        row = con.execute(
            "SELECT candidate_id,parent_candidate_id,tune_stage,request_hash,terminal_status,outcome FROM settings_trials"
        ).fetchone()
    assert row == ("parent", "parent", "DECAY_COARSE", "request-1", "COMPLETE", "NEAR_PASS")


def test_tune_request_requires_verified_parent_lineage(tmp_path) -> None:
    from alpha_mining.domain.expression_normalization import expression_identity
    from alpha_mining.factory.simulation_requests import SimulationRequestStore
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "requests.sqlite"
    migrate(database)
    store = SimulationRequestStore(database)
    expression = "rank(close)"
    initial = store.claim(expression, {"decay": 0}, context={"candidate_id": "parent"})
    assert initial.claimed

    unverified = store.claim(
        expression, {"decay": 2}, allow_existing_identity=True,
        context={"tune_parent_candidate_id": "missing"},
    )
    assert unverified.reason == "tune_parent_unverified"
    with sqlite3.connect(database) as con:
        con.execute(
            "INSERT INTO candidate_outcomes(request_hash,candidate_id,exact_hash,outcome,observed_at) VALUES (?,?,?,?,?)",
            (initial.request_hash, "parent", expression_identity(expression).exact_hash, "NEAR_PASS", "2026-08-03T00:00:00Z"),
        )

    verified = store.claim(
        expression, {"decay": 2}, allow_existing_identity=True,
        context={"tune_parent_candidate_id": "parent"},
    )
    assert verified.claimed
