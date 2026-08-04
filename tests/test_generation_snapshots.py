from __future__ import annotations

import json
import time
from pathlib import Path


def _write_dot_catalog(root: Path) -> None:
    context = {"cached_at": time.time(), "region": "USA", "universe": "TOP3000", "delay": 1}
    (root / ".alpha_datasets_cache.json").write_text(json.dumps({**context, "dataset_ids": ["ds"], "records": [{"id": "ds"}]}), encoding="utf-8")
    (root / ".alpha_datafields_cache.json").write_text(json.dumps({**context, "rows": [{"id": "field", "_ds": "ds", "type": "MATRIX"}]}), encoding="utf-8")
    (root / ".alpha_operators_cache.json").write_text(json.dumps({**context, "records": [{"name": "rank", "signature": "rank(x)", "arity": 1}]}), encoding="utf-8")


def test_snapshot_loader_requires_complete_local_catalog_and_summarises_feedback(tmp_path: Path) -> None:
    from alpha_mining.generation.feedback import CandidateFeedbackStore
    from alpha_mining.generation.snapshots import CatalogUnavailable, load_local_snapshots
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    try:
        load_local_snapshots(root=tmp_path, database=tmp_path / "history.sqlite")
        raise AssertionError("missing catalog must fail closed")
    except CatalogUnavailable:
        pass
    _write_dot_catalog(tmp_path)
    queue = CandidateCsvQueue(tmp_path / "待提交Alpha列表.csv", tmp_path / "events.csv")
    row = queue.empty_candidate()
    row.update(
        candidate_id="candidate-1",
        request_hash="request-1",
        expression="rank(field)",
        queue_status="FAILED",
        datasets='["ds"]',
    )
    with queue.writer():
        queue.upsert(row)
    feedback = CandidateFeedbackStore(tmp_path / "history.sqlite")
    feedback.record(
        "request-1", "FAILED", strategy_family="fundamental", dataset="ds",
        checks=[{"name": "SELF_CORRELATION", "result": "FAIL"}],
    )

    snapshots = load_local_snapshots(
        root=tmp_path,
        database=tmp_path / "history.sqlite",
        queue_path=queue.queue_path,
    )

    assert snapshots.catalog_source == "root-dot-cache"
    assert len(snapshots.catalog.fields) == 1
    assert snapshots.feedback.failure_counts["SELF_CORRELATION"] == 1
    assert len(snapshots.feedback.self_corr_risk) == 1


def test_v50_kernel_uses_pure_primitives_without_worldquant_pipeline(tmp_path: Path) -> None:
    from alpha_mining.generation.snapshots import load_local_snapshots
    from alpha_mining.generation.v50_kernel import V50Kernel

    _write_dot_catalog(tmp_path)
    snapshots = load_local_snapshots(root=tmp_path, database=tmp_path / "history.sqlite")
    batch = V50Kernel(seed_pool_size=12).generate_batch(snapshots)

    source = Path("alpha_mining/generation/v50_kernel.py").read_text(encoding="utf-8")
    assert batch.candidates
    assert "WorldQuantAlphaPipeline(" not in source
    assert "fetch_datafields" not in source
    assert "submit_simulation" not in source


def test_historical_operator_observations_keep_arity_as_a_local_hard_gate(tmp_path: Path) -> None:
    from alpha_mining.generation.snapshots import load_local_snapshots
    from alpha_mining.generation.validation import LocalExpressionValidator

    _write_dot_catalog(tmp_path)
    path = tmp_path / ".alpha_operators_cache.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source"] = "historical_platform_observations"
    payload["records"].append({"name": "log", "signature": "log(x, x)", "arity": 2})
    path.write_text(json.dumps(payload), encoding="utf-8")

    snapshots = load_local_snapshots(root=tmp_path, database=tmp_path / "history.sqlite")
    issues = LocalExpressionValidator(snapshots.catalog, allow_stale_catalog=True).validate(
        "log(field)", expected_dataset_id="ds",
    )

    assert snapshots.catalog.info["source"] == "historical_platform_observations"
    assert snapshots.catalog.info["operator_arity_trusted"] is False
    assert [(issue.code, issue.message) for issue in issues] == [
        ("INVALID_ARITY", "log expects 2, got 1"),
    ]


def test_pending_queue_is_inventory_not_platform_feedback(tmp_path: Path) -> None:
    from alpha_mining.generation.snapshots import load_local_snapshots
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    _write_dot_catalog(tmp_path)
    queue = CandidateCsvQueue(tmp_path / "待提交Alpha列表.csv", tmp_path / "events.csv")
    row = queue.empty_candidate()
    row.update(
        candidate_id="candidate-1",
        request_hash="request-1",
        expression="rank(field)",
        queue_status="PENDING_SIMULATION",
        datasets='["ds"]',
    )
    with queue.writer():
        queue.upsert(row)

    snapshots = load_local_snapshots(
        root=tmp_path,
        database=tmp_path / "history.sqlite",
        queue_path=queue.queue_path,
    )

    assert len(snapshots.inventory.records) == 1
    assert snapshots.inventory.records[0].expression == "rank(field)"
    assert snapshots.feedback.records == ()
    assert snapshots.feedback.expressions == ()


def test_platform_outcome_is_grounded_to_queue_expression_by_request_hash(tmp_path: Path) -> None:
    from alpha_mining.generation.feedback import CandidateFeedbackStore
    from alpha_mining.generation.snapshots import load_local_snapshots
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    _write_dot_catalog(tmp_path)
    queue = CandidateCsvQueue(tmp_path / "待提交Alpha列表.csv", tmp_path / "events.csv")
    row = queue.empty_candidate()
    row.update(
        candidate_id="candidate-1",
        request_hash="request-1",
        expression="rank(field)",
        queue_status="SIMULATED",
        datasets='["ds"]',
        operator_family="rank",
    )
    with queue.writer():
        queue.upsert(row)
    CandidateFeedbackStore(tmp_path / "history.sqlite").record(
        "request-1",
        "FAILED",
        strategy_family="fundamental",
        dataset="ds",
        checks=[{"name": "SELF_CORRELATION", "result": "FAIL"}],
    )

    snapshots = load_local_snapshots(
        root=tmp_path,
        database=tmp_path / "history.sqlite",
        queue_path=queue.queue_path,
    )

    assert len(snapshots.feedback.records) == 1
    record = snapshots.feedback.records[0]
    assert record.expression == "rank(field)"
    assert record.grounded is True
    assert record.request_hash == "request-1"
    assert record.ref_id.startswith("feedback:sqlite:")


def test_field_quality_metadata_is_preserved_into_v50_adapter(tmp_path: Path) -> None:
    from alpha_mining.generation.snapshots import load_local_snapshots
    from alpha_mining.generation.v50_kernel import V50Kernel

    _write_dot_catalog(tmp_path)
    path = tmp_path / ".alpha_datafields_cache.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rows"][0].update({"coverage": 0.42, "dateCoverage": 0.37, "userCount": 137, "alphaCount": 19})
    path.write_text(json.dumps(payload), encoding="utf-8")

    snapshots = load_local_snapshots(root=tmp_path, database=tmp_path / "history.sqlite")
    field = snapshots.catalog.fields["field"]
    batch = V50Kernel(seed_pool_size=12).generate_batch(snapshots)
    row = batch.catalog.df.iloc[0]

    assert field.coverage == 0.42
    assert field.date_coverage == 0.37
    assert field.user_count == 137
    assert field.alpha_count == 19
    assert float(row["coverage"]) == 0.42
    assert float(row["dateCoverage"]) == 0.37
    assert float(row["userCount"]) == 137


def test_feedback_uses_quality_reasons_and_does_not_promote_unchecked_outcomes(tmp_path: Path) -> None:
    from alpha_mining.generation.feedback import CandidateFeedbackStore
    from alpha_mining.generation.snapshots import load_local_snapshots
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    _write_dot_catalog(tmp_path)
    queue = CandidateCsvQueue(tmp_path / "待提交Alpha列表.csv", tmp_path / "events.csv")
    failed = queue.empty_candidate()
    failed.update(
        candidate_id="candidate-failed",
        request_hash="request-failed",
        expression="rank(field)",
        queue_status="FAILED",
        datasets='["ds"]',
        operator_family="rank",
    )
    waiting = queue.empty_candidate()
    waiting.update(
        candidate_id="candidate-waiting",
        request_hash="request-waiting",
        expression="rank(field + 1)",
        queue_status="SIMULATED",
        datasets='["ds"]',
        operator_family="rank",
    )
    with queue.writer():
        queue.upsert(failed)
        queue.upsert(waiting)

    store = CandidateFeedbackStore(tmp_path / "history.sqlite")
    store.record(
        "request-failed",
        "FAILED",
        quality_reasons=["SHARPE_LOW", "TURNOVER_HIGH"],
    )
    store.record(
        "request-waiting",
        "WAITING_CHECKS",
    )

    snapshots = load_local_snapshots(
        root=tmp_path,
        database=tmp_path / "history.sqlite",
        queue_path=queue.queue_path,
    )

    by_hash = {item.request_hash: item for item in snapshots.feedback.records}
    assert by_hash["request-failed"].failure_types == ("LOW_SHARPE", "HIGH_TURNOVER")
    assert by_hash["request-failed"].dataset == "ds"
    assert by_hash["request-waiting"] not in snapshots.feedback.positive
    assert by_hash["request-waiting"] not in snapshots.feedback.failures


def test_ungrounded_platform_observation_only_contributes_aggregate_failure_counts(tmp_path: Path) -> None:
    from alpha_mining.generation.feedback import CandidateFeedbackStore
    from alpha_mining.generation.snapshots import load_local_snapshots

    _write_dot_catalog(tmp_path)
    CandidateFeedbackStore(tmp_path / "history.sqlite").record(
        "missing-request",
        "FAILED",
        checks=[{"name": "SELF_CORRELATION", "result": "FAIL"}],
        quality_reasons=["LOW_SHARPE"],
    )

    snapshots = load_local_snapshots(root=tmp_path, database=tmp_path / "history.sqlite")

    assert len(snapshots.feedback.records) == 1
    assert snapshots.feedback.records[0].grounded is False
    assert snapshots.feedback.failure_counts == {"SELF_CORRELATION": 1, "LOW_SHARPE": 1}
    assert snapshots.feedback.expressions == ()
    assert snapshots.feedback.self_corr_risk == ()
    assert snapshots.feedback.positive == ()
    assert snapshots.feedback.near_pass == ()

    from alpha_mining.generation.high_quality import HighQualityGenerator
    from alpha_mining.knowledge.worldquant_repository import KnowledgeContext

    prompt = json.loads(
        HighQualityGenerator._candidate_prompt(
            snapshots,
            [],
            KnowledgeContext((), "NO_GROUNDED_FEEDBACK"),
            {},
        )
    )
    assert prompt["allowed_feedback_refs"] == []
