"""Read-only platform catalog synchronization used by the factory gate."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from alpha_mining.generation.validation import ExpressionCatalog, LocalExpressionValidator
from alpha_mining.offline.metadata import MetadataCache

CHECKPOINT_DIRNAME = ".alpha_catalog_sync_checkpoint"
CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_MAX_AGE_SECONDS = 12 * 3600
_MANIFEST_KEYS = (
    "checkpoint_schema_version",
    "instrumentType",
    "region",
    "universe",
    "delay",
    "page_size",
    "dataset_ids",
    "dataset_ids_hash",
    "datasets",
    "datasets_hash",
    "started_at",
    "state",
)


class CatalogCheckpointStale(RuntimeError):
    """The checkpoint no longer describes the catalog we are being asked for.

    Raised when the request context changed, when the platform's authoritative
    dataset snapshot moved, or when the checkpoint aged past
    ``CHECKPOINT_MAX_AGE_SECONDS``. Never auto-recovered inside one invocation:
    silently restarting would re-burn ~1900 requests and re-earn the 429.
    """


class CatalogCheckpointInvalid(RuntimeError):
    """The manifest itself is unusable. Reserved for manifest-level damage.

    A damaged single dataset envelope must NOT raise this -- it degrades to
    "refetch that one dataset" instead, otherwise the checkpoint would invent a
    new unrecoverable window.
    """


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _dataset_fingerprint(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order-independent view of the datasets snapshot.

    Sorted by id so platform page ordering cannot fake a STALE verdict. This is
    the full record: it is what ``datasets_hash`` commits to, so the manifest
    stays self-describing and every envelope binds to an exact snapshot.
    """
    return sorted((dict(row) for row in rows), key=lambda row: str(row.get("id") or ""))


# Resume is only refused when a field that bears on *data correctness* moved.
#   id                  -- identity
#   fieldCount          -- a stored envelope may now be incomplete
#   region/universe/delay -- the row no longer answers our request context
#   category/subcategory  -- consumed by MetadataCache (offline/metadata.py:329)
# Everything else is descriptive, scored, or a live platform counter:
# pyramidMultiplier, alphaCount, userCount, valueScore, dateUpdated, coverage,
# dateCoverage, name, description, themes, researchPapers.  Measured on
# 2026-08-09: the platform moved pyramidMultiplier 1.3 -> 1.2 on 6 of 297
# datasets during a ~2h sync while fieldCount held on all 297.  Binding the
# whole record made resume rarer than the failure it exists to survive.
_SNAPSHOT_BOUND_KEYS = (
    "id",
    "fieldCount",
    "region",
    "universe",
    "delay",
    "category",
    "subcategory",
)


def _bound_dataset_fingerprint(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The correctness-bearing projection compared across invocations."""
    return sorted(
        ({key: row[key] for key in _SNAPSHOT_BOUND_KEYS if key in row} for row in rows),
        key=lambda row: str(row.get("id") or ""),
    )


def _write_json_atomic(target: Path, payload: Any) -> None:
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(_stable_json(payload), encoding="utf-8")
    temporary.replace(target)


class CatalogClient(Protocol):
    def list_datasets(self, params: dict[str, object]) -> dict[str, Any]: ...
    def list_data_fields(self, params: dict[str, object]) -> dict[str, Any]: ...
    # /operators is unpaged: the client returns the records directly, not a page.
    def list_operators(self, params: dict[str, object]) -> list[dict[str, Any]]: ...


class ReadOnlyExpressionCatalog(LocalExpressionValidator):
    """Production catalog adapter backed only by a verified local snapshot."""

    def __init__(self, metadata: MetadataCache, *, max_age_hours: float = 168) -> None:
        super().__init__(metadata, max_age_hours=max_age_hours, allow_stale_catalog=False)


def _utc_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


class PlatformCatalogSynchronizer:
    def __init__(self, cache_dir: str | Path, *, page_size: int = 50, resume: bool = False) -> None:
        self.cache_dir = Path(cache_dir)
        # The platform currently rejects catalog pagination above 50.
        self.page_size = max(1, min(50, int(page_size)))
        # Opt-in only. Production keeps the 3273a52 lifecycle byte for byte: no
        # checkpoint is read, written, or left behind when resume is False.
        self.resume = bool(resume)

    # -- checkpoint paths -------------------------------------------------
    def _checkpoint_dir(self) -> Path:
        return self.cache_dir / CHECKPOINT_DIRNAME

    def _manifest_path(self) -> Path:
        return self._checkpoint_dir() / "manifest.json"

    def _fields_dir(self) -> Path:
        return self._checkpoint_dir() / "fields"

    def _envelope_path(self, dataset_id: str) -> Path:
        # Hashed because the platform never promised dataset IDs avoid the
        # characters Windows forbids in filenames (/ : * ? " < > |).
        digest = hashlib.sha256(str(dataset_id).encode("utf-8")).hexdigest()
        return self._fields_dir() / f"{digest}.json"

    def _clear_checkpoint(self) -> None:
        shutil.rmtree(self._checkpoint_dir(), ignore_errors=True)

    # -- manifest ---------------------------------------------------------
    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        self._fields_dir().mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self._manifest_path(), manifest)

    def _load_manifest(self, context: dict[str, Any], now: float) -> dict[str, Any] | None:
        """Validate the manifest before any platform request is issued."""
        path = self._manifest_path()
        if not path.is_file():
            return None
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogCheckpointInvalid(f"catalog checkpoint manifest is unreadable: {exc}") from exc
        if not isinstance(manifest, dict):
            raise CatalogCheckpointInvalid("catalog checkpoint manifest is not an object")
        missing = [key for key in _MANIFEST_KEYS if key not in manifest]
        if missing:
            raise CatalogCheckpointInvalid("catalog checkpoint manifest is missing: " + ", ".join(missing))
        if manifest["checkpoint_schema_version"] != CHECKPOINT_SCHEMA_VERSION:
            raise CatalogCheckpointInvalid(
                f"catalog checkpoint schema {manifest['checkpoint_schema_version']!r} "
                f"!= {CHECKPOINT_SCHEMA_VERSION}"
            )
        dataset_ids = manifest["dataset_ids"]
        datasets = manifest["datasets"]
        if not isinstance(dataset_ids, list) or not all(isinstance(item, str) for item in dataset_ids):
            raise CatalogCheckpointInvalid("catalog checkpoint dataset_ids is not a list of strings")
        if not isinstance(datasets, list) or not all(isinstance(item, dict) for item in datasets):
            raise CatalogCheckpointInvalid("catalog checkpoint datasets is not a list of objects")
        if not dataset_ids:
            raise CatalogCheckpointInvalid("catalog checkpoint has no dataset_ids")
        if not str(manifest["state"] or "").strip():
            raise CatalogCheckpointInvalid("catalog checkpoint has no state")
        try:
            started_at = float(manifest["started_at"])
        except (TypeError, ValueError) as exc:
            raise CatalogCheckpointInvalid("catalog checkpoint started_at is not a timestamp") from exc
        if started_at <= 0:
            raise CatalogCheckpointInvalid("catalog checkpoint started_at is not a timestamp")
        # Self-consistency: the recorded hashes must describe the recorded rows.
        if manifest["dataset_ids_hash"] != _stable_hash(dataset_ids):
            raise CatalogCheckpointInvalid("catalog checkpoint dataset_ids_hash does not match dataset_ids")
        if manifest["datasets_hash"] != _stable_hash(_dataset_fingerprint(datasets)):
            raise CatalogCheckpointInvalid("catalog checkpoint datasets_hash does not match datasets")
        mismatched = [key for key, value in context.items() if manifest.get(key) != value]
        if mismatched:
            raise CatalogCheckpointStale(
                "catalog checkpoint was built for a different request context: " + ", ".join(sorted(mismatched))
            )
        age = now - started_at
        if age > CHECKPOINT_MAX_AGE_SECONDS or age < 0:
            raise CatalogCheckpointStale(f"catalog checkpoint is {age / 3600:.1f} hours old")
        manifest["started_at"] = started_at
        return manifest

    def _verify_snapshot(self, manifest: dict[str, Any], datasets: list[dict[str, Any]], dataset_ids: list[str]) -> None:
        """Bind the checkpoint to the live authoritative datasets snapshot.

        Comparing IDs alone would happily splice a stale checkpoint onto a
        catalog whose dataset structure moved, so the correctness-bearing
        projection (``_SNAPSHOT_BOUND_KEYS``) is compared too. Descriptive and
        continuously re-scored fields are deliberately excluded: the platform
        mutates them faster than a full sync completes, so binding them would
        make the checkpoint expire before the failure it exists to survive.
        """
        if manifest["dataset_ids_hash"] != _stable_hash(dataset_ids):
            raise CatalogCheckpointStale("platform dataset ID set changed since the checkpoint was written")
        stored = _bound_dataset_fingerprint([dict(row) for row in manifest["datasets"]])
        if _stable_hash(stored) != _stable_hash(_bound_dataset_fingerprint(datasets)):
            raise CatalogCheckpointStale("platform dataset metadata changed since the checkpoint was written")

    # -- dataset field envelopes -----------------------------------------
    def _prune_foreign_envelopes(self, dataset_ids: list[str]) -> None:
        directory = self._fields_dir()
        if not directory.is_dir():
            return
        expected = {self._envelope_path(dataset_id).name for dataset_id in dataset_ids}
        for path in directory.iterdir():
            if path.is_file() and path.name not in expected:
                path.unlink(missing_ok=True)

    def _read_envelope(
        self, dataset_id: str, *, context_hash: str, snapshot_hash: str
    ) -> list[dict[str, Any]] | None:
        """Return the dataset's rows when the envelope proves itself complete.

        Any defect returns None after deleting the file: the dataset is simply
        refetched. This is deliberately never CatalogCheckpointInvalid.
        """
        path = self._envelope_path(dataset_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return None
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("checkpoint_schema_version") != CHECKPOINT_SCHEMA_VERSION
            or payload.get("dataset_id") != dataset_id
            or payload.get("context_hash") != context_hash
            or payload.get("dataset_snapshot_hash") != snapshot_hash
            or not isinstance(rows, list)
            or not all(isinstance(row, dict) for row in rows)
            or payload.get("row_count") != len(rows)
            or payload.get("rows_hash") != _stable_hash(rows)
        ):
            path.unlink(missing_ok=True)
            return None
        return [dict(row) for row in rows]

    def _write_envelope(
        self, dataset_id: str, rows: list[dict[str, Any]], *, context_hash: str, snapshot_hash: str
    ) -> None:
        self._fields_dir().mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            self._envelope_path(dataset_id),
            {
                "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
                "dataset_id": dataset_id,
                "context_hash": context_hash,
                "dataset_snapshot_hash": snapshot_hash,
                "row_count": len(rows),
                "rows_hash": _stable_hash(rows),
                "rows": rows,
            },
        )

    def _all_pages(self, fetch: Any, params: dict[str, object]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        expected: int | None = None
        while True:
            payload = fetch({**params, "limit": self.page_size, "offset": offset})
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("catalog response is not a paged object")
            count = int(payload.get("count") or 0)
            if expected is None:
                expected = count
            elif count != expected:
                raise ValueError("catalog count changed during pagination")
            batch = [item for item in payload["results"] if isinstance(item, dict)]
            rows.extend(batch)
            if len(rows) >= count:
                return rows[:count]
            if not batch:
                raise ValueError("catalog returned an empty incomplete page")
            offset += len(batch)

    def _operator_rows(self, client: CatalogClient, params: dict[str, object]) -> list[dict[str, Any]]:
        """Collect operator records from the unpaged /operators endpoint.

        A legacy paged object is still tolerated so existing contracts keep
        working; every other shape, and any non-object entry, fails closed.
        """
        payload = client.list_operators(dict(params))
        if isinstance(payload, dict):
            rows = payload.get("results")
            if not isinstance(rows, list):
                raise ValueError("operators catalog object has no results list")
        elif isinstance(payload, list):
            rows = payload
        else:
            raise ValueError("operators catalog response is neither an array nor an object")
        if not rows:
            raise ValueError("platform returned no operator metadata")
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("operators catalog contains a non-object entry")
        return list(rows)

    def sync(self, client: CatalogClient, *, region: str, universe: str, delay: int) -> dict[str, int]:
        base = {"instrumentType": "EQUITY", "region": region, "universe": universe, "delay": int(delay)}
        context = {**base, "page_size": self.page_size}
        context_hash = _stable_hash(context)
        started_at = _utc_timestamp()

        # Manifest validation runs before any request, so a STALE/INVALID
        # checkpoint costs zero platform quota.
        manifest = self._load_manifest(context, started_at) if self.resume else None

        datasets = self._all_pages(client.list_datasets, base)
        dataset_ids = [str(item.get("id") or "").strip() for item in datasets]
        dataset_ids = list(dict.fromkeys(item for item in dataset_ids if item))
        if not dataset_ids:
            raise ValueError("platform returned no dataset IDs")

        if manifest is None:
            manifest = {
                "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
                **context,
                "dataset_ids": dataset_ids,
                "dataset_ids_hash": _stable_hash(dataset_ids),
                "datasets": datasets,
                "datasets_hash": _stable_hash(_dataset_fingerprint(datasets)),
                "started_at": started_at,
                "state": "DATASETS_DONE",
            }
            if self.resume:
                self._write_manifest(manifest)
        else:
            self._verify_snapshot(manifest, datasets, dataset_ids)
            # The manifest is the authority once it validates, so a resumed run
            # is deterministic even if the platform reordered its pages.
            datasets = [dict(row) for row in manifest["datasets"]]
            dataset_ids = list(manifest["dataset_ids"])
            started_at = float(manifest["started_at"])

        snapshot_hash = str(manifest["datasets_hash"])
        if self.resume:
            self._prune_foreign_envelopes(dataset_ids)

        fields: list[dict[str, Any]] = []
        for dataset_id in dataset_ids:
            rows: list[dict[str, Any]] | None = None
            if self.resume:
                rows = self._read_envelope(dataset_id, context_hash=context_hash, snapshot_hash=snapshot_hash)
            if rows is None:
                rows = self._all_pages(client.list_data_fields, {**base, "dataset.id": dataset_id})
                if self.resume:
                    self._write_envelope(
                        dataset_id, rows, context_hash=context_hash, snapshot_hash=snapshot_hash
                    )
            for row in rows:
                if str(row.get("id") or "").strip():
                    fields.append({**row, "_ds": dataset_id})
        if not fields:
            raise ValueError("platform returned no data fields")
        # Operators are NOT paged: the platform returns one top-level array, so
        # _all_pages (which requires count/results) must not be used here.
        # Datasets and data-fields keep the strict paged contract above.
        operators = self._operator_rows(client, base)
        operator_records = [_normalise_operator_record(item) for item in operators]
        if any(record is None for record in operator_records):
            raise ValueError("platform operator metadata has no verifiable arity")
        normalised_operators = [record for record in operator_records if record is not None]
        names = [record["name"] for record in normalised_operators]
        names = list(dict.fromkeys(item for item in names if item))
        if not names:
            raise ValueError("platform returned no operator metadata")
        # Freshness reflects the oldest page in the snapshot. A checkpoint that
        # spans several rate-limit windows must not present itself as "fetched
        # just now" the instant it finalizes.
        now = started_at if self.resume else _utc_timestamp()
        cache_context = {"cached_at": now, "region": region, "universe": universe, "delay": int(delay), "source": "platform_catalog"}
        payloads = {
            ".alpha_datasets_cache.json": {**cache_context, "dataset_ids": dataset_ids, "records": datasets},
            ".alpha_datafields_cache.json": {**cache_context, "rows": fields},
            ".alpha_operators_cache.json": {**cache_context, "operators": names, "records": normalised_operators},
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for filename, payload in payloads.items():
            target = self.cache_dir / filename
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            temporary.replace(target)
        # Only once the full cache set landed. A finalization failure keeps the
        # checkpoint so the next resume re-runs operators/finalization alone.
        if self.resume:
            self._clear_checkpoint()
        return {"datasets": len(dataset_ids), "data_fields": len(fields), "operators": len(names)}


def _normalise_operator_record(row: dict[str, Any]) -> dict[str, Any] | None:
    name = str(row.get("name") or row.get("id") or "").strip().lower()
    signature = str(row.get("signature") or row.get("definition") or "").strip()
    if not name:
        return None
    try:
        arity = int(row["arity"])
    except (KeyError, TypeError, ValueError):
        arity = _arity_from_signature(signature)
    if arity is None or arity < 0:
        return None
    return {
        "name": name,
        "signature": signature or f"{name}({','.join('x' for _ in range(arity))})",
        "arity": arity,
        "description": str(row.get("description") or ""),
    }


def _arity_from_signature(signature: str) -> int | None:
    match = re.fullmatch(r"[^()]+\(([^()]*)\)", signature.strip())
    if not match:
        return None
    arguments = match.group(1).strip()
    if not arguments:
        return 0
    if any(token in arguments for token in ("...", "*", "[", "]")):
        return None
    return len([part for part in arguments.split(",") if part.strip()])
