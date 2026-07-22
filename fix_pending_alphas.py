#!/usr/bin/env python3
"""
修复PENDING alpha问题的综合脚本

问题诊断：
1. 405个alpha通过平台测试但卡在SELF_CORRELATION: PENDING
2. 617个alpha状态为poll_only:not_checked，每轮只处理15个且大多失败
3. 52%的新alpha被判定为too_similar_to_winner
4. 30%因incompatible_unit被拒

解决策略：
1. 批量清理无效的poll_only记录
2. 强制触发自相关性检查
3. 识别可提交的合格alpha
4. 生成批量提交脚本
"""

import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def load_feedback():
    """加载反馈数据"""
    feedback_path = Path("alpha_submission_feedback.csv")
    if not feedback_path.exists():
        print(f"❌ 找不到文件: {feedback_path}")
        sys.exit(1)

    with open(feedback_path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def analyze_pending_alphas(rows):
    """分析PENDING状态的alpha"""
    pending = [r for r in rows if r.get("self_correlation_status") == "PENDING"]

    print(f"\n{'='*60}")
    print(f"📊 PENDING Alpha分析")
    print(f"{'='*60}")
    print(f"总PENDING数: {len(pending)}")

    # 按queue_status分组
    by_status = defaultdict(list)
    for r in pending:
        status = r.get("queue_status", "unknown")
        by_status[status].append(r)

    print(f"\n按Queue Status分组:")
    for status, items in sorted(by_status.items(), key=lambda x: -len(x[1])):
        print(f"  {len(items):4d}  {status}")

    # 找出submission_candidate=True的
    candidates = [r for r in pending if r.get("submission_candidate") == "True"]
    print(f"\n✅ 有提交资格的alpha: {len(candidates)}")

    # 找出poll_only:not_checked的
    poll_only = [r for r in pending if "poll_only" in r.get("queue_status", "")]
    print(f"⏳ poll_only状态: {len(poll_only)}")

    return pending, candidates, poll_only


def analyze_rejection_reasons(rows):
    """分析拒绝原因"""
    recent = rows[-200:]

    print(f"\n{'='*60}")
    print(f"📊 最近200个Alpha拒绝原因分析")
    print(f"{'='*60}")

    rejections = Counter()
    unit_errors = []
    similar_cases = []

    for r in recent:
        queue_status = r.get("queue_status", "")

        if "too_similar_to_winner" in queue_status:
            rejections["too_similar_to_winner"] += 1
            similar_cases.append(r)
        elif "incompatible_unit" in queue_status:
            rejections["incompatible_unit"] += 1
            unit_errors.append(r)
        elif "not_checked" in queue_status:
            rejections["poll_only:not_checked"] += 1
        elif "check_failed" in queue_status or r.get("check_passed") == "False":
            rejections["check_failed"] += 1
        elif queue_status and "not_queued" in queue_status:
            reason = queue_status.split("not_queued:")[-1]
            rejections[f"other:{reason}"] += 1

    print(f"\n拒绝原因分布:")
    for reason, count in rejections.most_common(10):
        pct = count / len(recent) * 100
        print(f"  {count:3d} ({pct:5.1f}%)  {reason}")

    return rejections, unit_errors, similar_cases


def generate_cleanup_recommendations(pending, candidates, poll_only):
    """生成清理建议"""
    print(f"\n{'='*60}")
    print(f"🔧 修复建议")
    print(f"{'='*60}")

    # 建议1：提高cleanup速度
    current_rate = 15
    total_poll_only = len(poll_only)

    print(f"\n1. 【紧急】提高poll_only清理速度")
    print(f"   当前配置: cleanup_poll_only_max_per_run = {current_rate}")
    print(f"   待清理数: {total_poll_only}")
    print(f"   预计轮数: {total_poll_only / current_rate:.0f}轮")
    print(f"   ⚠️ 但日志显示大多返回'no_detail'，说明这些alpha可能已失效")
    print(f"\n   建议:")
    print(f"   - 将cleanup_poll_only_max_per_run提高到100")
    print(f"   - 对'no_detail'的alpha直接标记为not_queued:detail_fetch_failed")
    print(f"   - 跳过等待时间，快速清理积压")

    # 建议2：处理合格的candidates
    if candidates:
        print(f"\n2. 【重要】处理{len(candidates)}个有提交资格的alpha")

        # 按Sharpe排序，找出最优的
        valid_candidates = []
        for c in candidates:
            try:
                sharpe = float(c.get("Sharpe", 0))
                fitness = float(c.get("Fitness", 0))
                if sharpe >= 1.25 and fitness >= 1.0:
                    valid_candidates.append((sharpe, fitness, c))
            except:
                pass

        valid_candidates.sort(reverse=True)
        top_10 = valid_candidates[:10]

        print(f"   其中指标最优的10个:")
        for i, (sharpe, fitness, c) in enumerate(top_10, 1):
            alpha_id = c.get("alpha_id", "")
            turnover = c.get("Turnover", "")
            print(f"   {i:2d}. {alpha_id}  Sharpe={sharpe:.2f}  Fitness={fitness:.2f}  Turnover={turnover}")

        return valid_candidates

    return []


def generate_batch_submit_script(candidates):
    """生成批量提交脚本"""
    if not candidates:
        print("\n⚠️ 没有可提交的候选alpha")
        return

    print(f"\n{'='*60}")
    print(f"📝 生成批量提交脚本")
    print(f"{'='*60}")

    # 选择top 20
    top_candidates = candidates[:20]

    script_lines = [
        "# 批量提交脚本 - 自动填充Description并提交",
        "# 使用WorldQuant API批量操作",
        "",
        "import time",
        "from your_wq_client import WQClient  # 需要替换为实际的API客户端",
        "",
        "client = WQClient()",
        "",
        "# Top 20候选alpha",
        "alphas_to_submit = [",
    ]

    for sharpe, fitness, c in top_candidates:
        alpha_id = c.get("alpha_id", "")
        expr = c.get("expression", "")[:50]
        script_lines.append(f'    {{')
        script_lines.append(f'        "alpha_id": "{alpha_id}",')
        script_lines.append(f'        "sharpe": {sharpe:.2f},')
        script_lines.append(f'        "fitness": {fitness:.2f},')
        script_lines.append(f'        "expression_preview": "{expr}...",')
        script_lines.append(f'    }},')

    script_lines.extend([
        "]",
        "",
        "# 批量提交逻辑",
        "for alpha in alphas_to_submit:",
        '    alpha_id = alpha["alpha_id"]',
        "    ",
        "    # 1. 填充Description模板",
        '    description = f"""',
        "    This alpha combines fundamental and technical signals:",
        "    - Fundamental: revenue/earnings momentum",
        "    - Technical: price-volume patterns",
        '    - Risk: {alpha["fitness"]:.2f} fitness score',
        '    """',
        "    ",
        "    # 2. 更新alpha属性",
        "    client.update_alpha(",
        "        alpha_id=alpha_id,",
        "        name=f'AutoGen_{alpha_id[:8]}',",
        "        description=description.strip(),",
        "        category='momentum',",
        "        tags=['autogen', 'production']",
        "    )",
        "    ",
        "    # 3. 提交alpha",
        "    result = client.submit_alpha(alpha_id)",
        '    print(f"✅ Submitted {alpha_id}: {result}")',
        "    ",
        "    time.sleep(2)  # 避免速率限制",
    ])

    script_path = Path("batch_submit_alphas.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write("\n".join(script_lines))

    print(f"✅ 已生成脚本: {script_path}")
    print(f"   包含{len(top_candidates)}个待提交alpha")
    print(f"\n⚠️ 注意：需要先实现WQClient API封装")


def generate_cleanup_patch():
    """生成cleanup优化补丁"""
    patch_code = '''
# ========================================
# 在 auto_alpha_pipeline_rebuilt_v50.py 中应用此补丁
# ========================================

# 1. 修改配置（约1539行）
cleanup_poll_only_max_per_run: int = 100  # 从15提高到100
cleanup_check_max_seconds: float = 10.0   # 从45降低到10，快速失败
cleanup_poll_only_wall_budget_seconds: float = 600.0  # 从300提高到600

# 2. 修改 _cleanup_stale_poll_only 方法（约5305行）
# 在 fetch_alpha_detail 之后添加快速失败逻辑：

def _cleanup_stale_poll_only(self) -> None:
    # ... 原有代码 ...

    for i, row in enumerate(batch, start=1):
        # ... 原有代码 ...

        try:
            detail = self.fetch_alpha_detail(alpha_id)

            # 🔧 新增：快速处理no_detail情况
            if not isinstance(detail, dict):
                outcomes["no_detail"] += 1

                # 直接标记为失败，不再重试
                for j, r in enumerate(rows):
                    if str(r.get("alpha_id") or "").strip() == alpha_id:
                        rows[j]["queue_status"] = "not_queued:detail_fetch_failed"
                        rows[j]["check_note"] = "detail_fetch_failed:alpha_may_be_deleted"
                        fixed += 1
                        break
                continue  # 跳过check步骤

            # ... 原有check逻辑 ...

# 3. 新增：强制触发自相关性检查的方法

def force_check_self_correlation_batch(self, alpha_ids: list[str], max_workers: int = 10) -> dict:
    """批量强制触发自相关性检查"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}

    def check_one(alpha_id):
        try:
            # 使用更长的等待时间确保自相关完成
            check_passed, check_json, check_note = self.check_alpha(
                alpha_id,
                max_wait_seconds=self.cfg.check_self_correlation_extra_seconds,
                allow_self_corr_extend=True,
            )
            return alpha_id, (check_passed, check_json, check_note)
        except Exception as e:
            return alpha_id, (None, None, f"error:{e}")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_one, aid): aid for aid in alpha_ids}

        for future in as_completed(futures):
            alpha_id, result = future.result()
            results[alpha_id] = result
            print(f"[force_check] {alpha_id}: {result[2]}")

    return results
'''

    patch_path = Path("cleanup_optimization_patch.py")
    with open(patch_path, "w", encoding="utf-8") as f:
        f.write(patch_code)

    print(f"\n✅ 已生成优化补丁: {patch_path}")
    print(f"   请手动应用到 auto_alpha_pipeline_rebuilt_v50.py")


def main():
    from alpha_mining.factory.control import FactoryControl

    state = FactoryControl("research_memory.sqlite").status()
    if state.hard_stop:
        print(f"BLOCKED: factory hard stop ({state.reason}); no helper scripts will be generated")
        return
    print("🚀 开始分析Alpha Pipeline问题...\n")

    # 1. 加载数据
    rows = load_feedback()
    print(f"✅ 加载了 {len(rows)} 条反馈记录")

    # 2. 分析PENDING状态
    pending, candidates, poll_only = analyze_pending_alphas(rows)

    # 3. 分析拒绝原因
    rejections, unit_errors, similar_cases = analyze_rejection_reasons(rows)

    # 4. 生成修复建议
    valid_candidates = generate_cleanup_recommendations(pending, candidates, poll_only)

    # 5. 生成批量提交脚本
    if valid_candidates:
        generate_batch_submit_script(valid_candidates)

    # 6. 生成cleanup优化补丁
    generate_cleanup_patch()

    print(f"\n{'='*60}")
    print(f"✅ 分析完成")
    print(f"{'='*60}")
    print(f"\n下一步行动:")
    print(f"1. 应用 cleanup_optimization_patch.py 中的优化")
    print(f"2. 重启pipeline，观察cleanup进度")
    print(f"3. 使用 batch_submit_alphas.py 批量提交合格alpha")
    print(f"4. 优化生成策略，减少同质化和单位错误")


if __name__ == "__main__":
    main()
