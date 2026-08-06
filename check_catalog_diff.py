"""检查新旧catalog的字段差异"""

import json
from pathlib import Path

def check_catalog(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    fields = {row["id"] for row in data.get("rows", []) if isinstance(row, dict) and row.get("id")}
    return fields

# 缺失的字段
missing_fields = [
    "acquired_finite_intangible_assets_total",
    "anl4_fs_guidances_advanced_qf_nd_netprofita_maxguidance",
    "assets_curr",
    "fn_liab_fair_val_l1_a",
    "fnd6_cptnewqv1300_epsfxq",
]

print("=== 检查旧catalog ===")
old_path = Path(".alpha_datafields_cache.json")
old_fields = check_catalog(old_path)
print(f"旧catalog字段数: {len(old_fields)}")
for field in missing_fields:
    status = "✓" if field in old_fields else "✗"
    print(f"{status} {field}")

print("\n=== 检查新catalog ===")
new_path = Path("tmp_debug_generation/.alpha_datafields_cache.json")
new_fields = check_catalog(new_path)
print(f"新catalog字段数: {len(new_fields)}")
for field in missing_fields:
    status = "✓" if field in new_fields else "✗"
    print(f"{status} {field}")

print(f"\n=== 差异统计 ===")
only_new = new_fields - old_fields
only_old = old_fields - new_fields
print(f"新增字段: {len(only_new)}")
print(f"删除字段: {len(only_old)}")
print(f"共同字段: {len(old_fields & new_fields)}")

if only_new:
    print(f"\n新增字段示例（前10个）:")
    for field in sorted(only_new)[:10]:
        print(f"  + {field}")
