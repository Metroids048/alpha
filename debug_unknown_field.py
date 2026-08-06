"""运行一次生成并详细记录UNKNOWN_FIELD拒绝"""

import sys
from alpha_mining.generation.production import main
from alpha_mining.generation.snapshots import load_local_snapshots
from alpha_mining.generation.validation import LocalExpressionValidator

# 先检查catalog中有哪些字段
print("=== 加载catalog ===")
snapshots = load_local_snapshots(root=".", allow_partial_offline=True)
catalog_fields = set(snapshots.catalog.fields.keys())
print(f"Catalog字段数: {len(catalog_fields)}")
print(f"Catalog来源: {snapshots.catalog_source}")
print(f"前10个字段样例: {list(catalog_fields)[:10]}")

# Monkey patch validate函数来记录详细信息
original_validate = LocalExpressionValidator.validate

def patched_validate(self, expression, **kwargs):
    """记录详细的验证信息"""
    result = original_validate(self, expression, **kwargs)

    unknown_field_issues = [issue for issue in result if issue.code == "UNKNOWN_FIELD"]
    if unknown_field_issues:
        print("\n=== UNKNOWN_FIELD拒绝详情 ===")
        print(f"Expression: {expression[:200]}...")
        print(f"Unknown fields ({len(unknown_field_issues)}):")
        for issue in unknown_field_issues[:10]:
            field = issue.message
            print(f"  - {field}")
            # 尝试模糊匹配
            similar = [f for f in catalog_fields if field.lower() in f.lower() or f.lower() in field.lower()]
            if similar:
                print(f"    相似字段: {similar[:3]}")

    return result

LocalExpressionValidator.validate = patched_validate

print("\n=== 开始生成 ===")
try:
    main()
except Exception as e:
    print(f"\n生成异常: {e}")
    import traceback
    traceback.print_exc()
