"""Tests for description PATCH delivery to WorldQuant Brain API."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock


from alpha_mining.storage.sqlite_store import SqliteRunLog
from alpha_mining.submitter.observation import SubmissionObservationService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> SqliteRunLog:
    db = SqliteRunLog(tmp_path / "research.sqlite")
    db.initialize_schema()
    return db


def _store_description(
    db: SqliteRunLog, expression: str, alpha_id: str, text: str
) -> None:
    """Directly insert a description row into submission_observations for test setup."""
    from alpha_mining.integration.phase4 import expression_id_for
    import hashlib
    from datetime import datetime, timezone

    expression_id = expression_id_for(expression)
    obs_id = hashlib.sha256(f"{expression_id}|{alpha_id}|test".encode()).hexdigest()
    with sqlite3.connect(str(db.path)) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO submission_observations (
                observation_id, expression_id, alpha_id, check_digest, check_passed,
                queue_status, metrics_json, checks_json, failure_categories_json,
                recommended_actions_json, description_text, description_source, created_at
            ) VALUES (?, ?, ?, 'test', 1, 'ready', '{}', '[]', '[]', '[]', ?, 'template', ?)
            """,
            (
                obs_id,
                expression_id,
                alpha_id,
                text,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


# ---------------------------------------------------------------------------
# SubmissionObservationService.fetch_description
# ---------------------------------------------------------------------------


class TestFetchDescription:
    def test_returns_stored_description_by_alpha_id(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        expr = "ts_rank(close, 21)"
        _store_description(
            db,
            expr,
            "alpha-123",
            "Idea: test description stored here.\nRationale for data used: close price.\nRationale for operators used: ts_rank.",
        )
        svc = SubmissionObservationService(db)

        from alpha_mining.integration.phase4 import expression_id_for

        result = svc.fetch_description(expression_id_for(expr), "alpha-123")

        assert result is not None
        assert "Idea:" in result

    def test_falls_back_to_any_expression_match_when_alpha_id_missing(
        self, tmp_path: Path
    ) -> None:
        db = _make_db(tmp_path)
        expr = "rank(close)"
        _store_description(
            db,
            expr,
            "",
            "Idea: fallback description.\nRationale for data used: close.\nRationale for operators used: rank.",
        )
        svc = SubmissionObservationService(db)

        from alpha_mining.integration.phase4 import expression_id_for

        result = svc.fetch_description(expression_id_for(expr), "alpha-new-id")

        assert result is not None

    def test_returns_none_when_nothing_stored(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        svc = SubmissionObservationService(db)

        from alpha_mining.integration.phase4 import expression_id_for

        result = svc.fetch_description(expression_id_for("rank(volume)"), "alpha-xyz")

        assert result is None


# ---------------------------------------------------------------------------
# patch_alpha_description — HTTP layer
# ---------------------------------------------------------------------------


def _make_pipeline_stub(tmp_path: Path, dry_run: bool = False):
    """Build a minimal pipeline-like object with patch_alpha_description wired up."""
    # Load the real module
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    mod_path = root / "auto_alpha_pipeline_rebuilt_v50.py"
    mod_spec = importlib.util.spec_from_file_location(
        "auto_alpha_pipeline_rebuilt_v50", mod_path
    )
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules["auto_alpha_pipeline_rebuilt_v50"] = mod
    mod_spec.loader.exec_module(mod)

    cfg = mod.PipelineConfig(
        username="test_user",
        password="test_pass",
        dry_run_submit=dry_run,
        sqlite_runs_path=str(tmp_path / "research.sqlite"),
    )
    pipe = object.__new__(mod.WorldQuantAlphaPipeline)
    pipe.cfg = cfg
    pipe._submission_observer = None

    # Fake session
    fake_sess = MagicMock()
    pipe.sess = fake_sess
    import threading

    pipe._sess_lock = threading.Lock()

    def _sess_request(method, url, **kwargs):
        return fake_sess.request(method, url, **kwargs)

    pipe._sess_request = _sess_request
    pipe._timeout = lambda: (10.0, 30.0)
    pipe.ensure_authenticated = MagicMock()
    return pipe, fake_sess


class TestPatchAlphaDescription:
    def test_sends_patch_with_description_field(self, tmp_path: Path) -> None:
        pipe, fake_sess = _make_pipeline_stub(tmp_path)
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        fake_sess.request.return_value = resp_mock

        ok = pipe.patch_alpha_description(
            "alpha-abc",
            "Idea: test.\nRationale for data used: price.\nRationale for operators used: rank.",
        )

        assert ok is True
        args, kwargs = fake_sess.request.call_args
        assert args[0] == "PATCH"
        assert "alpha-abc" in args[1]
        assert "description" in kwargs.get("json", {})
        assert "Idea:" in kwargs["json"]["description"]

    def test_returns_true_in_dry_run_without_http_call(self, tmp_path: Path) -> None:
        pipe, fake_sess = _make_pipeline_stub(tmp_path, dry_run=True)

        ok = pipe.patch_alpha_description("alpha-abc", "any description text here")

        assert ok is True
        fake_sess.request.assert_not_called()

    def test_returns_false_on_empty_description(self, tmp_path: Path) -> None:
        pipe, fake_sess = _make_pipeline_stub(tmp_path)

        ok = pipe.patch_alpha_description("alpha-abc", "")

        assert ok is False
        fake_sess.request.assert_not_called()

    def test_returns_false_and_does_not_raise_on_http_error(
        self, tmp_path: Path
    ) -> None:
        pipe, fake_sess = _make_pipeline_stub(tmp_path)
        fake_sess.request.side_effect = Exception("network error")

        ok = pipe.patch_alpha_description(
            "alpha-abc", "Idea: test description text here that is long enough."
        )

        assert ok is False

    def test_patch_is_idempotent(self, tmp_path: Path) -> None:
        pipe, fake_sess = _make_pipeline_stub(tmp_path)
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        fake_sess.request.return_value = resp_mock
        desc = "Idea: idempotent test.\nRationale for data used: volume.\nRationale for operators used: ts_rank."

        pipe.patch_alpha_description("alpha-idem", desc)
        pipe.patch_alpha_description("alpha-idem", desc)

        assert fake_sess.request.call_count == 2
        for c in fake_sess.request.call_args_list:
            assert c[1]["json"]["description"] == desc


# ---------------------------------------------------------------------------
# Legacy submit integration is disabled; vNext owns guarded POST /submit.
# ---------------------------------------------------------------------------


class TestSubmitLoopInjectsDescription:
    def test_legacy_submit_is_blocked_without_post(self, tmp_path: Path) -> None:
        pipe, fake_sess = _make_pipeline_stub(tmp_path)

        # Inject a description into SQLite so _get_description_for can retrieve it
        db = SqliteRunLog(tmp_path / "research.sqlite")
        db.initialize_schema()
        expr = "rank(close - open)"
        _store_description(
            db,
            expr,
            "alpha-loop-1",
            "Idea: loop test.\nRationale for data used: close and open.\nRationale for operators used: rank.",
        )
        pipe.cfg.submission_observe_enabled = True

        call_order = []

        patch_resp = MagicMock()
        patch_resp.status_code = 200
        submit_resp = MagicMock()
        submit_resp.status_code = 202

        def fake_request(method, url, **kwargs):
            call_order.append(method)
            if method == "PATCH":
                return patch_resp
            return submit_resp

        fake_sess.request.side_effect = fake_request

        pipe.patch_alpha_description(
            "alpha-loop-1",
            "Idea: loop test.\nRationale for data used: close and open.\nRationale for operators used: rank.",
        )
        ok, note = pipe.submit_alpha("alpha-loop-1")

        assert call_order == ["PATCH"]
        assert ok is False
        assert "legacy_live_submit_disabled" in note
