from __future__ import annotations

import inspect
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import run_pipeline_loop as pipeline_loop


def test_run_forever_survives_twenty_rc1_then_continues_after_success() -> None:
    calls: list[int] = []
    outcomes: list[object] = []
    sleeps: list[float] = []

    def cycle_runner(cycle: int) -> int:
        calls.append(cycle)
        return 1 if cycle <= 20 else 0

    result = pipeline_loop.run_forever(
        cycle_runner=cycle_runner,
        stop_requested=lambda: len(calls) >= 22,
        sleeper=sleeps.append,
        on_outcome=outcomes.append,
        inter_cycle_sleep=2.0,
    )

    assert result == 0
    assert calls == list(range(1, 23))
    assert len(outcomes) == 22
    assert outcomes[19].consecutive_failures == 20
    assert outcomes[20].consecutive_failures == 0
    assert outcomes[21].cycle == 22
    assert len(sleeps) == 21


def test_run_forever_records_unknown_exception_and_recreates_next_cycle() -> None:
    calls: list[int] = []
    outcomes = []

    def cycle_runner(cycle: int) -> int:
        calls.append(cycle)
        if cycle == 1:
            raise RuntimeError("broken child task")
        return 0

    pipeline_loop.run_forever(
        cycle_runner=cycle_runner,
        stop_requested=lambda: len(calls) >= 2,
        sleeper=lambda _seconds: None,
        on_outcome=outcomes.append,
    )

    assert calls == [1, 2]
    assert outcomes[0].category is pipeline_loop.RecoveryCategory.UNKNOWN_RUNTIME_ERROR
    assert "RuntimeError: broken child task" in outcomes[0].traceback_text
    assert outcomes[0].consecutive_failures == 1
    assert outcomes[1].category is pipeline_loop.RecoveryCategory.SUCCESS


def test_production_entry_uses_unbounded_recovery_loop_without_sentinel() -> None:
    source = inspect.getsource(pipeline_loop)
    main_source = inspect.getsource(pipeline_loop.main)

    assert "max-consecutive-failures" not in source
    assert "pipeline_loop_blocked.flag" not in source
    assert "--max-cycles" not in source
    assert "run_forever(" in main_source


def test_run_forever_classifies_child_crash_and_honors_retry_after() -> None:
    outcomes = []
    sleeps: list[float] = []

    def cycle_runner(cycle: int) -> pipeline_loop.CycleOutcome:
        return pipeline_loop.CycleOutcome(
            cycle=cycle,
            rc=-9,
            category=pipeline_loop.RecoveryCategory.CHILD_PROCESS_CRASH,
            task_id="task-7",
            input_id="expr-7",
            retry_after_seconds=7.5,
        )

    pipeline_loop.run_forever(
        cycle_runner=cycle_runner,
        stop_requested=lambda: len(outcomes) >= 1,
        sleeper=sleeps.append,
        on_outcome=outcomes.append,
    )

    assert outcomes[0].category is pipeline_loop.RecoveryCategory.CHILD_PROCESS_CRASH
    assert outcomes[0].task_id == "task-7"
    assert outcomes[0].input_id == "expr-7"
    assert pipeline_loop._recovery_delay(
        outcomes[0], consecutive_failures=1, inter_cycle_sleep=120
    ) == 7.5
    assert sleeps == []


def test_run_forever_stop_event_is_the_only_loop_exit_signal() -> None:
    stop_event = threading.Event()
    calls: list[int] = []

    def cycle_runner(cycle: int) -> int:
        calls.append(cycle)
        stop_event.set()
        return 1

    result = pipeline_loop.run_forever(
        cycle_runner=cycle_runner,
        stop_requested=stop_event.is_set,
        sleeper=lambda _seconds: (_ for _ in ()).throw(
            AssertionError("must not sleep after an explicit stop")
        ),
        inter_cycle_sleep=60,
    )

    assert result == 0
    assert calls == [1]


def test_recovery_delay_is_capped_and_success_uses_base_sleep() -> None:
    failure = pipeline_loop.CycleOutcome(
        cycle=1,
        rc=429,
        category=pipeline_loop.RecoveryCategory.RATE_LIMITED,
    )
    assert pipeline_loop._recovery_delay(
        failure, consecutive_failures=100, inter_cycle_sleep=120
    ) == 900
    assert pipeline_loop._recovery_delay(
        pipeline_loop.CycleOutcome(1, 0, pipeline_loop.RecoveryCategory.SUCCESS),
        consecutive_failures=0,
        inter_cycle_sleep=12,
    ) == 12


def test_cycle_outcome_unknown_exception_keeps_task_identity_and_traceback() -> None:
    observed = []

    def runner(_cycle: int) -> int:
        raise ValueError("bad expression task password=do-not-persist")

    pipeline_loop.run_forever(
        cycle_runner=runner,
        stop_requested=lambda: len(observed) >= 1,
        sleeper=lambda _seconds: None,
        on_outcome=observed.append,
    )

    assert observed[0].category is pipeline_loop.RecoveryCategory.UNKNOWN_RUNTIME_ERROR
    assert "ValueError: bad expression task" in observed[0].traceback_text
    assert "do-not-persist" not in observed[0].traceback_text


def test_child_process_crash_outcome_keeps_sanitized_output_tail() -> None:
    result = pipeline_loop.ChildProcessResult(
        rc=-9,
        output_tail=(
            "Traceback (most recent call last):\n"
            "RuntimeError: worker crashed password=do-not-store"
        ),
    )

    outcome = pipeline_loop._outcome_from_child_result(
        cycle=3,
        task_id="cycle_3/simulate",
        result=result,
    )

    assert outcome.category is pipeline_loop.RecoveryCategory.CHILD_PROCESS_CRASH
    assert outcome.task_id == "cycle_3/simulate"
    assert "RuntimeError: worker crashed" in outcome.traceback_text
    assert "do-not-store" not in outcome.traceback_text

    unknown = pipeline_loop._outcome_from_child_result(
        cycle=4,
        task_id="cycle_4/simulate",
        result=pipeline_loop.ChildProcessResult(
            rc=7,
            output_tail="Traceback (most recent call last):\nRuntimeError: unknown",
        ),
    )
    assert unknown.category is pipeline_loop.RecoveryCategory.UNKNOWN_RUNTIME_ERROR


def test_outcome_persistence_failure_does_not_stop_next_cycle() -> None:
    calls: list[int] = []
    callback_calls = 0

    def runner(cycle: int) -> int:
        calls.append(cycle)
        return 1

    def failing_callback(_outcome: pipeline_loop.CycleOutcome) -> None:
        nonlocal callback_calls
        callback_calls += 1
        if callback_calls == 1:
            raise OSError("temporary state write failure")

    pipeline_loop.run_forever(
        cycle_runner=runner,
        stop_requested=lambda: len(calls) >= 2,
        sleeper=lambda _seconds: None,
        on_outcome=failing_callback,
    )

    assert calls == [1, 2]
    assert callback_calls == 2


def test_rate_limit_outcome_uses_persisted_retry_after_deadline(tmp_path) -> None:
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "rate-limit.sqlite"
    migrate(database)
    now = datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc)
    deadline = (now + timedelta(seconds=75)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(database) as con:
        con.execute(
            "UPDATE platform_access_state SET state='RATE_LIMITED',retry_after_until=? WHERE singleton=1",
            (deadline,),
        )

    assert pipeline_loop._persisted_retry_after_seconds(database, now=now) == 75


def test_stop_probe_database_lock_is_recoverable() -> None:
    calls: list[int] = []
    probes = 0

    def stop_probe() -> bool:
        nonlocal probes
        probes += 1
        if probes == 1:
            raise sqlite3.OperationalError("database is locked")
        return len(calls) >= 2

    pipeline_loop.run_forever(
        cycle_runner=lambda cycle: calls.append(cycle) or 0,
        stop_requested=stop_probe,
        sleeper=lambda _seconds: None,
    )

    assert calls == [1, 2]
