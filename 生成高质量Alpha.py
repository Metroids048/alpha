#!/usr/bin/env python3
"""
高质量Alpha生成脚本 - 精简版

核心理念：优中选优，质量驱动
- 目标：通过WorldQuant平台提交门槛（夏普≥1.57，fitness≥1.0，换手率1%-70%）
- 策略：少而精，每轮深度探索，长时间打磨单个候选
- 输出：高质量Alpha候选.csv（唯一持久化文件）

使用方法：
    python 生成高质量Alpha.py                    # 默认配置（推荐）
    python 生成高质量Alpha.py --max-rounds 10    # 限制轮数
    python 生成高质量Alpha.py --help             # 查看所有选项
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def load_existing_hashes(output_path: Path) -> set[str]:
    """读取已生成的精确哈希（去重）"""
    if not output_path.exists():
        return set()

    existing = set()
    try:
        with output_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                h = row.get("精确哈希", "")
                if h:
                    existing.add(h)
    except Exception as exc:
        print(f"[警告] 读取现有哈希失败: {exc}")

    return existing


def append_to_csv(
    output_path: Path,
    payloads: list[dict],
    generation_time: str,
    existing_hashes: set[str],
) -> int:
    """追加候选到CSV，返回实际写入数量"""
    new_payloads = [p for p in payloads if p.get("meta", {}).get("exact_hash") not in existing_hashes]

    if not new_payloads:
        return 0

    file_exists = output_path.exists()
    with output_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "候选ID",
                "策略家族",
                "来源",
                "分数",
                "表达式",
                "模拟设置JSON",
                "变体序号",
                "精确哈希",
                "参数骨架",
                "字段骨架",
                "生成时间",
            ])

        for payload in new_payloads:
            meta = payload.get("meta", {})
            settings_json = json.dumps(payload.get("settings", {}), ensure_ascii=False)
            writer.writerow([
                meta.get("profile", ""),
                meta.get("family", ""),
                meta.get("source", ""),
                meta.get("candidate_score", 0.0),
                payload.get("regular", ""),
                settings_json,
                meta.get("variant", 0),
                meta.get("exact_hash", ""),
                meta.get("parameter_skeleton", ""),
                meta.get("field_skeleton", ""),
                generation_time,
            ])
            existing_hashes.add(meta.get("exact_hash", ""))

    return len(new_payloads)


def feed_back_to_registry(
    registry_path: Path,
    payloads: list[dict],
    run_salt: int,
    generation_time: str,
) -> None:
    """反馈历史注册表，打破离线确定性循环"""
    if not payloads:
        return

    file_exists = registry_path.exists()
    try:
        with registry_path.open("a", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["utc_iso", "run_salt", "family", "source", "score", "expression"])
            for payload in payloads:
                meta = payload.get("meta", {})
                writer.writerow([
                    generation_time,
                    run_salt,
                    meta.get("family", "generated"),
                    meta.get("source", "offline"),
                    meta.get("candidate_score", 0.0),
                    payload.get("regular", ""),
                ])
    except Exception as exc:
        print(f"[警告] 写入历史注册表失败（不影响主流程）: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="高质量Alpha生成（优中选优，通过平台门槛为目标）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output", default="高质量Alpha候选.csv", help="输出CSV文件路径")
    parser.add_argument("--batch-size", type=int, default=150, help="每轮目标候选数（质量优先，减少数量）")
    parser.add_argument("--max-payloads", type=int, default=300, help="每轮最大payload数（精简）")
    parser.add_argument("--interval", type=int, default=60, help="轮次间隔（秒）- 更长间隔保证质量")
    parser.add_argument("--max-rounds", type=int, default=0, help="最大轮数（0=无限循环）")
    args = parser.parse_args(argv)

    from alpha_mining.common import load_workspace_env

    load_workspace_env(_ROOT / ".env")

    print("=" * 60)
    print("🎯 高质量Alpha生成模式")
    print("=" * 60)
    print(f"核心目标: 通过平台门槛（夏普≥1.57，fitness≥1.0，换手率1%-70%）")
    print(f"策略: 优中选优，时间换质量")
    print(f"输出文件: {args.output}")
    print(f"每轮目标: {args.batch_size} 个候选（精简，高质量）")
    print(f"轮次间隔: {args.interval} 秒（深度打磨）")
    if args.max_rounds > 0:
        print(f"最大轮数: {args.max_rounds}")
    else:
        print(f"模式: 无限循环（Ctrl+C停止）")
    print()

    # 初始化v50引擎（高质量配置）
    import auto_alpha_pipeline_rebuilt_v50 as v50

    username = os.environ.get("WQ_USERNAME", "")
    password = os.environ.get("WQ_PASSWORD", "")
    if not username or not password:
        print("❌ 错误: 未找到 WQ_USERNAME 或 WQ_PASSWORD 环境变量")
        print("   请在 .env 文件中配置：")
        print("   WQ_USERNAME=你的用户名")
        print("   WQ_PASSWORD=你的密码")
        return 1

    def _make_high_quality_pipeline():
        """创建高质量配置的引擎"""
        cfg = v50.PipelineConfig(username=username, password=password)

        # === 核心质量门槛（对齐平台提交标准）===
        cfg.min_sharpe_threshold = 1.57  # ✅ 用户要求：夏普≥1.57
        cfg.min_fitness_threshold = 1.0   # ✅ 用户要求：fitness≥1.0
        cfg.min_turnover_threshold = 0.01 # ✅ 用户要求：换手率1%-70%
        cfg.max_turnover_threshold = 0.70

        cfg.queue_min_sharpe = 1.57       # 队列门槛也同步提高
        cfg.queue_min_fitness = 1.0

        # === 质量优先策略 ===
        cfg.min_candidates_floor = args.batch_size  # 每轮候选数（精简）
        cfg.target_simulate_batch = args.batch_size
        cfg.max_simulate_batch_per_run = args.batch_size + 50

        # 探索型预设（多样性）
        cfg.preset = "diverse_exploration"
        cfg.apply_preset()

        # 近通过变异：降低参数微调型占比，提高探索
        cfg.min_near_pass_batch_share = 0.15  # 从默认40%降到15%

        # 【关键修复】同结构上限：必须在apply_preset()之后设置
        # 预设会设置默认值，我们的配置要覆盖它
        # 提高到30以平衡通过率和多样性（避免5000+被structure_budget_exceeded拒绝）
        cfg.max_same_shape_per_run = 30  # 20 -> 30（进一步降低structure_budget_exceeded拒绝率）

        # 行为多样性：允许更多不同行为模式
        cfg.behavior_similarity_cap = 0.78  # 0.75 -> 0.78（略微放宽）
        cfg.max_behavior_per_batch = 4      # 3 -> 4（每批次允许更多行为变体）

        # 字段偏好：使用低竞争字段
        cfg.prefer_underused_fields = True
        cfg.underused_field_share = 0.25  # 25%使用低竞争字段

        # 跨字段变体：提高结构多样性
        cfg.enable_near_pass_cross_field_variants = True
        cfg.near_pass_cross_field_variants_per_seed = 4

        # 【关键修复】历史相似度：大幅放宽，打破离线确定性
        cfg.max_history_similarity = 0.92  # 0.85 -> 0.92（激进放宽）
        cfg.prescreen_max_history_similarity = 0.88  # 0.82 -> 0.88
        cfg.prescreen_intrabatch_similarity = 0.88  # 0.82 -> 0.88

        # 【关键修复】完全禁用历史骨架限制
        cfg.block_history_skeleton_always = False
        cfg.template_skip_history_skeleton = False
        cfg.block_history_skeleton_when_abundant = False  # 额外禁用

        # 【新增】引入随机扰动：每轮微调相似度阈值
        # 这样即使历史相同，不同的阈值也会产生不同的筛选结果
        perturbation = random.uniform(-0.03, 0.03)  # ±3%随机扰动
        cfg.max_history_similarity += perturbation
        cfg.prescreen_max_history_similarity += perturbation

        # 【新增】每轮更新时间戳，确保历史采样的随机性
        # 引擎内部用 random.seed(42) 采样历史，导致每次采样结果相同
        # 通过时间戳扰动打破这个确定性
        import time
        cfg._init_timestamp = time.time()  # 引擎可能会用这个（如果有的话）

        # 关闭联网（离线模式）
        cfg.sync_platform_tried_before_simulate = False
        cfg.library_expression_fetch_max = 0

        # 启用磁盘缓存（永久TTL）
        cfg.enable_fields_disk_cache = True
        cfg.fields_disk_cache_ttl_seconds = 365 * 24 * 3600

        pipeline = v50.WorldQuantAlphaPipeline(cfg)
        selector = v50.ProfileSelector(cfg)
        return pipeline, selector, cfg

    # 验证配置
    try:
        _p, _s, _c = _make_high_quality_pipeline()
        print("✅ 引擎配置验证通过")
        print(f"✅ 质量门槛: 夏普≥{_c.min_sharpe_threshold:.2f}, fitness≥{_c.min_fitness_threshold:.2f}")
        print(f"✅ 换手率: {_c.min_turnover_threshold:.1%} - {_c.max_turnover_threshold:.1%}")
        print(f"✅ 探索型配额: {_c.arch_explore_batch_quota}")
        print(f"✅ 近通过占比: {_c.min_near_pass_batch_share:.0%}")
        print(f"✅ 同结构上限: {_c.max_same_shape_per_run}")
        print(f"✅ 低竞争字段: {_c.underused_field_share:.0%}")
        del _p, _s, _c
    except Exception as exc:
        print(f"❌ 引擎配置验证失败: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    output_path = Path(args.output)
    registry_path = _ROOT / "alpha_generated_expressions.csv"
    existing_hashes = load_existing_hashes(output_path)
    print(f"\n📊 已加载 {len(existing_hashes)} 个历史哈希（去重）")
    print(f"📝 历史注册表: {registry_path.name}\n")

    round_num = 0
    total_generated = 0
    zero_new_streak = 0

    try:
        while True:
            round_num += 1

            if args.max_rounds > 0 and round_num > args.max_rounds:
                print(f"\n🏁 已达到最大轮数 {args.max_rounds}，停止")
                break

            print(f"\n{'='*60}")
            print(f"🔄 第 {round_num} 轮生成")
            print(f"{'='*60}")

            # 每轮重新初始化引擎
            # 【关键修复】猴子补丁：拦截引擎内部的 random.seed(42) 调用
            # 引擎在读取历史时会用 random.seed(42) 进行分层采样，导致每次采样结果相同
            # 我们用猴子补丁让它每次都用不同的种子
            import random as _random
            _original_seed = _random.seed
            _dynamic_seed = int(time.time() * 1000) + round_num

            def _patched_seed(a=None, version=2):
                """拦截 seed(42) 调用，用动态种子替换"""
                if a == 42:  # 拦截引擎的固定种子
                    return _original_seed(_dynamic_seed, version)
                return _original_seed(a, version)

            _random.seed = _patched_seed  # 应用补丁

            try:
                pipeline, selector, _ = _make_high_quality_pipeline()
            except Exception as exc:
                print(f"❌ 引擎初始化失败: {exc}")
                import traceback
                traceback.print_exc()
                time.sleep(args.interval)
                continue
            finally:
                # 恢复原始的 random.seed
                _random.seed = _original_seed

            # 生成候选
            try:
                candidates, catalog = pipeline.generate_candidates()
                print(f"✅ 生成: {len(candidates)} 个候选")
            except Exception as exc:
                print(f"❌ 生成失败: {exc}")
                error_msg = str(exc).lower()
                if "403" in error_msg or "401" in error_msg or "cache" in error_msg:
                    print(f"💡 可能原因: 本地字段缓存缺失")
                    print(f"   建议: 运行一次 提交Alpha.py 完成登录并刷新缓存")
                import traceback
                traceback.print_exc()
                print(f"⏳ 等待 {args.interval} 秒后重试...\n")
                time.sleep(args.interval)
                continue

            if not candidates:
                print(f"⚠️  本轮未生成候选，等待重试...\n")
                time.sleep(args.interval)
                continue

            # 转换为payloads
            try:
                payloads = selector.payloads_for(candidates, max_payloads=args.max_payloads)
                print(f"✅ 转换: {len(payloads)} 个 payload")

                # 填充哈希和骨架
                from alpha_mining.domain.expression_normalization import expression_identity

                skeleton_set = set()
                for payload in payloads:
                    if "meta" not in payload:
                        payload["meta"] = {}
                    identity = expression_identity(payload.get("regular", ""))
                    payload["meta"]["exact_hash"] = identity.exact_hash
                    payload["meta"]["parameter_skeleton"] = identity.parameter_skeleton
                    payload["meta"]["field_skeleton"] = identity.field_skeleton
                    skeleton_set.add(identity.field_skeleton)

                # 多样性统计
                total_candidates = len(payloads)
                unique_skeletons = len(skeleton_set)
                diversity_ratio = unique_skeletons / total_candidates if total_candidates > 0 else 0.0
                print(f"📊 多样性: {total_candidates}个候选 → {unique_skeletons}种结构骨架 ({diversity_ratio:.1%})")

                if diversity_ratio < 0.30:
                    print(f"⚠️  多样性偏低，系统将自动调整")

            except Exception as exc:
                print(f"❌ payload转换失败: {exc}")
                import traceback
                traceback.print_exc()
                time.sleep(args.interval)
                continue

            # 写入CSV
            generation_time = datetime.now(timezone.utc).isoformat()
            run_salt = random.getrandbits(32)

            # 筛选出新候选（用于写入CSV和反馈历史）
            new_payloads = [p for p in payloads if p.get("meta", {}).get("exact_hash") not in existing_hashes]

            try:
                new_count = append_to_csv(output_path, payloads, generation_time, existing_hashes)
                total_generated += new_count
                print(f"✅ 新增 {new_count} 个候选（总计 {total_generated}）")
            except Exception as exc:
                print(f"❌ CSV写入失败: {exc}")
                import traceback
                traceback.print_exc()
                new_count = 0

            # 【关键修复】只反馈新增的候选到历史注册表，避免重复写入导致历史爆炸
            # 如果全部是重复的，随机选择5-10个作为"尝试过的证据"，促使引擎下次调整策略
            if new_payloads:
                payloads_to_feedback = new_payloads
            else:
                # 随机选择5-10个（避免每次都是相同的前2个）
                sample_size = min(random.randint(5, 10), len(payloads))
                payloads_to_feedback = random.sample(payloads, sample_size)
            feed_back_to_registry(registry_path, payloads_to_feedback, run_salt, generation_time)

            if not new_payloads and payloads:
                print(f"💡 提示: 本轮228个候选全部重复，已记录2个作为历史证据，引擎下次将调整策略")

            # 卡死检测
            if new_count == 0:
                zero_new_streak += 1
                if zero_new_streak >= 3:
                    print(f"⚠️  已连续 {zero_new_streak} 轮新增0个候选")
                    if zero_new_streak >= 5:
                        # 自动归档并重置
                        archive_name = output_path.parent / f"archive_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
                        if output_path.exists():
                            import shutil
                            shutil.move(str(output_path), str(archive_name))
                            print(f"📦 已归档: {archive_name.name}")
                            print(f"🔄 重置去重池，开始新批次")
                            existing_hashes.clear()
                            total_generated = 0
                            zero_new_streak = 0
            else:
                zero_new_streak = 0

            # 等待下一轮
            if args.max_rounds == 0 or round_num < args.max_rounds:
                print(f"⏳ 等待 {args.interval} 秒...\n")
                time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\n\n⏹️  用户中断（Ctrl+C）")

    print(f"\n{'='*60}")
    print(f"📈 统计摘要")
    print(f"{'='*60}")
    print(f"总轮数: {round_num}")
    print(f"累积生成: {total_generated} 个候选")
    print(f"输出文件: {output_path.absolute()}")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
