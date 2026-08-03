"""Strict reader for locally synchronized platform metadata."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MetadataCacheError(ValueError):
    """The local metadata cache is absent or internally inconsistent."""


class MetadataCacheMissing(MetadataCacheError):
    pass


class MetadataCacheStale(MetadataCacheError):
    pass


@dataclass(frozen=True)
class OperatorMetadata:
    name: str
    signature: str
    arity: int
    description: str


@dataclass(frozen=True)
class FieldMetadata:
    field_id: str
    dataset_id: str
    field_type: str
    category: str
    description: str


@dataclass(frozen=True)
class DatasetMetadata:
    dataset_id: str
    name: str
    category: str


@dataclass(frozen=True)
class MetadataCache:
    cache_dir: Path
    operators: dict[str, OperatorMetadata]
    fields: dict[str, FieldMetadata]
    datasets: dict[str, DatasetMetadata]
    info: dict[str, Any]

    @classmethod
    def load(
        cls,
        cache_dir: Path | str,
        *,
        max_age_hours: float = 168,
        allow_stale: bool = False,
    ) -> "MetadataCache":
        root = Path(cache_dir)
        names = {
            "operators": "操作符.json",
            "data_fields": "数据字段.json",
            "datasets": "数据集.json",
            "info": "缓存信息.json",
        }
        missing = [name for name in names.values() if not (root / name).is_file()]
        if missing:
            raise MetadataCacheMissing(f"平台元数据缓存缺失: {', '.join(missing)}")

        payloads: dict[str, Any] = {}
        for key, filename in names.items():
            try:
                payloads[key] = json.loads((root / filename).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise MetadataCacheError(f"无法读取平台元数据缓存 {filename}: {exc}") from exc

        info = _mapping(payloads["info"], "缓存信息.json")
        expected_schema = str(info.get("schema_version") or "")
        if not expected_schema:
            raise MetadataCacheError("缓存信息缺少 schema_version")

        records: dict[str, list[dict[str, Any]]] = {}
        for key in ("operators", "data_fields", "datasets"):
            document = _mapping(payloads[key], names[key])
            if str(document.get("schema_version") or "") != expected_schema:
                raise MetadataCacheError(f"{names[key]} schema_version 与缓存信息不一致")
            raw_records = document.get("records")
            if not isinstance(raw_records, list) or not all(isinstance(row, dict) for row in raw_records):
                raise MetadataCacheError(f"{names[key]} records 必须为对象数组")
            records[key] = raw_records

        counts = _mapping(info.get("record_counts"), "缓存信息.record_counts")
        for key, rows in records.items():
            if int(counts.get(key, -1)) != len(rows):
                raise MetadataCacheError(f"{key} 记录数量与缓存信息不一致")

        hashed = {key: payloads[key] for key in ("operators", "data_fields", "datasets")}
        actual_hash = hashlib.sha256(
            json.dumps(hashed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if str(info.get("content_hash") or "") != actual_hash:
            raise MetadataCacheError("平台元数据缓存 content_hash 校验失败")

        fetched_at = _parse_time(str(info.get("fetched_at") or ""))
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        if age_hours > float(max_age_hours) and not allow_stale:
            raise MetadataCacheStale(
                f"平台元数据缓存已过期: {age_hours:.1f} 小时，限制 {max_age_hours:g} 小时"
            )

        operators: dict[str, OperatorMetadata] = {}
        for row in records["operators"]:
            name = _required_text(row, "name", "操作符")
            signature = _required_text(row, "signature", name)
            try:
                arity = int(row["arity"])
            except (KeyError, TypeError, ValueError) as exc:
                raise MetadataCacheError(f"操作符 {name} 缺少有效 arity") from exc
            if arity < 0:
                raise MetadataCacheError(f"操作符 {name} 的 arity 无效")
            operators[name.lower()] = OperatorMetadata(
                name=name.lower(), signature=signature, arity=arity,
                description=str(row.get("description") or ""),
            )

        datasets: dict[str, DatasetMetadata] = {}
        for row in records["datasets"]:
            dataset_id = _required_text(row, "id", "数据集")
            datasets[dataset_id] = DatasetMetadata(
                dataset_id=dataset_id,
                name=str(row.get("name") or dataset_id),
                category=str(row.get("category") or "unknown").lower(),
            )

        fields: dict[str, FieldMetadata] = {}
        for row in records["data_fields"]:
            field_id = _required_text(row, "id", "数据字段")
            dataset_id = _required_text(row, "dataset_id", field_id)
            if dataset_id not in datasets:
                raise MetadataCacheError(f"字段 {field_id} 引用了未知数据集 {dataset_id}")
            field_type = _required_text(row, "type", field_id).upper()
            fields[field_id] = FieldMetadata(
                field_id=field_id,
                dataset_id=dataset_id,
                field_type=field_type,
                category=str(row.get("category") or "unknown").lower(),
                description=str(row.get("description") or ""),
            )
        if not operators or not fields or not datasets:
            raise MetadataCacheError("平台元数据缓存不得为空")
        return cls(root, operators, fields, datasets, info)

    @classmethod
    def from_platform_disk_cache(
        cls,
        cache_dir: Path | str,
        *,
        max_age_hours: float = 24,
        allow_stale: bool = False,
    ) -> "MetadataCache":
        """Load the existing three-file platform cache protocol.

        This is intentionally separate from :meth:`load`: four-file Chinese
        JSON snapshots remain supported for offline tools, but production
        generation uses the synchronizer's established ``.alpha_*`` files.
        """

        root = Path(cache_dir)
        names = {
            "datasets": ".alpha_datasets_cache.json",
            "fields": ".alpha_datafields_cache.json",
            "operators": ".alpha_operators_cache.json",
        }
        missing = [filename for filename in names.values() if not (root / filename).is_file()]
        if missing:
            raise MetadataCacheMissing("platform catalog cache missing: " + ", ".join(missing))
        payloads: dict[str, dict[str, Any]] = {}
        for key, filename in names.items():
            try:
                payloads[key] = _mapping(json.loads((root / filename).read_text(encoding="utf-8")), filename)
            except (OSError, json.JSONDecodeError) as exc:
                raise MetadataCacheError(f"cannot read {filename}: {exc}") from exc

        datasets_payload = payloads["datasets"]
        dataset_rows = datasets_payload.get("records") or []
        dataset_ids = [str(value).strip() for value in datasets_payload.get("dataset_ids") or [] if str(value).strip()]
        datasets: dict[str, DatasetMetadata] = {}
        for row in dataset_rows:
            if isinstance(row, dict) and str(row.get("id") or "").strip():
                dataset_id = str(row.get("id")).strip()
                datasets[dataset_id] = DatasetMetadata(
                    dataset_id=dataset_id,
                    name=str(row.get("name") or dataset_id),
                    category=str(row.get("category") or "unknown").lower(),
                )
        for dataset_id in dataset_ids:
            datasets.setdefault(dataset_id, DatasetMetadata(dataset_id, dataset_id, "unknown"))
        if not datasets:
            raise MetadataCacheError("platform dataset cache has no dataset IDs")

        fields: dict[str, FieldMetadata] = {}
        for row in payloads["fields"].get("rows") or []:
            if not isinstance(row, dict):
                continue
            field_id = str(row.get("id") or "").strip()
            nested_dataset = row.get("dataset") if isinstance(row.get("dataset"), dict) else {}
            dataset_id = str(row.get("_ds") or row.get("dataset_id") or nested_dataset.get("id") or "").strip()
            if not field_id or not dataset_id:
                continue
            if dataset_id not in datasets:
                datasets[dataset_id] = DatasetMetadata(dataset_id, dataset_id, "unknown")
            fields[field_id] = FieldMetadata(
                field_id=field_id,
                dataset_id=dataset_id,
                field_type=str(row.get("type") or row.get("dataType") or "UNKNOWN").upper(),
                category=str(row.get("category") or "unknown").lower(),
                description=str(row.get("description") or ""),
            )
        if not fields:
            raise MetadataCacheError("platform data-field cache has no usable rows")

        operator_records = payloads["operators"].get("records")
        if not isinstance(operator_records, list):
            raise MetadataCacheMissing("operator metadata records are unavailable")
        operators: dict[str, OperatorMetadata] = {}
        for row in operator_records:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("id") or "").strip().lower()
            signature = str(row.get("signature") or "").strip()
            arity = _operator_arity(row, signature)
            if not name or arity is None:
                raise MetadataCacheError(f"operator metadata is incomplete for {name or '<unknown>'}")
            operators[name] = OperatorMetadata(name, signature or name, arity, str(row.get("description") or ""))
        if not operators:
            raise MetadataCacheError("platform operator cache has no complete records")

        cached_at = max(float(payload.get("cached_at") or 0.0) for payload in payloads.values())
        if cached_at <= 0:
            raise MetadataCacheError("platform catalog cache has no cached_at timestamp")
        fetched_at = datetime.fromtimestamp(cached_at, timezone.utc)
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        if age_hours > float(max_age_hours) and not allow_stale:
            raise MetadataCacheStale(f"platform catalog cache is stale: {age_hours:.1f} hours")
        context = payloads["operators"]
        info = {
            "fetched_at": fetched_at.isoformat().replace("+00:00", "Z"),
            "region": context.get("region") or payloads["fields"].get("region") or "",
            "universe": context.get("universe") or payloads["fields"].get("universe") or "",
            "delay": context.get("delay") if context.get("delay") is not None else payloads["fields"].get("delay"),
            "source": "platform_catalog",
        }
        return cls(root, operators, fields, datasets, info)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MetadataCacheError(f"{label} 必须为 JSON 对象")
    return value


def _required_text(row: dict[str, Any], key: str, label: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise MetadataCacheError(f"{label} 缺少 {key}")
    return value


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MetadataCacheError("缓存信息 fetched_at 无效") from exc
    if parsed.tzinfo is None:
        raise MetadataCacheError("缓存信息 fetched_at 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _operator_arity(row: dict[str, Any], signature: str) -> int | None:
    try:
        value = int(row["arity"])
    except (KeyError, TypeError, ValueError):
        value = -1
    if value >= 0:
        return value
    if "(" not in signature or not signature.endswith(")"):
        return None
    arguments = signature.split("(", 1)[1][:-1].strip()
    if not arguments:
        return 0
    if any(token in arguments for token in ("...", "*", "[", "]")):
        return None
    return len([part for part in arguments.split(",") if part.strip()])
