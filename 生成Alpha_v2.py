#!/usr/bin/env python3
"""
Alpha生成脚本 v2（参考 v50 架构）

功能：
1. 使用 ConsultantGenerator 生成候选（稳定、快速）
2. 轮次去重（field_skeleton），全局去重（exact_hash）
3. 优化策略：动态调整主题/家族权重
4. 输出CSV：待提交Alpha列表.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import random
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@dataclass(frozen=True)
class AlphaCandidate:
    candidate_id: str
    topic_id: str
    hypothesis_id: str
    research_family: str
    strategy_family: str
    mutation_type: str
    mechanism: str
    dataset: str
    expression: str
    exact_hash: str
    parameter_skeleton: str
    field_skeleton: str


def load_research_specs(database: Path) -> list[dict]:
    """从数据库加载活跃的研究规范"""
    try:
        with sqlite3.connect(database) as con:
            rows = con.execute(
                """SELECT h.hypothesis_id, COALESCE(t.topic_id,''), COALESCE(t.category,''),
                          COALESCE(h.mechanism,h.statement_en,''),
                          COALESCE(h.horizon,'medium'), m.data_field, COALESCE(m.dataset_id,'')
                   FROM hypotheses h
                   JOIN research_topics t ON t.topic_id=h.topic_id
                   JOIN data_mappings m ON m.hypothesis_id=h.hypothesis_id
                   WHERE COALESCE(h.status,'active')='active' AND COALESCE(t.active,1)=1
                   ORDER BY h.created_at, h.hypothesis_id"""
            ).fetchall()
    except Exception as exc:
        print(f"[生成] ✗ 数据库读取失败: {exc}")
        return []

    grouped: dict[str, dict] = {}
    for row in rows:
        hid = str(row[0])
        if hid not in grouped:
            grouped[hid] = {
                "hypothesis_id": hid,
                "topic_id": str(row[1]),
                "family": str(row[2]),
                "mechanism": str(row[3]),
                "horizon": str(row[4]),
                "fields": [],
                "dataset": "",
            }
        if row[5]:
            grouped[hid]["fields"].append(str(row[5]))
        if row[6] and not grouped[hid]["dataset"]:
            grouped[hid]["dataset"] = str(row[6])

    return [
        {**spec, "fields": tuple(spec["fields"][:10])}
        for spec in grouped.values()
        if spec["fields"]
    ]


def classify_strategy_family(mechanism: str, family: str) -> str:
    """分类策略家族"""
    keyword_groups = {
        "momentum": ("momentum", "trend", "growth"),
        "reversal": ("reversal", "mean reversion", "contrarian"),
        "volatility": ("volatility", "risk"),
        "fundamental": ("fundamental", "value", "quality", "profitability"),
    }
    for text in (mechanism, family):
        normalized = " ".join(str(text or "").lower().replace("_", " ").split())
        for category, keywords in keyword_groups.items():
            if any(kw in normalized for kw in keywords):
                return category
    return "balanced"


def generate_batch(
    specs: list[dict],
    *,
    limit: int,
    global_seen_hashes: set[str],
    strategy_weights: dict[str, float],
    rng: random.Random,
) -> tuple[list[AlphaCandidate], dict[str, int]]:
    """生成一批候选"""
    from alpha_mining.domain.expression_normalization import expression_identity
    from alpha_mining.generator.consultant_generator import ConsultantGenerator

    generator = ConsultantGenerator(max_per_hypothesis=8, max_same_behavior=2)

    # 按策略家族分组
    family_buckets: dict[str, list[dict]] = defaultdict(list)
    for spec in specs:
        sf = classify_strategy_family(spec["mechanism"], spec["family"])
        family_buckets[sf].append(spec)

    # 按权重排序
    families_in_order = sorted(
        family_buckets.keys(),
        key=lambda f: -strategy_weights.get(f, 1.0),
    )

    # 洗牌每个桶
    for bucket in family_buckets.values():
        rng.shuffle(bucket)

    # 轮次去重
    round_seen_skeletons: set[str] = set()
    rejected_by_reason: dict[str, int] = defaultdict(int)
    accepted: list[AlphaCandidate] = []

    family_index = 0
    attempts = 0
    max_attempts = limit * 30 + len(specs) * 20

    while len(accepted) < limit and attempts < max_attempts:
        attempts += 1
        if not families_in_order:
            break

        sf = families_in_order[family_index % len(families_in_order)]
        family_index += 1
        bucket = family_buckets.get(sf, [])
        if not bucket:
            continue

        spec = bucket[attempts % len(bucket)]

        try:
            candidates = generator.generate(
                hypothesis_id=spec["hypothesis_id"],
                family=spec["family"],
                mechanism=spec["mechanism"],
                horizon=spec["horizon"],
                fields=spec["fields"],
            )
        except Exception:
            continue

        if not candidates:
            continue

        rng.shuffle(candidates)
        for candidate in candidates:
            if len(accepted) >= limit:
                break

            try:
                identity = expression_identity(candidate.expression)
            except Exception:
                rejected_by_reason["INVALID_IDENTITY"] += 1
                continue

            # 全局exact_hash去重（跨轮次）
            if identity.exact_hash in global_seen_hashes:
                rejected_by_reason["EXACT_HASH_EXISTS"] += 1
                continue

            # 轮次field_skeleton去重
            if identity.field_skeleton in round_seen_skeletons:
                rejected_by_reason["FIELD_SKELETON_ROUND_LIMIT"] += 1
                continue

            round_seen_skeletons.add(identity.field_skeleton)
            global_seen_hashes.add(identity.exact_hash)

            strategy_family = classify_strategy_family(spec["mechanism"], spec["family"])
            cid = hashlib.sha256(
                f"{spec['hypothesis_id']}_{identity.exact_hash}".encode()
            ).hexdigest()[:24]

            accepted.append(
                AlphaCandidate(
                    candidate_id=f"candidate_{cid}",
                    topic_id=spec["topic_id"],
                    hypothesis_id=spec["hypothesis_id"],
                    research_family=spec["family"],
                    strategy_family=strategy_family,
                    mutation_type=candidate.mutation_type,
                    mechanism=spec["mechanism"],
                    dataset=spec["dataset"],
                    expression=candidate.expression,
                    exact_hash=identity.exact_hash,
                    parameter_skeleton=identity.parameter_skeleton,
                    field_skeleton=identity.field_skeleton,
                )
            )

    return accepted, dict(rejected_by_reason)


def update_strategy_weights(
    current_weights: dict[str, float],
    batch: list[AlphaCandidate],
    learning_rate: float = 0.1,
) -> dict[str, float]:
    """根据生成效果动态调整策略权重"""
    if not batch:
        return current_weights

    # 统计每个策略家族的生成数量
    family_counts = defaultdict(int)
    for candidate in batch:
        family_counts[candidate.strategy_family] += 1

    # 提升生成少的家族权重（探索）
    new_weights = current_weights.copy()
    total_count = len(batch)
    for family, count in family_counts.items():
        ratio = count / total_count
        # 如果生成比例低于期望，提升权重
        if ratio < 0.15:  # 期望每个家族占15%
            new_weights[family] = new_weights.get(family, 1.0) * (1 + learning_rate)
        elif ratio > 0.35:  # 如果某家族过多，降低权重
            new_weights[family] = new_weights.get(family, 1.0) * (1 - learning_rate * 0.5)

    # 归一化（避免权重无限增长）
    max_weight = max(new_weights.values()) if new_weights else 1.0
    if max_weight > 5.0:
        new_weights = {k: v / max_weight * 2.0 for k, v in new_weights.items()}

    return new_weights


def append_to_csv(output_path: Path, candidates: list[AlphaCandidate]) -> None:
    """追加候选到CSV"""
    file_exists = output_path.exists()
    with output_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "候选ID", "主题ID", "假设ID", "研究家族", "策略家族",
                "变异类型", "机制", "数据集", "表达式", "精确哈希",
                "参数骨架", "字段骨架", "生成时间",
            ])
        generation_time = datetime.now(timezone.utc).isoformat()
        for c in candidates:
            writer.writerow([
                c.candidate_id, c.topic_id, c.hypothesis_id, c.research_family,
                c.strategy_family, c.mutation_type, c.mechanism, c.dataset,
                c.expression, c.exact_hash, c.parameter_skeleton,
                c.field_skeleton, generation_time,
            ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alpha生成脚本 v2")
    parser.add_argument("--database", default="research_memory.sqlite", help="数据库路径")
    parser.add_argument("--output", default="待提交Alpha列表.csv", help="输出CSV")
    parser.add_argument("--batch-size", type=int, default=50, help="每轮生成数量")
    parser.add_argument("--interval", type=int, default=10, help="轮次间隔（秒）")
    parser.add_argument("--max-rounds", type=int, default=0, help="最大轮数（0=无限）")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args(argv)

    from alpha_mining.common import load_workspace_env
    load_workspace_env(_ROOT / ".env")

    print(f"[生成] === Alpha生成脚本 v2 ===")
    print(f"[生成] 数据库: {args.database}")
    print(f"[生成] 输出: {args.output}")
    print(f"[生成] 每轮: {args.batch_size} 个")
    print(f"[生成] 间隔: {args.interval} 秒")
    if args.max_rounds > 0:
        print(f"[生成] 最大轮数: {args.max_rounds}")
    else:
        print(f"[生成] 模式: 无限循环（Ctrl+C停止）")
    print()

    # 加载研究规范
    specs = load_research_specs(Path(args.database))
    if not specs:
        print("[生成] ✗ 无可用研究规范")
        return 1
    print(f"[生成] ✓ 加载 {len(specs)} 个研究规范")

    # 加载已存在的哈希（全局去重）
    global_seen_hashes: set[str] = set()
    output_path = Path(args.output)
    if output_path.exists():
        try:
            with output_path.open("r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("精确哈希"):
                        global_seen_hashes.add(row["精确哈希"])
            print(f"[生成] ✓ 加载 {len(global_seen_hashes)} 个已有哈希")
        except Exception:
            pass

    # 初始化策略权重（公平起点）
    strategy_weights = {
        "momentum": 1.0,
        "reversal": 1.0,
        "volatility": 1.0,
        "fundamental": 1.0,
        "balanced": 1.0,
    }

    rng = random.Random()
    round_num = 0
    total_generated = 0
    consecutive_empty_rounds = 0

    try:
        while True:
            round_num += 1
            if args.max_rounds > 0 and round_num > args.max_rounds:
                break

            print(f"\n[生成] === 第 {round_num} 轮 ===")
            if args.verbose:
                print(f"[生成]   策略权重: {strategy_weights}")

            # 生成批次
            try:
                batch, rejected = generate_batch(
                    specs,
                    limit=args.batch_size,
                    global_seen_hashes=global_seen_hashes,
                    strategy_weights=strategy_weights,
                    rng=rng,
                )
            except Exception as exc:
                print(f"[生成] ✗ 生成失败: {exc}")
                time.sleep(args.interval)
                continue

            if not batch:
                consecutive_empty_rounds += 1
                print(f"[生成] 本轮未生成候选（连续第 {consecutive_empty_rounds} 轮）")
                if rejected:
                    total_rejected = sum(rejected.values())
                    print(f"[生成]   拒绝 {total_rejected} 个: {rejected}")

                if consecutive_empty_rounds >= 10:
                    print(f"\n[生成] ⚠️  已连续 {consecutive_empty_rounds} 轮空轮")
                    print(f"[生成] 可能原因: 组合空间耗尽 / 去重过滤严格")

                if consecutive_empty_rounds >= 30:
                    print(f"\n[生成] ✗ 已连续 {consecutive_empty_rounds} 轮空轮，停止")
                    break

                time.sleep(args.interval)
                continue

            consecutive_empty_rounds = 0

            # 显示统计
            family_dist = defaultdict(int)
            for c in batch:
                family_dist[c.strategy_family] += 1

            print(f"[生成] ✓ 新增 {len(batch)} 个候选（总计 {total_generated + len(batch)}）")
            print(f"[生成]   策略分布: {dict(family_dist)}")
            if rejected:
                total_rejected = sum(rejected.values())
                print(f"[生成]   拒绝 {total_rejected} 个: {rejected}")

            if args.verbose and batch:
                print(f"[生成]   示例表达式:")
                for i, c in enumerate(batch[:3], 1):
                    print(f"[生成]     {i}. {c.expression[:70]}...")

            # 保存到CSV
            try:
                append_to_csv(output_path, batch)
                total_generated += len(batch)
            except Exception as exc:
                print(f"[生成] ✗ CSV写入失败: {exc}")

            # 优化策略权重
            strategy_weights = update_strategy_weights(strategy_weights, batch)

            # 等待下一轮
            if args.max_rounds == 0 or round_num < args.max_rounds:
                time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\n[生成] 用户中断（Ctrl+C）")

    print(f"\n[生成] === 统计 ===")
    print(f"[生成] 总轮数: {round_num}")
    print(f"[生成] 累积生成: {total_generated} 个候选")
    print(f"[生成] 输出文件: {output_path.absolute()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
