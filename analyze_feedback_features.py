#!/usr/bin/env python3
"""检查candidate_outcomes是否有足够信息用于熔断模型训练"""
import sqlite3
import json
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")

with sqlite3.connect(database) as con:
    print("=== 检查PASS反馈的详细信息 ===")
    positive = con.execute("""
        SELECT
            request_hash,
            strategy_family,
            mechanism,
            dataset,
            operator_topology,
            field_skeleton,
            parameter_skeleton,
            sharpe,
            fitness,
            turnover,
            checks_json,
            quality_status
        FROM candidate_outcomes
        WHERE outcome = 'PASS'
        ORDER BY observed_at DESC
    """).fetchall()

    print(f"\n找到 {len(positive)} 条PASS记录：\n")
    for i, row in enumerate(positive, 1):
        req_hash, fam, mech, ds, topo, field_skel, param_skel, sharpe, fitness, turnover, checks, qual = row
        print(f"[{i}] {fam}")
        print(f"    dataset: {ds}")
        print(f"    operator_topology: {topo}")
        print(f"    mechanism: {mech[:80]}..." if mech and len(mech) > 80 else f"    mechanism: {mech}")
        print(f"    sharpe={sharpe:.3f}, fitness={fitness:.3f}, turnover={turnover if turnover else 'N/A'}")
        print(f"    field_skeleton: {field_skel[:60]}..." if field_skel and len(field_skel) > 60 else f"    field_skeleton: {field_skel}")
        print(f"    parameter_skeleton: {param_skel[:60]}..." if param_skel and len(param_skel) > 60 else f"    parameter_skeleton: {param_skel}")
        if checks:
            try:
                checks_list = json.loads(checks)
                print(f"    checks: {len(checks_list)} 项")
            except:
                print(f"    checks: (无法解析)")
        print()

    print("\n=== 检查FAILED反馈的详细信息（前5条）===")
    failed = con.execute("""
        SELECT
            request_hash,
            strategy_family,
            mechanism,
            dataset,
            operator_topology,
            field_skeleton,
            parameter_skeleton,
            error_category,
            error_message,
            quality_status
        FROM candidate_outcomes
        WHERE outcome = 'FAILED'
        ORDER BY observed_at DESC
        LIMIT 5
    """).fetchall()

    print(f"\n找到 {len(failed)} 条（显示前5条）：\n")
    for i, row in enumerate(failed, 1):
        req_hash, fam, mech, ds, topo, field_skel, param_skel, err_cat, err_msg, qual = row
        print(f"[{i}] {fam}")
        print(f"    dataset: {ds}")
        print(f"    operator_topology: {topo}")
        print(f"    error_category: {err_cat}")
        print(f"    error_message: {err_msg[:100]}..." if err_msg and len(err_msg) > 100 else f"    error_message: {err_msg}")
        print(f"    field_skeleton: {field_skel[:60]}..." if field_skel and len(field_skel) > 60 else f"    field_skeleton: {field_skel}")
        print()

    print("\n=== 可用特征字段统计 ===")
    fields_check = con.execute("""
        SELECT
            COUNT(CASE WHEN strategy_family != '' THEN 1 END) as has_family,
            COUNT(CASE WHEN mechanism != '' THEN 1 END) as has_mechanism,
            COUNT(CASE WHEN dataset != '' THEN 1 END) as has_dataset,
            COUNT(CASE WHEN operator_topology != '' THEN 1 END) as has_topology,
            COUNT(CASE WHEN field_skeleton != '' THEN 1 END) as has_field_skeleton,
            COUNT(CASE WHEN parameter_skeleton != '' THEN 1 END) as has_param_skeleton,
            COUNT(*) as total
        FROM candidate_outcomes
    """).fetchone()

    has_fam, has_mech, has_ds, has_topo, has_field, has_param, total = fields_check
    print(f"总记录数: {total}")
    print(f"  有 strategy_family: {has_fam} ({has_fam/total*100:.1f}%)")
    print(f"  有 mechanism: {has_mech} ({has_mech/total*100:.1f}%)")
    print(f"  有 dataset: {has_ds} ({has_ds/total*100:.1f}%)")
    print(f"  有 operator_topology: {has_topo} ({has_topo/total*100:.1f}%)")
    print(f"  有 field_skeleton: {has_field} ({has_field/total*100:.1f}%)")
    print(f"  有 parameter_skeleton: {has_param} ({has_param/total*100:.1f}%)")
