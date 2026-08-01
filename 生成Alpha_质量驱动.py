#!/usr/bin/env python3
"""
Alpha生成脚本 - 质量驱动版本（基于 v50 架构）

核心流程：
1. 生成候选 → 2. 平台模拟 → 3. 质量筛选 → 4. 反馈学习 → 5. 优化策略 → 循环

质量目标：
- Sharpe ≥ 1.24
- Fitness ≥ 1.0
- 降低策略家族内自相关
- 学习高质量特征
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


@dataclass
class SimulationResult:
    """平台模拟结果"""
    expression: str
    sharpe: float | None
    fitness: float | None
    turnover: float | None
    check_passed: bool
    quality_score: float  # sharpe + 1.05 * fitness


@dataclass
class QualityFeedback:
    """质量反馈（用于优化生成策略）"""
    strategy_family: str
    pass_count: int
    fail_count: int
    avg_sharpe: float
    avg_fitness: float
    pass_rate: float

    def quality_score(self) -> float:
        """综合质量分数（越高越好）"""
        return self.pass_rate * 100 + self.avg_sharpe * 10


def load_research_specs(database: Path) -> list[dict]:
    """从数据库加载研究规范"""
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
                   ORDER BY h.created_at"""
            ).fetchall()
    except Exception as exc:
        print(f"[质量] ✗ 数据库读取失败: {exc}")
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
    quality_feedback: dict[str, QualityFeedback],
    rng: random.Random,
) -> tuple[list[AlphaCandidate], dict[str, int]]:
    """生成一批候选（质量驱动选择）"""
    from alpha_mining.domain.expression_normalization import expression_identity
    from alpha_mining.generator.consultant_generator import ConsultantGenerator

    # 根据质量反馈调整生成器参数
    best_families = sorted(
        quality_feedback.items(),
        key=lambda x: x[1].quality_score(),
        reverse=True,
    )[:3] if quality_feedback else []

    if best_families:
        print(f"[质量]   最佳策略家族: {[f[0] for f in best_families]}")

    generator = ConsultantGenerator(
        max_per_hypothesis=12,  # 更多变体
        max_same_behavior=3,    # 允许更多同类型
    )

    # 按策略家族分组
    family_buckets: dict[str, list[dict]] = defaultdict(list)
    for spec in specs:
        sf = classify_strategy_family(spec["mechanism"], spec["family"])
        family_buckets[sf].append(spec)

    # 按质量权重排序（结合历史质量分数）
    families_in_order = sorted(
        family_buckets.keys(),
        key=lambda f: (
            strategy_weights.get(f, 1.0) *
            (quality_feedback.get(f).quality_score() if f in quality_feedback else 50.0)
        ),
        reverse=True,
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
    max_attempts = limit * 50  # 更多尝试次数，筛选更严格

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

            # 全局exact_hash去重
            if identity.exact_hash in global_seen_hashes:
                rejected_by_reason["EXACT_HASH_EXISTS"] += 1
                continue

            # 轮次field_skeleton去重（降低自相关）
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


def simulate_batch_quality(
    candidates: list[AlphaCandidate],
    gateway: Any,  # 复用外部传入的 gateway
    *,
    min_sharpe: float = 1.24,
    min_fitness: float = 1.0,
) -> list[SimulationResult]:
    """平台模拟（质量筛选）"""
    print(f"[质量]   开始平台模拟 {len(candidates)} 个候选...")

    # 默认设置
    default_settings = {
        "delay": 1,
        "decay": 0,
        "neutralization": "SUBINDUSTRY",
        "truncation": 0.08,
        "pasteurization": "ON",
        "unitHandling": "VERIFY",
        "nanHandling": "OFF",
        "language": "FASTEXPR",
        "visualization": False,
    }

    results: list[SimulationResult] = []
    for i, candidate in enumerate(candidates, 1):
        try:
            # 调用平台模拟
            sim_result = gateway.simulate(
                expression=candidate.expression,
                settings=default_settings,
                alpha_type="REGULAR",
            )

            sharpe = sim_result.metrics.get("sharpe")
            fitness = sim_result.metrics.get("fitness")
            turnover = sim_result.metrics.get("turnover")

            # 检查是否通过
            check_passed = False
            if sim_result.checks:
                check_passed = all(
                    str(c.get("result", "")).upper() == "PASS"
                    for c in sim_result.checks
                )

            quality_score = (sharpe or 0) + 1.05 * (fitness or 0)

            results.append(SimulationResult(
                expression=candidate.expression,
                sharpe=sharpe,
                fitness=fitness,
                turnover=turnover,
                check_passed=check_passed,
                quality_score=quality_score,
            ))

            if check_passed:
                status = f"✓ PASS (S={sharpe:.2f} F={fitness:.2f})"
            else:
                status = f"✗ FAIL (S={sharpe:.2f if sharpe else 0:.2f} F={fitness:.2f if fitness else 0:.2f})"
            print(f"[质量]     [{i}/{len(candidates)}] {status}")

        except Exception as exc:
            print(f"[质量]     [{i}/{len(candidates)}] ✗ 模拟失败: {exc}")
            continue

    return results


def update_quality_feedback(
    current_feedback: dict[str, QualityFeedback],
    batch: list[AlphaCandidate],
    results: list[SimulationResult],
) -> dict[str, QualityFeedback]:
    """根据模拟结果更新质量反馈"""

    # 按策略家族聚合结果
    family_stats: dict[str, dict] = defaultdict(lambda: {
        "pass": 0,
        "fail": 0,
        "sharpe_sum": 0.0,
        "fitness_sum": 0.0,
        "count": 0,
    })

    result_map = {r.expression: r for r in results}

    for candidate in batch:
        result = result_map.get(candidate.expression)
        if not result:
            continue

        stats = family_stats[candidate.strategy_family]
        stats["count"] += 1

        if result.sharpe is not None:
            stats["sharpe_sum"] += result.sharpe
        if result.fitness is not None:
            stats["fitness_sum"] += result.fitness

        if result.check_passed:
            stats["pass"] += 1
        else:
            stats["fail"] += 1

    # 更新反馈（指数移动平均）
    alpha = 0.3  # 新数据权重
    updated_feedback = current_feedback.copy()

    for family, stats in family_stats.items():
        count = stats["count"]
        if count == 0:
            continue

        new_pass_rate = stats["pass"] / count
        new_avg_sharpe = stats["sharpe_sum"] / count
        new_avg_fitness = stats["fitness_sum"] / count

        if family in updated_feedback:
            old = updated_feedback[family]
            updated_feedback[family] = QualityFeedback(
                strategy_family=family,
                pass_count=old.pass_count + stats["pass"],
                fail_count=old.fail_count + stats["fail"],
                avg_sharpe=alpha * new_avg_sharpe + (1 - alpha) * old.avg_sharpe,
                avg_fitness=alpha * new_avg_fitness + (1 - alpha) * old.avg_fitness,
                pass_rate=alpha * new_pass_rate + (1 - alpha) * old.pass_rate,
            )
        else:
            updated_feedback[family] = QualityFeedback(
                strategy_family=family,
                pass_count=stats["pass"],
                fail_count=stats["fail"],
                avg_sharpe=new_avg_sharpe,
                avg_fitness=new_avg_fitness,
                pass_rate=new_pass_rate,
            )

    return updated_feedback


def save_high_quality_candidates(
    output_path: Path,
    candidates: list[AlphaCandidate],
    results: list[SimulationResult],
    min_quality_score: float = 2.0,
) -> int:
    """只保存高质量候选"""
    result_map = {r.expression: r for r in results}

    high_quality = [
        c for c in candidates
        if c.expression in result_map and
        result_map[c.expression].quality_score >= min_quality_score
    ]

    if not high_quality:
        return 0

    file_exists = output_path.exists()
    with output_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "候选ID", "主题ID", "假设ID", "研究家族", "策略家族",
                "变异类型", "机制", "数据集", "表达式", "精确哈希",
                "参数骨架", "字段骨架", "生成时间",
                "Sharpe", "Fitness", "Turnover", "质量分数", "检查通过",
            ])

        generation_time = datetime.now(timezone.utc).isoformat()
        for c in high_quality:
            result = result_map[c.expression]
            writer.writerow([
                c.candidate_id, c.topic_id, c.hypothesis_id, c.research_family,
                c.strategy_family, c.mutation_type, c.mechanism, c.dataset,
                c.expression, c.exact_hash, c.parameter_skeleton,
                c.field_skeleton, generation_time,
                f"{result.sharpe:.3f}" if result.sharpe else "",
                f"{result.fitness:.3f}" if result.fitness else "",
                f"{result.turnover:.3f}" if result.turnover else "",
                f"{result.quality_score:.3f}",
                "YES" if result.check_passed else "NO",
            ])

    return len(high_quality)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alpha生成 - 质量驱动版本")
    parser.add_argument("--database", default="research_memory.sqlite")
    parser.add_argument("--output", default="高质量Alpha列表.csv")
    parser.add_argument("--batch-size", type=int, default=20, help="每轮生成数量（会模拟筛选）")
    parser.add_argument("--min-quality-score", type=float, default=2.0, help="最小质量分数（Sharpe+1.05*Fitness）")
    parser.add_argument("--interval", type=int, default=30, help="轮次间隔（秒）")
    parser.add_argument("--max-rounds", type=int, default=10, help="最大轮数")
    parser.add_argument("--simulate", action="store_true", help="启用平台模拟（需要账号）")
    args = parser.parse_args(argv)

    from alpha_mining.common import load_workspace_env
    load_workspace_env(_ROOT / ".env")

    print(f"[质量] === Alpha生成 - 质量驱动版本 ===")
    print(f"[质量] 数据库: {args.database}")
    print(f"[质量] 输出: {args.output}")
    print(f"[质量] 每轮: {args.batch_size} 个（模拟后筛选）")
    print(f"[质量] 最小质量分数: {args.min_quality_score}")
    print(f"[质量] 平台模拟: {'启用' if args.simulate else '禁用（使用 --simulate 启用）'}")
    print()

    # 初始化平台网关（只初始化一次，避免数据库锁）
    gateway = None
    if args.simulate:
        try:
            from alpha_mining.platform.gateway import PlatformGateway
            gateway = PlatformGateway(
                state_path=".wq_auth_state.json",
                database=args.database,
                lock_path="worldquant_api.lock",
                timeout=45.0,
                min_interval=2.0,
                poll_interval=3.0,
                max_poll_seconds=600.0,
            )
            print(f"[质量] ✓ 平台连接已初始化\n")
        except Exception as exc:
            print(f"[质量] ✗ 平台连接初始化失败: {exc}")
            print(f"[质量] 建议: 关闭其他占用数据库的进程后重试\n")
            return 1

    # 加载研究规范
    specs = load_research_specs(Path(args.database))
    if not specs:
        print("[质量] ✗ 无可用研究规范")
        return 1
    print(f"[质量] ✓ 加载 {len(specs)} 个研究规范\n")

    # 全局去重
    global_seen_hashes: set[str] = set()
    output_path = Path(args.output)
    if output_path.exists():
        try:
            with output_path.open("r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("精确哈希"):
                        global_seen_hashes.add(row["精确哈希"])
            print(f"[质量] ✓ 加载 {len(global_seen_hashes)} 个已有哈希\n")
        except Exception:
            pass

    # 初始化权重和反馈
    strategy_weights = {
        "momentum": 1.0,
        "reversal": 1.0,
        "volatility": 1.0,
        "fundamental": 1.0,
        "balanced": 1.0,
    }
    quality_feedback: dict[str, QualityFeedback] = {}

    rng = random.Random()
    round_num = 0
    total_generated = 0
    total_high_quality = 0

    try:
        while round_num < args.max_rounds:
            round_num += 1
            print(f"\n[质量] === 第 {round_num} 轮 ===")

            # 生成批次
            batch, rejected = generate_batch(
                specs,
                limit=args.batch_size,
                global_seen_hashes=global_seen_hashes,
                strategy_weights=strategy_weights,
                quality_feedback=quality_feedback,
                rng=rng,
            )

            if not batch:
                print(f"[质量] 本轮未生成候选")
                if rejected:
                    print(f"[质量]   拒绝: {rejected}")
                time.sleep(args.interval)
                continue

            total_generated += len(batch)
            family_dist = defaultdict(int)
            for c in batch:
                family_dist[c.strategy_family] += 1

            print(f"[质量] ✓ 生成 {len(batch)} 个候选")
            print(f"[质量]   策略分布: {dict(family_dist)}")

            # 平台模拟
            if args.simulate and gateway:
                results = simulate_batch_quality(
                    batch,
                    gateway,
                    min_sharpe=1.24,
                    min_fitness=1.0,
                )

                if results:
                    # 更新质量反馈
                    quality_feedback = update_quality_feedback(
                        quality_feedback, batch, results
                    )

                    # 显示质量反馈
                    if quality_feedback:
                        print(f"\n[质量] === 质量反馈 ===")
                        for family in sorted(quality_feedback.keys()):
                            fb = quality_feedback[family]
                            print(f"[质量]   {family}: "
                                  f"通过率={fb.pass_rate:.1%} "
                                  f"Sharpe={fb.avg_sharpe:.2f} "
                                  f"Fitness={fb.avg_fitness:.2f} "
                                  f"质量分={fb.quality_score():.1f}")

                    # 保存高质量候选
                    saved = save_high_quality_candidates(
                        output_path, batch, results, args.min_quality_score
                    )
                    total_high_quality += saved
                    print(f"\n[质量] ✓ 保存 {saved} 个高质量候选（总计 {total_high_quality}）")

            else:
                # 未启用模拟，保存所有候选
                print(f"[质量] 跳过模拟（使用 --simulate 启用）")

            # 等待下一轮
            if round_num < args.max_rounds:
                print(f"\n[质量] 等待 {args.interval} 秒...")
                time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\n[质量] 用户中断")

    print(f"\n[质量] === 统计 ===")
    print(f"[质量] 总轮数: {round_num}")
    print(f"[质量] 累积生成: {total_generated} 个")
    if args.simulate:
        print(f"[质量] 高质量候选: {total_high_quality} 个")
    print(f"[质量] 输出文件: {output_path.absolute()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
