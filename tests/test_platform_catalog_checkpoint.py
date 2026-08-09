"""Dataset-boundary checkpoint/resume for PlatformCatalogSynchronizer.

INFRA-REL-001 / CATALOG_SYNC_NONRESUMABLE_UNDER_PLATFORM_RATE_LIMIT.

A full USA/TOP3000/delay=1 catalog sync costs ~1900 read-only requests over
~100 minutes.  Before this feature any failure in the last request discarded
every earlier page, so a platform 429 in minute 90 forced a complete re-run --
which immediately re-earned the 429.  Checkpointing at dataset boundaries lets
the next invocation skip the datasets already collected.

Design invariants under test:

* ``resume=False`` (production default) never reads or writes a checkpoint.
* ``manifest.json`` is written once, immediately after the authoritative
  datasets snapshot, and is immutable afterwards.  It carries no
  ``completed_dataset_ids``.
* A legal ``fields/<sha256(dataset_id)>.json`` envelope is the ONLY authority
  for "this dataset is done", so a crash between the envelope write and any
  later manifest write cannot lose work.
* A damaged single envelope degrades to "refetch that one dataset"; it must
  never escalate into CatalogCheckpointInvalid.
* The three production caches keep the 3273a52 schema exactly.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

CHECKPOINT_DIRNAME = ".alpha_catalog_sync_checkpoint"
CACHE_FILENAMES = (
    ".alpha_datasets_cache.json",
    ".alpha_datafields_cache.json",
    ".alpha_operators_cache.json",
)

DATASET_IDS = ("pv1", "analyst10", "fundamental6")
OPERATOR_ROWS = (
    {"name": "rank", "definition": "rank(x)"},
    {"name": "ts_delta", "definition": "ts_delta(x, d)"},
)


class _Boom(RuntimeError):
    """Stands in for a platform 429 / transport drop / CircuitOpen."""


class _Client:
    """Paged data-sets and data-fields, unpaged operators (3273a52 contract)."""

    def __init__(
        self,
        *,
        dataset_ids: tuple[str, ...] = DATASET_IDS,
        fail_on_dataset: str | None = None,
        dataset_category: str = "model",
    ) -> None:
        self.dataset_ids = dataset_ids
        self.fail_on_dataset = fail_on_dataset
        self.dataset_category = dataset_category
        self.dataset_requests: list[dict[str, object]] = []
        self.field_requests: list[dict[str, object]] = []
        self.operator_requests: list[dict[str, object]] = []

    # -- helpers ---------------------------------------------------------
    def _page(self, rows: list[dict[str, object]], params: dict[str, object]) -> dict[str, object]:
        offset, limit = int(params["offset"]), int(params["limit"])
        return {"count": len(rows), "results": rows[offset : offset + limit]}

    def _dataset_rows(self) -> list[dict[str, object]]:
        return [
            {"id": dataset_id, "name": dataset_id.upper(), "category": self.dataset_category}
            for dataset_id in self.dataset_ids
        ]

    def _field_rows(self, dataset_id: str) -> list[dict[str, object]]:
        return [
            {"id": f"{dataset_id}_f{index}", "type": "MATRIX"}
            for index in range(1, 3)
        ]

    # -- client protocol -------------------------------------------------
    def list_datasets(self, params: dict[str, object]) -> dict[str, object]:
        self.dataset_requests.append(dict(params))
        return self._page(self._dataset_rows(), params)

    def list_data_fields(self, params: dict[str, object]) -> dict[str, object]:
        self.field_requests.append(dict(params))
        dataset_id = str(params["dataset.id"])
        if self.fail_on_dataset is not None and dataset_id == self.fail_on_dataset:
            raise _Boom(f"platform refused data-fields for {dataset_id}")
        return self._page(self._field_rows(dataset_id), params)

    def list_operators(self, params: dict[str, object]) -> list[dict[str, object]]:
        self.operator_requests.append(dict(params))
        return [dict(row) for row in OPERATOR_ROWS]

    # -- assertions helpers ----------------------------------------------
    def requested_datasets(self) -> list[str]:
        return [str(params["dataset.id"]) for params in self.field_requests]


def _synchronizer(cache_dir, *, resume: bool, page_size: int = 50):
    from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

    return PlatformCatalogSynchronizer(cache_dir, page_size=page_size, resume=resume)


def _sync(cache_dir, client, *, resume: bool, page_size: int = 50, universe: str = "TOP3000"):
    return _synchronizer(cache_dir, resume=resume, page_size=page_size).sync(
        client, region="USA", universe=universe, delay=1
    )


def _envelope_path(cache_dir, dataset_id: str):
    digest = hashlib.sha256(dataset_id.encode("utf-8")).hexdigest()
    return cache_dir / CHECKPOINT_DIRNAME / "fields" / f"{digest}.json"


def _manifest_path(cache_dir):
    return cache_dir / CHECKPOINT_DIRNAME / "manifest.json"


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _freeze_clock(monkeypatch, value: float) -> None:
    from alpha_mining.platform import catalog as catalog_module

    monkeypatch.setattr(catalog_module, "_utc_timestamp", lambda: value)


def _cache_bytes(cache_dir) -> dict[str, str]:
    return {
        name: (cache_dir / name).read_text(encoding="utf-8")
        for name in CACHE_FILENAMES
    }


# ---------------------------------------------------------------------------
# 1. deterministic output
# ---------------------------------------------------------------------------
def test_resumed_sync_produces_identical_caches_and_leaves_no_checkpoint(tmp_path, monkeypatch) -> None:
    """A resumed run must be byte-identical to a straight run, clock frozen.

    Also pins the two directory invariants: resume=False never creates the
    checkpoint directory, and a completed resume=True run removes it.
    """
    _freeze_clock(monkeypatch, 1_700_000_000.0)

    straight_dir = tmp_path / "straight"
    straight_dir.mkdir()
    straight = _Client()
    baseline = _sync(straight_dir, straight, resume=False)
    assert not (straight_dir / CHECKPOINT_DIRNAME).exists()

    resumed_dir = tmp_path / "resumed"
    resumed_dir.mkdir()
    with pytest.raises(_Boom):
        _sync(resumed_dir, _Client(fail_on_dataset="analyst10"), resume=True)
    recovered = _sync(resumed_dir, _Client(), resume=True)

    assert recovered == baseline
    assert _cache_bytes(resumed_dir) == _cache_bytes(straight_dir)
    assert not (resumed_dir / CHECKPOINT_DIRNAME).exists()


# ---------------------------------------------------------------------------
# 2. dataset k failure keeps the first k-1 envelopes, writes no cache
# ---------------------------------------------------------------------------
def test_failure_mid_fields_keeps_earlier_envelopes_and_writes_no_cache(tmp_path) -> None:
    client = _Client(fail_on_dataset="analyst10")
    with pytest.raises(_Boom):
        _sync(tmp_path, client, resume=True)

    assert _envelope_path(tmp_path, "pv1").is_file()
    assert not _envelope_path(tmp_path, "analyst10").exists()
    assert not _envelope_path(tmp_path, "fundamental6").exists()
    assert _manifest_path(tmp_path).is_file()
    for name in CACHE_FILENAMES:
        assert not (tmp_path / name).exists()


def test_failure_without_resume_writes_no_checkpoint_at_all(tmp_path) -> None:
    """Production default must not start littering checkpoint directories."""
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=False)

    assert not (tmp_path / CHECKPOINT_DIRNAME).exists()


# ---------------------------------------------------------------------------
# 3. resume issues zero requests for completed datasets
# ---------------------------------------------------------------------------
def test_resume_does_not_refetch_completed_datasets(tmp_path) -> None:
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    second = _Client()
    result = _sync(tmp_path, second, resume=True)

    assert "pv1" not in second.requested_datasets()
    assert set(second.requested_datasets()) == {"analyst10", "fundamental6"}
    assert result["data_fields"] == 6


def test_resume_after_operator_failure_refetches_no_fields(tmp_path) -> None:
    """run#4 shape: every field page succeeded, /operators killed the run."""

    class _OperatorFailureClient(_Client):
        def list_operators(self, params):
            self.operator_requests.append(dict(params))
            raise _Boom("operators protocol mismatch")

    with pytest.raises(_Boom):
        _sync(tmp_path, _OperatorFailureClient(), resume=True)

    for dataset_id in DATASET_IDS:
        assert _envelope_path(tmp_path, dataset_id).is_file()

    second = _Client()
    result = _sync(tmp_path, second, resume=True)

    assert second.field_requests == []
    assert second.operator_requests
    assert result == {"datasets": 3, "data_fields": 6, "operators": 2}


# ---------------------------------------------------------------------------
# 4. envelope-write crash window
# ---------------------------------------------------------------------------
def test_envelope_alone_proves_completion_when_manifest_never_updated(tmp_path) -> None:
    """The crash window that made completed_dataset_ids unsafe.

    The envelope lands, then the process dies before any further manifest
    write.  Resume must treat the dataset as done -- and the manifest must be
    byte-identical, proving nothing tracks completion inside it.
    """
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    manifest_before = _manifest_path(tmp_path).read_text(encoding="utf-8")
    manifest_payload = json.loads(manifest_before)
    assert "completed_dataset_ids" not in manifest_payload
    assert "updated_at" not in manifest_payload

    second = _Client()
    _sync(tmp_path, second, resume=True)

    assert "pv1" not in second.requested_datasets()
    # The completed run removes the checkpoint, so compare the captured bytes
    # against a fresh checkpoint built from the same frozen inputs instead.
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    with pytest.raises(_Boom):
        _sync(replay_dir, _Client(fail_on_dataset="analyst10"), resume=True)
    replayed = json.loads(_manifest_path(replay_dir).read_text(encoding="utf-8"))
    assert {key: replayed[key] for key in replayed if key != "started_at"} == {
        key: manifest_payload[key] for key in manifest_payload if key != "started_at"
    }


# ---------------------------------------------------------------------------
# 5. corrupted envelope refetches only its own dataset
# ---------------------------------------------------------------------------
def test_bad_rows_hash_refetches_only_that_dataset(tmp_path) -> None:
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="fundamental6"), resume=True)

    target = _envelope_path(tmp_path, "pv1")
    payload = _read_json(target)
    payload["rows_hash"] = "0" * 64
    target.write_text(json.dumps(payload), encoding="utf-8")

    second = _Client()
    result = _sync(tmp_path, second, resume=True)

    requested = second.requested_datasets()
    assert "pv1" in requested
    assert "analyst10" not in requested
    assert result["data_fields"] == 6
    assert not (tmp_path / CHECKPOINT_DIRNAME).exists()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda payload: payload.__setitem__("row_count", 99), id="row_count"),
        pytest.param(lambda payload: payload.__setitem__("checkpoint_schema_version", 999), id="schema_version"),
        pytest.param(lambda payload: payload.__setitem__("context_hash", "0" * 64), id="context_hash"),
        pytest.param(lambda payload: payload.__setitem__("dataset_snapshot_hash", "0" * 64), id="snapshot_hash"),
        pytest.param(lambda payload: payload.__setitem__("dataset_id", "analyst10"), id="dataset_id_mismatch"),
        pytest.param(lambda payload: payload.pop("rows"), id="missing_rows"),
    ],
)
def test_damaged_envelope_degrades_to_refetch_never_invalid(tmp_path, mutate) -> None:
    from alpha_mining.platform.catalog import CatalogCheckpointInvalid

    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="fundamental6"), resume=True)

    target = _envelope_path(tmp_path, "pv1")
    payload = _read_json(target)
    mutate(payload)
    target.write_text(json.dumps(payload), encoding="utf-8")

    second = _Client()
    try:
        result = _sync(tmp_path, second, resume=True)
    except CatalogCheckpointInvalid as exc:  # pragma: no cover - guarded below
        pytest.fail(f"single damaged envelope escalated to CatalogCheckpointInvalid: {exc}")

    assert "pv1" in second.requested_datasets()
    assert result["data_fields"] == 6


def test_unparseable_envelope_degrades_to_refetch(tmp_path) -> None:
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="fundamental6"), resume=True)

    _envelope_path(tmp_path, "pv1").write_text("{not json", encoding="utf-8")

    second = _Client()
    result = _sync(tmp_path, second, resume=True)

    assert "pv1" in second.requested_datasets()
    assert result["data_fields"] == 6


# ---------------------------------------------------------------------------
# 6. orphan envelope removal
# ---------------------------------------------------------------------------
def test_orphan_envelope_is_deleted_and_never_reaches_the_cache(tmp_path) -> None:
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    orphan = _envelope_path(tmp_path, "retired_dataset")
    orphan.write_text(
        json.dumps(
            {
                "checkpoint_schema_version": 1,
                "dataset_id": "retired_dataset",
                "context_hash": "0" * 64,
                "dataset_snapshot_hash": "0" * 64,
                "row_count": 1,
                "rows_hash": "0" * 64,
                "rows": [{"id": "retired_field"}],
            }
        ),
        encoding="utf-8",
    )

    result = _sync(tmp_path, _Client(), resume=True)

    assert result["data_fields"] == 6
    rows = _read_json(tmp_path / ".alpha_datafields_cache.json")["rows"]
    assert all(row["_ds"] in DATASET_IDS for row in rows)
    assert not orphan.exists()


def test_unexpected_checkpoint_filename_is_pruned(tmp_path) -> None:
    """A filename that is not sha256(dataset_id) for any current dataset."""
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    stray = tmp_path / CHECKPOINT_DIRNAME / "fields" / "not-a-digest.json"
    stray.write_text("{}", encoding="utf-8")

    _sync(tmp_path, _Client(), resume=True)

    assert not stray.exists()


# ---------------------------------------------------------------------------
# 7. context / page_size mismatch -> STALE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"page_size": 20}, id="page_size"),
        pytest.param({"universe": "TOP1000"}, id="universe"),
    ],
)
def test_context_mismatch_is_stale_and_issues_no_field_requests(tmp_path, kwargs) -> None:
    from alpha_mining.platform.catalog import CatalogCheckpointStale

    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    second = _Client()
    with pytest.raises(CatalogCheckpointStale):
        _sync(tmp_path, second, resume=True, **kwargs)

    assert second.field_requests == []
    assert _manifest_path(tmp_path).is_file()


def test_stale_checkpoint_is_not_auto_restarted_in_the_same_invocation(tmp_path) -> None:
    """A STALE checkpoint must abort, not silently re-burn ~1900 requests."""
    from alpha_mining.platform.catalog import CatalogCheckpointStale

    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    second = _Client()
    with pytest.raises(CatalogCheckpointStale):
        _sync(tmp_path, second, resume=True, universe="TOP1000")

    for name in CACHE_FILENAMES:
        assert not (tmp_path / name).exists()
    assert _envelope_path(tmp_path, "pv1").is_file()


# ---------------------------------------------------------------------------
# 8. same dataset IDs, changed dataset metadata -> STALE
# ---------------------------------------------------------------------------
def test_same_dataset_ids_with_changed_metadata_is_stale(tmp_path) -> None:
    from alpha_mining.platform.catalog import CatalogCheckpointStale

    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    changed = _Client(dataset_category="analyst")
    with pytest.raises(CatalogCheckpointStale):
        _sync(tmp_path, changed, resume=True)

    assert changed.field_requests == []


def test_changed_dataset_id_set_is_stale(tmp_path) -> None:
    from alpha_mining.platform.catalog import CatalogCheckpointStale

    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    with pytest.raises(CatalogCheckpointStale):
        _sync(tmp_path, _Client(dataset_ids=("pv1", "analyst10")), resume=True)


# ---------------------------------------------------------------------------
# 9. age > 12h -> STALE
# ---------------------------------------------------------------------------
def test_checkpoint_older_than_twelve_hours_is_stale(tmp_path, monkeypatch) -> None:
    from alpha_mining.platform.catalog import CatalogCheckpointStale

    start = 1_700_000_000.0
    _freeze_clock(monkeypatch, start)
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    _freeze_clock(monkeypatch, start + 12 * 3600 + 1)
    late = _Client()
    with pytest.raises(CatalogCheckpointStale):
        _sync(tmp_path, late, resume=True)
    assert late.field_requests == []

    _freeze_clock(monkeypatch, start + 11 * 3600)
    in_window = _Client()
    _sync(tmp_path, in_window, resume=True)
    assert "pv1" not in in_window.requested_datasets()


def test_final_cache_cached_at_uses_checkpoint_started_at(tmp_path, monkeypatch) -> None:
    """Freshness must reflect the oldest page in the snapshot, not finalize time."""
    start = 1_700_000_000.0
    _freeze_clock(monkeypatch, start)
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    _freeze_clock(monkeypatch, start + 6 * 3600)
    _sync(tmp_path, _Client(), resume=True)

    for name in CACHE_FILENAMES:
        assert _read_json(tmp_path / name)["cached_at"] == start


def test_non_resume_cache_keeps_finalize_timestamp(tmp_path, monkeypatch) -> None:
    """Production default keeps 3273a52 semantics exactly."""
    _freeze_clock(monkeypatch, 1_700_000_500.0)
    _sync(tmp_path, _Client(), resume=False)

    assert _read_json(tmp_path / ".alpha_datasets_cache.json")["cached_at"] == 1_700_000_500.0


# ---------------------------------------------------------------------------
# 10. bad manifest -> INVALID
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda path: path.write_text("{not json", encoding="utf-8"), id="unparseable"),
        pytest.param(lambda path: path.write_text("[]", encoding="utf-8"), id="not_an_object"),
    ],
)
def test_unreadable_manifest_is_invalid(tmp_path, write) -> None:
    from alpha_mining.platform.catalog import CatalogCheckpointInvalid

    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    write(_manifest_path(tmp_path))

    client = _Client()
    with pytest.raises(CatalogCheckpointInvalid):
        _sync(tmp_path, client, resume=True)
    assert client.dataset_requests == []
    assert client.field_requests == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda payload: payload.__setitem__("checkpoint_schema_version", 999), id="schema_version"),
        pytest.param(lambda payload: payload.pop("dataset_ids"), id="missing_dataset_ids"),
        pytest.param(lambda payload: payload.pop("datasets_hash"), id="missing_datasets_hash"),
        pytest.param(lambda payload: payload.pop("started_at"), id="missing_started_at"),
        pytest.param(lambda payload: payload.__setitem__("dataset_ids_hash", "0" * 64), id="self_inconsistent"),
        pytest.param(lambda payload: payload.__setitem__("started_at", "not-a-number"), id="bad_started_at"),
    ],
)
def test_malformed_manifest_schema_is_invalid(tmp_path, mutate) -> None:
    from alpha_mining.platform.catalog import CatalogCheckpointInvalid

    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    path = _manifest_path(tmp_path)
    payload = _read_json(path)
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CatalogCheckpointInvalid):
        _sync(tmp_path, _Client(), resume=True)


def test_invalid_checkpoint_is_not_auto_restarted_in_the_same_invocation(tmp_path) -> None:
    from alpha_mining.platform.catalog import CatalogCheckpointInvalid

    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    _manifest_path(tmp_path).write_text("{not json", encoding="utf-8")

    with pytest.raises(CatalogCheckpointInvalid):
        _sync(tmp_path, _Client(), resume=True)

    for name in CACHE_FILENAMES:
        assert not (tmp_path / name).exists()
    # Operator recovery is the caller's explicit decision: clear and re-run.
    (tmp_path / CHECKPOINT_DIRNAME / "manifest.json").unlink()
    (tmp_path / CHECKPOINT_DIRNAME / "fields" / "stale.json").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 11. finalization failure after the first cache file
# ---------------------------------------------------------------------------
def test_finalization_failure_keeps_checkpoint_and_downstream_rejects_partial_cache(tmp_path) -> None:
    from alpha_mining.offline.metadata import MetadataCache, MetadataCacheMissing

    blocker = tmp_path / ".alpha_datafields_cache.json"
    blocker.mkdir()  # replace() onto a directory fails -> finalization aborts

    first = _Client()
    with pytest.raises(OSError):
        _sync(tmp_path, first, resume=True)

    assert _manifest_path(tmp_path).is_file()
    for dataset_id in DATASET_IDS:
        assert _envelope_path(tmp_path, dataset_id).is_file()
    assert (tmp_path / ".alpha_datasets_cache.json").is_file()
    assert not (tmp_path / ".alpha_operators_cache.json").exists()

    with pytest.raises(MetadataCacheMissing):
        MetadataCache.from_platform_disk_cache(tmp_path, allow_stale=True)

    blocker.rmdir()
    second = _Client()
    result = _sync(tmp_path, second, resume=True)

    assert second.field_requests == []
    assert result == {"datasets": 3, "data_fields": 6, "operators": 2}
    assert not (tmp_path / CHECKPOINT_DIRNAME).exists()
    cache = MetadataCache.from_platform_disk_cache(tmp_path, allow_stale=True)
    assert set(cache.datasets) == set(DATASET_IDS)


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------
def test_envelope_filename_is_hashed_not_raw_dataset_id(tmp_path) -> None:
    """Raw IDs are not path-safe; the platform never promised they would be."""
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    names = [path.name for path in (tmp_path / CHECKPOINT_DIRNAME / "fields").iterdir()]
    assert names == [f"{hashlib.sha256(b'pv1').hexdigest()}.json"]
    assert all(re.fullmatch(r"[0-9a-f]{64}\.json", name) for name in names)

    payload = _read_json(_envelope_path(tmp_path, "pv1"))
    assert payload["dataset_id"] == "pv1"
    assert payload["row_count"] == len(payload["rows"])


def test_manifest_inlines_datasets_and_has_no_separate_datasets_file(tmp_path) -> None:
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)

    assert not (tmp_path / CHECKPOINT_DIRNAME / "datasets.json").exists()
    manifest = _read_json(_manifest_path(tmp_path))
    assert [row["id"] for row in manifest["datasets"]] == list(DATASET_IDS)
    assert manifest["dataset_ids"] == list(DATASET_IDS)
    assert manifest["region"] == "USA"
    assert manifest["universe"] == "TOP3000"
    assert manifest["delay"] == 1
    assert manifest["page_size"] == 50
    assert manifest["instrumentType"] == "EQUITY"


def test_checkpoint_writes_nothing_into_the_platform_access_database(tmp_path) -> None:
    """Checkpoint state is filesystem-only; it must not touch any sqlite file."""
    with pytest.raises(_Boom):
        _sync(tmp_path, _Client(fail_on_dataset="analyst10"), resume=True)
    _sync(tmp_path, _Client(), resume=True)

    assert list(tmp_path.rglob("*.sqlite")) == []
    assert list(tmp_path.rglob("*.db")) == []


def test_default_resume_is_false(tmp_path) -> None:
    from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

    assert PlatformCatalogSynchronizer(tmp_path).resume is False
    assert PlatformCatalogSynchronizer(tmp_path, page_size=100).page_size == 50
