from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _alpha(alpha_id: str, *, status: str = "UNSUBMITTED", expression: str = "rank(close)") -> dict:
    return {
        "id": alpha_id,
        "status": status,
        "type": "REGULAR",
        "hidden": False,
        "dateCreated": "2026-07-20T00:00:00Z",
        "dateModified": "2026-07-20T00:00:00Z",
        "regular": {"code": expression, "description": ""},
        "settings": {"region": "USA", "universe": "TOP3000", "delay": 1},
        "is": {
            "sharpe": 1.5,
            "fitness": 1.1,
            "turnover": 0.2,
            "checks": [
                {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
                {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
            ],
        },
    }


class FakeListClient:
    def __init__(self, pages: dict[int, list[dict]], count: int) -> None:
        self.pages = pages
        self.count = count
        self.params: list[dict] = []

    def list_alphas(self, params: dict) -> dict:
        self.params.append(dict(params))
        return {"count": self.count, "results": self.pages.get(int(params["offset"]), [])}


def test_platform_count_matches_full_pagination(tmp_path: Path) -> None:
    from alpha_mining.platform.ledger import AlphaQueryFilters, PlatformLedgerSynchronizer

    client = FakeListClient({0: [_alpha("a1"), _alpha("a2")], 2: [_alpha("a3")]}, 3)
    result = PlatformLedgerSynchronizer(tmp_path / "ledger.sqlite", page_size=2).sync(
        client, AlphaQueryFilters(status="UNSUBMITTED")
    )
    assert result.status == "COMPLETE"
    assert result.declared_count == result.unique_alpha_ids == 3


def test_count_and_list_use_same_filters(tmp_path: Path) -> None:
    from alpha_mining.platform.ledger import AlphaQueryFilters, PlatformLedgerSynchronizer

    client = FakeListClient({0: [_alpha("a1")]}, 1)
    filters = AlphaQueryFilters(status="UNSUBMITTED", region="USA", hidden=False)
    PlatformLedgerSynchronizer(tmp_path / "ledger.sqlite", page_size=10).sync(client, filters)
    canonical = filters.to_params()
    assert client.params
    assert all({k: value for k, value in call.items() if k not in {"limit", "offset", "order"}} == canonical for call in client.params)


def test_duplicate_alpha_id_not_double_counted(tmp_path: Path) -> None:
    from alpha_mining.platform.ledger import AlphaQueryFilters, PlatformLedgerSynchronizer

    client = FakeListClient({0: [_alpha("a1"), _alpha("a1")]}, 1)
    result = PlatformLedgerSynchronizer(tmp_path / "ledger.sqlite", page_size=10).sync(
        client, AlphaQueryFilters(status="UNSUBMITTED")
    )
    assert result.unique_alpha_ids == 1
    assert result.duplicate_alpha_ids == 1


class DateShardClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.params: list[dict] = []

    def list_alphas(self, params: dict) -> dict:
        self.params.append(dict(params))
        start = str(params.get("dateCreated>=") or "")
        end = str(params.get("dateCreated<") or "")
        selected = [
            row for row in self.rows
            if (not start or str(row["dateCreated"]) >= start)
            and (not end or str(row["dateCreated"]) < end)
        ]
        offset = int(params["offset"])
        limit = int(params["limit"])
        return {"count": len(selected), "results": selected[offset : offset + limit]}


def test_date_shards_are_half_open_and_do_not_duplicate_boundary(tmp_path: Path) -> None:
    from alpha_mining.platform.ledger import AlphaQueryFilters, PlatformLedgerSynchronizer

    rows = []
    for index, date in enumerate(("1971-01-01T00:00:00Z", "2000-01-01T00:00:00Z", "2026-01-01T00:00:00Z")):
        item = _alpha(f"a{index}")
        item["dateCreated"] = date
        rows.append(item)
    result = PlatformLedgerSynchronizer(tmp_path / "shards.sqlite", page_size=1, max_offset=0).sync(
        DateShardClient(rows), AlphaQueryFilters(status="UNSUBMITTED")
    )
    assert result.status == "COMPLETE"
    assert result.unique_alpha_ids == result.declared_count == 3


def test_partial_pagination_does_not_promote_ledger(tmp_path: Path) -> None:
    from alpha_mining.platform.ledger import AlphaQueryFilters, PlatformLedgerSynchronizer

    class FailingClient(FakeListClient):
        def list_alphas(self, params: dict) -> dict:
            if int(params["offset"]) > 0:
                raise RuntimeError("page failed")
            return super().list_alphas(params)

    db = tmp_path / "partial.sqlite"
    result = PlatformLedgerSynchronizer(db, page_size=1).sync(
        FailingClient({0: [_alpha("a1")]}, 2), AlphaQueryFilters()
    )
    assert result.status == "PARTIAL"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM platform_alpha_ledger").fetchone()[0] == 0


def test_database_migration_failure_rolls_back_partial_schema(tmp_path: Path, monkeypatch) -> None:
    import alpha_mining.storage.migrations as migrations

    db = tmp_path / "rollback.sqlite"
    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        ((999, "CREATE TABLE must_rollback(value TEXT); THIS IS NOT SQL;"),),
    )
    try:
        migrations.migrate(db)
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("invalid migration unexpectedly succeeded")
    with sqlite3.connect(db) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='must_rollback'"
        ).fetchone()[0] == 0


def test_remote_and_local_status_are_separate(tmp_path: Path) -> None:
    from alpha_mining.platform.ledger import AlphaQueryFilters, PlatformLedgerSynchronizer

    db = tmp_path / "ledger.sqlite"
    PlatformLedgerSynchronizer(db).sync(
        FakeListClient({0: [_alpha("a1", status="UNSUBMITTED")]}, 1),
        AlphaQueryFilters(status="UNSUBMITTED"),
    )
    with sqlite3.connect(db) as con:
        cols = {row[1] for row in con.execute("PRAGMA table_info(platform_alpha_ledger)")}
        status = con.execute("SELECT platform_status FROM platform_alpha_ledger WHERE alpha_id='a1'").fetchone()[0]
    assert "local_status" not in cols
    assert status == "UNSUBMITTED"


def test_self_corr_missing_blocks_submit() -> None:
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    decision = SubmissionGuard().evaluate(
        CandidateContext("a", "e", [{"name": "LOW_SHARPE", "result": "PASS", "mandatory": True}], True, True, "PASS")
    )
    assert not decision.allowed
    assert "SELF_CORRELATION_MISSING" in decision.reasons


def test_self_corr_unknown_blocks_submit() -> None:
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    decision = SubmissionGuard().evaluate(
        CandidateContext(
            "a",
            "e",
            [
                {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
                {"name": "SELF_CORRELATION", "result": "UNKNOWN", "mandatory": True},
            ],
            True,
            True,
            "PASS",
        )
    )
    assert not decision.allowed
    assert "SELF_CORRELATION_UNKNOWN" in decision.reasons


def test_settings_only_same_research_identity() -> None:
    from alpha_mining.domain.research_identity import ResearchIdentity

    identity = ResearchIdentity("improvement", "fundamental", "filing", "peer", "fundamental", "group_rank>ts_delta")
    assert identity.identity_id({"decay": 0}) == identity.identity_id({"decay": 8, "truncation": 0.05})


def test_failed_cluster_is_frozen() -> None:
    from alpha_mining.legacy.self_corr import cluster_disposition

    assert cluster_disposition(["FAIL", "FAIL", "FAIL"]) == "FROZEN"
    assert cluster_disposition(["FAIL", "MISSING", "FAIL"]) == "OBSERVE_ONLY"


def test_new_alpha_failure_has_primary_reason() -> None:
    from alpha_mining.analysis.funnel import classify_failure

    result = classify_failure(
        [{"name": "LOW_SHARPE", "result": "FAIL"}, {"name": "SELF_CORRELATION", "result": "FAIL"}]
    )
    assert result.primary_failure == "low_sharpe"
    assert result.all_failures == ("low_sharpe", "self_correlation")


def _description_inputs(alpha_type: str = "REGULAR") -> dict:
    return {
        "alpha_type": alpha_type,
        "expression": "rank(ts_delta(close, 21))",
        "field_metadata": {"close": {"description": "closing price"}},
        "settings": {"region": "USA", "universe": "TOP3000", "delay": 1, "decay": 2},
        "hypothesis": {"mechanism": "price change", "expected_direction": "higher_is_long"},
    }


def test_description_regular_schema() -> None:
    from alpha_mining.submitter.description import build_description

    draft = build_description(**_description_inputs())
    assert set(draft.sections) == {"hypothesis", "data_rationale", "operator_rationale", "long_short_interpretation", "settings_rationale", "expected_behavior", "risks"}
    assert set(draft.patch_payload) == {"regular"}


def test_description_selection_schema() -> None:
    from alpha_mining.submitter.description import build_description

    draft = build_description(**_description_inputs("SELECTION"))
    assert set(draft.sections) == {"selection_universe", "selection_conditions", "economic_rationale", "signal_construction", "risks"}
    assert set(draft.patch_payload) == {"selection"}


def test_description_combo_schema() -> None:
    from alpha_mining.submitter.description import build_description

    draft = build_description(**_description_inputs("COMBO"))
    assert set(draft.sections) == {"component_alphas", "combination_logic", "incremental_rationale", "correlation_control", "risks"}
    assert set(draft.patch_payload) == {"combo"}


def test_description_matches_expression_fields() -> None:
    from alpha_mining.submitter.description import build_description, validate_description

    draft = build_description(**_description_inputs())
    assert validate_description(draft, expression="rank(ts_delta(close, 21))").valid
    assert "close" in draft.fields
    assert draft.windows == (21,)


def test_description_direction_consistency() -> None:
    from alpha_mining.submitter.description import build_description, validate_description

    draft = build_description(**_description_inputs())
    assert validate_description(draft, expression="rank(ts_delta(close, 21))", expected_direction="higher_is_long").valid
    assert not validate_description(draft, expression="rank(ts_delta(close, 21))", expected_direction="higher_is_short").valid


def test_community_knowledge_cannot_be_production_rule_without_validation() -> None:
    from alpha_mining.knowledge.hub import KnowledgeRule

    rule = KnowledgeRule("community", "C", "peer surprise", approved=True, platform_validation_status="MISSING")
    assert not rule.production_eligible


def test_public_alpha_expression_not_copied() -> None:
    from alpha_mining.knowledge.hub import PublicExpressionGuard

    guard = PublicExpressionGuard(["rank(ts_delta(close, 21))"])
    assert not guard.allows("-2 * (rank(ts_delta(close, 22)))")
    assert guard.allows("group_rank(revenue/cap, industry)-0.5")


def test_submit_requires_fresh_platform_ledger() -> None:
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    checks = [
        {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
        {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
    ]
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    context = CandidateContext("a", "e", checks, True, True, "PASS", ledger_status="COMPLETE", ledger_synced_at=stale)
    decision = SubmissionGuard().evaluate(context)
    assert not decision.allowed
    assert "PLATFORM_LEDGER_STALE_OR_MISSING" in decision.reasons


def test_submit_blocks_when_platform_ledger_identity_is_missing() -> None:
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    checks = [
        {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
        {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
    ]
    decision = SubmissionGuard().evaluate(CandidateContext("a", "e", checks, True, True, "PASS"))
    assert not decision.allowed
    assert "PLATFORM_LEDGER_STALE_OR_MISSING" in decision.reasons
    assert "PLATFORM_LEDGER_SYNC_MISSING" in decision.reasons


def test_factory_hard_stop_blocks_generation(tmp_path: Path) -> None:
    from alpha_mining.factory.control import FactoryControl

    control = FactoryControl(tmp_path / "factory.sqlite")
    assert control.status().hard_stop is True
    assert control.can_generate() is False


def test_cycle_entry_uses_vnext_factory_runtime() -> None:
    source = (Path(__file__).parents[1] / "run_pipeline_cycle.py").read_text(encoding="utf-8")
    assert "alpha_mining.factory.runtime" in source
    assert "import auto_alpha_pipeline_rebuilt_v50" not in source
    legacy = (Path(__file__).parents[1] / "auto_alpha_pipeline_rebuilt_v50.py").read_text(encoding="utf-8")
    assert "FactoryControl" in legacy


def test_all_active_wrappers_enforce_factory_control() -> None:
    root = Path(__file__).parents[1]
    for name in ("run_pipeline_supervisor.py", "run_pipeline_loop.py"):
        assert "FactoryControl" in (root / name).read_text(encoding="utf-8"), name
    assert "alpha_mining.factory.runtime" in (root / "run_pipeline_cycle.py").read_text(encoding="utf-8")
    legacy_sync = (root / "更新alpha数据.py").read_text(encoding="utf-8")
    assert "legacy CSV platform sync is disabled" in legacy_sync


def test_browser_cookie_is_ignored_and_not_in_git_index() -> None:
    root = Path(__file__).parents[1]
    assert ".wq_browser_cookie*.json" in (root / ".gitignore").read_text(encoding="utf-8")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".wq_browser_cookie.next.json"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode != 0


def test_acceptance_report_writes_required_artifacts(tmp_path: Path) -> None:
    from alpha_mining.audit.acceptance import run_acceptance_audit
    from alpha_mining.storage.migrations import migrate

    db = tmp_path / "audit.sqlite"
    migrate(db)
    result = run_acceptance_audit(db, tmp_path)
    assert result.status == "BLOCKED"
    for name in (
        "CURRENT_MAIN_ACCEPTANCE_AUDIT.md",
        "CONSULTANT_FACTORY_FINAL_ACCEPTANCE.md",
        "platform_reconciliation.csv",
        "legacy_self_corr_clusters.csv",
        "new_alpha_failure_funnel.csv",
        "description_validation_report.csv",
        "knowledge_source_inventory.csv",
        "submission_dry_run.csv",
        "submission_blocked.csv",
    ):
        assert (tmp_path / name).is_file(), name


def test_generator_emits_only_seven_bounded_mechanism_variants() -> None:
    from alpha_mining.generator.consultant_generator import ConsultantGenerator

    candidates = ConsultantGenerator(max_per_hypothesis=99).generate(
        hypothesis_id="h1", family="fundamental", fields=["revenue", "close"]
    )
    assert [item.mutation_type for item in candidates] == [
        "baseline",
        "level_to_change",
        "change_to_acceleration",
        "absolute_to_historical_surprise",
        "absolute_to_peer_relative",
        "regime_conditioned",
        "low_correlation_parent_hybrid",
    ]


def test_parent_priority_is_correlation_then_quality_then_robustness() -> None:
    from alpha_mining.scheduler.parent_priority import rank_parents

    rows = [
        {"id": "high_sharpe_fail", "self_corr_status": "FAIL", "quality": 9.0, "robustness": 9.0, "mechanism_novelty": 9.0},
        {"id": "pass_robust", "self_corr_status": "PASS", "quality": 1.0, "robustness": 2.0, "mechanism_novelty": 0.0},
        {"id": "pass_quality", "self_corr_status": "PASS", "quality": 2.0, "robustness": 1.0, "mechanism_novelty": 0.0},
    ]
    assert [row["id"] for row in rank_parents(rows)] == ["pass_quality", "pass_robust", "high_sharpe_fail"]
