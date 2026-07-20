from __future__ import annotations

import sqlite3
from pathlib import Path

from alpha_mining.storage.sqlite_store import SqliteRunLog
from alpha_mining.submitter.description import (
    MIN_DESCRIPTION_LENGTH,
    generate_description,
)
from alpha_mining.submitter.observation import (
    SubmissionObservationService,
    observe_feedback_csv,
)


class FakeLLM:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls = 0

    def generate_json(self, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        return self.response


def test_description_uses_structured_llm_when_valid() -> None:
    llm = FakeLLM(
        {
            "idea": "This alpha measures persistent relative strength in a diversified cross-section.",
            "data_rationale": "The selected market inputs represent liquid price and activity signals used to compare stocks consistently.",
            "operator_rationale": "Ranking and time-series operators smooth transient noise while preserving comparable cross-sectional ordering.",
        }
    )

    draft = generate_description(
        "group_neutralize(ts_rank(close, 21), market)", llm=llm
    )

    assert draft.source == "deepseek"
    assert len(draft.text) >= MIN_DESCRIPTION_LENGTH
    assert "Idea:" in draft.text
    assert llm.calls == 1


def test_description_falls_back_when_llm_output_is_invalid() -> None:
    draft = generate_description(
        "group_neutralize(ts_rank(close, 21), market)",
        llm=FakeLLM({"idea": "short"}),
        family="fundamental",
    )

    assert draft.source == "template"
    assert len(draft.text) >= MIN_DESCRIPTION_LENGTH
    assert "Rationale for data used:" in draft.text


def test_observation_persists_new_platform_failure_idempotently(tmp_path: Path) -> None:
    database = tmp_path / "research.sqlite"
    service = SubmissionObservationService(SqliteRunLog(database))
    checks = [{"name": "PROD_CORRELATION", "result": "FAIL", "value": 0.81}]

    first = service.observe(
        alpha_id="alpha-1",
        expression="ts_rank(close, 21)",
        checks=checks,
        metrics={"sharpe": 1.3, "fitness": 1.1},
        queue_status="not_queued:checks_not_passed",
        check_passed=False,
        failure_detail="PROD_CORRELATION above cutoff",
    )
    second = service.observe(
        alpha_id="alpha-1",
        expression="ts_rank(close, 21)",
        checks=checks,
        metrics={"sharpe": 1.3, "fitness": 1.1},
        queue_status="not_queued:checks_not_passed",
        check_passed=False,
        failure_detail="PROD_CORRELATION above cutoff",
    )

    assert first.failure_categories == ("PROD_CORRELATION",)
    assert second.observation_id == first.observation_id
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT failure_categories_json, recommended_actions_json, description_text "
            "FROM submission_observations"
        ).fetchone()
        count = connection.execute(
            "SELECT COUNT(*) FROM submission_observations"
        ).fetchone()[0]
    assert count == 1
    assert "PROD_CORRELATION" in row[0]
    assert "change_data_or_operator_family" in row[1]
    assert row[2] is None


def test_ready_observation_generates_description_without_platform_write(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research.sqlite"
    service = SubmissionObservationService(
        SqliteRunLog(database),
        llm_factory=lambda: FakeLLM({"idea": "invalid"}),
        description_limit=1,
    )

    result = service.observe(
        alpha_id="alpha-ready",
        expression="group_neutralize(ts_rank(close, 21), market)",
        checks=[{"name": "SELF_CORRELATION", "result": "PASS"}],
        metrics={"sharpe": 1.4, "fitness": 1.2},
        queue_status="ready",
        check_passed=True,
        failure_detail="",
    )

    assert result.description_source == "template"
    assert result.description_text is not None
    assert len(result.description_text) >= MIN_DESCRIPTION_LENGTH
    with sqlite3.connect(database) as connection:
        text = connection.execute(
            "SELECT description_text FROM submission_observations WHERE alpha_id='alpha-ready'"
        ).fetchone()[0]
    assert text == result.description_text


def test_feedback_csv_replay_observes_rows_without_network(tmp_path: Path) -> None:
    source = tmp_path / "feedback.csv"
    source.write_text(
        "alpha_id,expression,family,source,queue_status,check_passed,Failure Reasons,platform_check_json,Sharpe,Fitness\n"
        "alpha-1,ts_rank(close 21),fundamental,fixture,not_queued:checks_not_passed,False,"
        'PROD_CORRELATION above cutoff,"{""is"":{""checks"":[{""name"":""PROD_CORRELATION"",""result"":""FAIL""}]}}",1.3,1.1\n',
        encoding="utf-8",
    )

    summary = observe_feedback_csv(
        SqliteRunLog(tmp_path / "research.sqlite"),
        source,
        llm_factory=lambda: FakeLLM({"idea": "invalid"}),
    )

    assert summary.rows_scanned == 1
    assert summary.rows_observed == 1
    assert summary.failure_category_counts == {"PROD_CORRELATION": 1}
    with sqlite3.connect(tmp_path / "research.sqlite") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM submission_observations"
            ).fetchone()[0]
            == 1
        )
