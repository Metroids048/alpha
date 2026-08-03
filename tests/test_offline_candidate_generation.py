from __future__ import annotations

import csv
import hashlib
import json
import os
import socket
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _write_cache(cache_dir: Path, *, stale: bool = False) -> None:
    cache_dir.mkdir(parents=True)
    operators = {
        "schema_version": "1",
        "records": [
            {"name": name, "signature": signature, "arity": arity, "description": description}
            for name, signature, arity, description in [
                ("abs", "abs(x)", 1, "absolute value"),
                ("add", "add(x, y)", 2, "addition"),
                ("divide", "divide(x, y)", 2, "division"),
                ("group_rank", "group_rank(x, group)", 2, "rank within group"),
                ("multiply", "multiply(x, y)", 2, "multiplication"),
                ("rank", "rank(x)", 1, "cross-sectional rank"),
                ("sign", "sign(x)", 1, "sign of input"),
                ("subtract", "subtract(x, y)", 2, "subtraction"),
                ("ts_corr", "ts_corr(x, y, d)", 3, "time-series correlation"),
                ("ts_decay_linear", "ts_decay_linear(x, d)", 2, "linear decay"),
                ("ts_delta", "ts_delta(x, d)", 2, "time-series change"),
                ("ts_max", "ts_max(x, d)", 2, "time-series maximum"),
                ("ts_mean", "ts_mean(x, d)", 2, "time-series mean"),
                ("ts_min", "ts_min(x, d)", 2, "time-series minimum"),
                ("ts_rank", "ts_rank(x, d)", 2, "time-series rank"),
                ("ts_std_dev", "ts_std_dev(x, d)", 2, "time-series standard deviation"),
                ("ts_sum", "ts_sum(x, d)", 2, "time-series sum"),
                ("ts_zscore", "ts_zscore(x, d)", 2, "time-series z-score"),
            ]
        ],
    }
    datasets = {
        "schema_version": "1",
        "records": [
            {"id": "pv_fixture", "name": "Price and volume fixture", "category": "price_volume"},
            {"id": "risk_fixture", "name": "Risk fixture", "category": "risk"},
            {"id": "fund_fixture", "name": "Fundamental fixture", "category": "fundamental"},
            {"id": "analyst_fixture", "name": "Analyst fixture", "category": "analyst"},
            {"id": "event_fixture", "name": "Event fixture", "category": "event"},
        ],
    }
    fields = {
        "schema_version": "1",
        "records": [
            {"id": "fixture_close", "dataset_id": "pv_fixture", "type": "MATRIX", "category": "price", "description": "fixture closing price"},
            {"id": "fixture_vwap", "dataset_id": "pv_fixture", "type": "MATRIX", "category": "price", "description": "fixture volume weighted price"},
            {"id": "fixture_volume", "dataset_id": "pv_fixture", "type": "MATRIX", "category": "liquidity", "description": "fixture traded volume"},
            {"id": "fixture_turnover", "dataset_id": "pv_fixture", "type": "MATRIX", "category": "liquidity", "description": "fixture turnover"},
            {"id": "fixture_realized_volatility", "dataset_id": "risk_fixture", "type": "MATRIX", "category": "volatility", "description": "fixture realized volatility"},
            {"id": "fixture_beta_risk", "dataset_id": "risk_fixture", "type": "MATRIX", "category": "volatility", "description": "fixture beta risk"},
            {"id": "fixture_revenue", "dataset_id": "fund_fixture", "type": "MATRIX", "category": "fundamental", "description": "fixture revenue"},
            {"id": "fixture_cashflow", "dataset_id": "fund_fixture", "type": "MATRIX", "category": "fundamental", "description": "fixture operating cash flow"},
            {"id": "fixture_earnings_yield", "dataset_id": "fund_fixture", "type": "MATRIX", "category": "valuation", "description": "fixture earnings yield"},
            {"id": "fixture_book_to_price", "dataset_id": "fund_fixture", "type": "MATRIX", "category": "valuation", "description": "fixture book to price"},
            {"id": "fixture_return_on_equity", "dataset_id": "fund_fixture", "type": "MATRIX", "category": "quality", "description": "fixture return on equity"},
            {"id": "fixture_gross_margin", "dataset_id": "fund_fixture", "type": "MATRIX", "category": "quality", "description": "fixture gross margin"},
            {"id": "fixture_eps_revision", "dataset_id": "analyst_fixture", "type": "MATRIX", "category": "expectation", "description": "fixture analyst EPS revision"},
            {"id": "fixture_sales_revision", "dataset_id": "analyst_fixture", "type": "MATRIX", "category": "expectation", "description": "fixture analyst sales revision"},
            {"id": "fixture_news_sentiment", "dataset_id": "event_fixture", "type": "MATRIX", "category": "event", "description": "fixture news sentiment"},
            {"id": "fixture_announcement", "dataset_id": "event_fixture", "type": "MATRIX", "category": "event", "description": "fixture announcement event"},
        ],
    }
    payloads = {"operators": operators, "data_fields": fields, "datasets": datasets}
    content_hash = hashlib.sha256(
        json.dumps(payloads, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    fetched = datetime.now(timezone.utc) - (timedelta(days=10) if stale else timedelta(minutes=1))
    info = {
        "fetched_at": fetched.isoformat().replace("+00:00", "Z"),
        "api_endpoints": ["/operators", "/data-fields", "/data-sets"],
        "region": "USA",
        "universe": "TOP3000",
        "delay": 1,
        "record_counts": {"operators": len(operators["records"]), "data_fields": len(fields["records"]), "datasets": len(datasets["records"])},
        "schema_version": "1",
        "content_hash": content_hash,
    }
    for name, payload in [
        ("操作符.json", operators),
        ("数据字段.json", fields),
        ("数据集.json", datasets),
        ("缓存信息.json", info),
    ]:
        (cache_dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_offline_generation_writes_100_unique_candidates_without_io_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alpha_mining.offline.service import run_offline_generation

    cache_dir = tmp_path / "数据" / "平台缓存"
    queue = tmp_path / "数据" / "候选队列" / "候选Alpha.csv"
    events = tmp_path / "数据" / "候选队列" / "处理事件.csv"
    _write_cache(cache_dir)

    def forbid_network(*args, **kwargs):
        raise AssertionError("offline generation attempted network access")

    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    monkeypatch.setattr(socket, "create_connection", forbid_network)
    original_getenv = os.getenv

    def guarded_getenv(key: str, *args):
        if key in {"WQ_USERNAME", "WQ_PASSWORD", "WQ_COOKIE", "WQ_SESSION"}:
            raise AssertionError(f"offline generation read {key}")
        return original_getenv(key, *args)

    monkeypatch.setattr(os, "getenv", guarded_getenv)
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        low = str(path).lower()
        if any(token in low for token in ("cookie", "session", ".env")):
            raise AssertionError(f"offline generation read sensitive path: {path}")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    platform_modules_before = {
        name for name in sys.modules if name == "alpha_mining.platform" or name.startswith("alpha_mining.platform.")
    }
    first = run_offline_generation(cache_dir=cache_dir, queue_path=queue, events_path=events, count=100)
    first_rows = _read_csv(queue)
    second = run_offline_generation(cache_dir=cache_dir, queue_path=queue, events_path=events, count=100)

    assert first.added == 100
    assert second.added == 0
    rows = _read_csv(queue)
    assert len(rows) == 100
    assert len({row["candidate_id"] for row in rows}) == 100
    signatures = [json.loads(row["canonical_signature"]) for row in rows]
    assert len({item["exact_hash"] for item in signatures}) == 100
    assert len({item["skeleton"] for item in signatures}) == 100
    assert all("group_rank" not in row["expression"] for row in rows)
    assert len({row["generator_source"] for row in rows}) == 1
    assert len({item["generator_family"] for item in signatures}) >= 8
    assert all(row["queue_status"] == "QUEUED" for row in rows)
    assert all("sharpe" not in row["description_draft"].lower() for row in rows)
    assert len(_read_csv(events)) == 200
    priorities = [float(row["priority_score"]) for row in rows]
    assert len(set(priorities)) > 1
    assert priorities == sorted(priorities, reverse=True)
    expected_order = sorted(
        rows,
        key=lambda row: (
            -float(row["priority_score"]),
            -float(row["local_score"]),
            row["operator_family"],
            row["canonical_signature"],
            row["candidate_id"],
        ),
    )
    assert [row["candidate_id"] for row in rows] == [
        row["candidate_id"] for row in expected_order
    ]
    assert [
        (row["candidate_id"], row["local_score"], row["priority_score"]) for row in rows
    ] == [
        (row["candidate_id"], row["local_score"], row["priority_score"])
        for row in first_rows
    ]
    family_counts = Counter(row["operator_family"] for row in rows)
    scarce_count = min(family_counts.values())
    abundant_count = max(family_counts.values())
    assert scarce_count < abundant_count
    scarce_bonuses = {
        round(float(row["priority_score"]) - float(row["local_score"]), 6)
        for row in rows
        if family_counts[row["operator_family"]] == scarce_count
    }
    abundant_bonuses = {
        round(float(row["priority_score"]) - float(row["local_score"]), 6)
        for row in rows
        if family_counts[row["operator_family"]] == abundant_count
    }
    assert min(scarce_bonuses) > max(abundant_bonuses)
    assert {
        name for name in sys.modules if name == "alpha_mining.platform" or name.startswith("alpha_mining.platform.")
    } == platform_modules_before


def test_failed_csv_history_reduces_candidate_priority(tmp_path: Path) -> None:
    from alpha_mining.offline.service import run_offline_generation
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    cache_dir = tmp_path / "cache"
    queue_path = tmp_path / "queue.csv"
    events_path = tmp_path / "events.csv"
    _write_cache(cache_dir)
    run_offline_generation(
        cache_dir=cache_dir,
        queue_path=queue_path,
        events_path=events_path,
        count=100,
    )
    before = {row["candidate_id"]: float(row["priority_score"]) for row in _read_csv(queue_path)}
    target_id = next(iter(before))
    queue = CandidateCsvQueue(queue_path, events_path)
    with queue.writer():
        queue.transition(target_id, "FAILED", "offline validation failed")

    run_offline_generation(
        cache_dir=cache_dir,
        queue_path=queue_path,
        events_path=events_path,
        count=100,
    )
    after = {row["candidate_id"]: float(row["priority_score"]) for row in _read_csv(queue_path)}

    assert after[target_id] <= before[target_id] - 0.85


def test_missing_cache_stops_with_sync_instruction(tmp_path: Path, capsys) -> None:
    from alpha_mining.offline.cli import main

    result = main(
        [
            "--cache-dir",
            str(tmp_path / "missing"),
            "--queue-path",
            str(tmp_path / "queue.csv"),
            "--events-path",
            str(tmp_path / "events.csv"),
        ]
    )

    assert result == 2
    assert "同步平台元数据.py" in capsys.readouterr().err


def test_stale_cache_requires_explicit_continue(tmp_path: Path) -> None:
    from alpha_mining.offline.metadata import MetadataCacheStale
    from alpha_mining.offline.service import run_offline_generation

    cache_dir = tmp_path / "cache"
    _write_cache(cache_dir, stale=True)
    kwargs = {
        "cache_dir": cache_dir,
        "queue_path": tmp_path / "queue.csv",
        "events_path": tmp_path / "events.csv",
        "count": 1,
        "cache_max_age_hours": 24,
    }
    with pytest.raises(MetadataCacheStale):
        run_offline_generation(**kwargs)
    with pytest.warns(UserWarning, match="缓存已过期"):
        summary = run_offline_generation(**kwargs, allow_stale_cache=True)
    assert summary.added == 1


def test_canonical_skeleton_blocks_field_window_and_wrapper_only_variants() -> None:
    from alpha_mining.generation.canonical import canonical_skeleton

    assert canonical_skeleton("rank(ts_delta(field_a, 5))") == canonical_skeleton(
        "zscore(ts_delta(field_b, 63))"
    )


def test_metadata_rejects_unknown_operator_and_field(tmp_path: Path) -> None:
    from alpha_mining.generation.validation import LocalExpressionValidator
    from alpha_mining.offline.metadata import MetadataCache

    cache_dir = tmp_path / "cache"
    _write_cache(cache_dir)
    metadata = MetadataCache.load(cache_dir, max_age_hours=24)
    validator = LocalExpressionValidator(metadata)

    assert "UNKNOWN_OPERATOR" in {issue.code for issue in validator.validate("unknown_op(fixture_close)")}
    assert "UNKNOWN_FIELD" in {issue.code for issue in validator.validate("rank(not_in_cache)")}


def test_generation_reports_an_exhausted_metadata_constrained_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alpha_mining.offline.service as service

    cache_dir = tmp_path / "cache"
    _write_cache(cache_dir)
    monkeypatch.setattr(service, "generate_candidate_pool", lambda metadata: [])

    with pytest.raises(service.OfflineCandidatePoolExhausted, match="目标 1.*实际 0"):
        service.run_offline_generation(
            cache_dir=cache_dir,
            queue_path=tmp_path / "queue.csv",
            events_path=tmp_path / "events.csv",
            count=1,
        )


def test_offline_packages_have_no_platform_or_http_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    sources = [root / "生成Alpha候选.py"]
    sources.extend((root / "alpha_mining" / "offline").glob("*.py"))
    # Note: alpha_mining/generation is a shared module (offline + factory)
    # so we exclude it from strict offline-only checks
    sources.append(root / "alpha_mining" / "storage" / "csv_queue.py")
    text = "\n".join(path.read_text(encoding="utf-8") for path in sources)

    for forbidden in (
        "import requests",
        "from requests",
        "alpha_mining.platform",
        "requests.Session",
        "worldquantbrain.com",
        "WQ_USERNAME",
        "WQ_PASSWORD",
        "Cookie",
        "simulation",
        "submit(",
    ):
        assert forbidden not in text
