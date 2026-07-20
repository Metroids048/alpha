from __future__ import annotations

import csv
import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock


def _module():
    return importlib.import_module("auto_alpha_pipeline_rebuilt_v50")


def _payload() -> dict:
    return {
        "regular": "ts_rank(close, 21)",
        "settings": {"region": "USA", "universe": "TOP3000"},
        "meta": {"family": "test", "source": "test"},
    }


def test_submission_observation_defaults_to_zero_behavior(tmp_path: Path) -> None:
    module = _module()
    feedback = tmp_path / "feedback.csv"
    cfg = module.PipelineConfig(
        username="u",
        password="p",
        feedback_ledger_filename=str(feedback),
        hopeful_queue_filename=str(tmp_path / "hopeful.jsonl"),
        submission_results_filename=str(tmp_path / "submissions.jsonl"),
    )
    pipeline = module.WorldQuantAlphaPipeline(cfg)
    pipeline._submission_observation_service = MagicMock(
        side_effect=AssertionError("must stay disabled")
    )

    pipeline._append_feedback(
        _payload(),
        "alpha-1",
        {},
        "ok",
        True,
        "check_passed",
        "ready",
        check_json={"is": {"checks": [{"name": "SELF_CORRELATION", "result": "PASS"}]}},
    )

    assert feedback.is_file()
    assert not (tmp_path / "hopeful.jsonl").exists()
    assert not (tmp_path / "submissions.jsonl").exists()


def test_enabled_observer_records_feedback_without_queue_writes(tmp_path: Path) -> None:
    module = _module()
    feedback = tmp_path / "feedback.csv"
    queue_path = tmp_path / "hopeful.jsonl"
    submission_path = tmp_path / "submissions.jsonl"
    cfg = module.PipelineConfig(
        username="u",
        password="p",
        sqlite_runs_path=str(tmp_path / "research.sqlite"),
        submission_observe_enabled=True,
        feedback_ledger_filename=str(feedback),
        hopeful_queue_filename=str(queue_path),
        submission_results_filename=str(submission_path),
    )
    pipeline = module.WorldQuantAlphaPipeline(cfg)

    pipeline._append_feedback(
        _payload(),
        "alpha-1",
        {},
        "ok",
        False,
        "check_failed",
        "not_queued:checks_not_passed",
        check_json={"is": {"checks": [{"name": "IS_LADDER_SHARPE", "result": "FAIL"}]}},
    )

    assert feedback.is_file()
    assert not queue_path.exists()
    assert not submission_path.exists()
    with feedback.open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle))[-1]["alpha_id"] == "alpha-1"


def test_cli_requires_sqlite_for_submission_observation(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(sys, "argv", ["pipeline", "--submission-observe"])

    assert module.main() == 2
