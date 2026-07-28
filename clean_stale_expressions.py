#!/usr/bin/env python3
"""清理未完成的 expressions，让 pipeline 重新开始"""
import sqlite3

def main():
    con = sqlite3.connect('alpha_state.sqlite3')

    print('🧹 清理未完成的 expressions')
    print('=' * 60)

    # 1. 删除 expression_identities
    result1 = con.execute('DELETE FROM expression_identities').rowcount
    print(f'✅ 删除了 {result1} 个 expression_identities')

    # 2. 删除 expressions
    result2 = con.execute('DELETE FROM expressions').rowcount
    print(f'✅ 删除了 {result2} 个 expressions')

    con.commit()

    # 3. 验证清理结果
    print('\n📊 清理后统计:')
    print(f'   expression_identities: {con.execute("SELECT COUNT(*) FROM expression_identities").fetchone()[0]}')
    print(f'   expressions: {con.execute("SELECT COUNT(*) FROM expressions").fetchone()[0]}')
    print(f'   simulation_requests: {con.execute("SELECT COUNT(*) FROM simulation_requests").fetchone()[0]}')
    print(f'   factory_candidate_claims: {con.execute("SELECT COUNT(*) FROM factory_candidate_claims").fetchone()[0]}')

    # 4. 验证 hypotheses 和 data_mappings 还在
    print(f'\n✅ 保留的数据:')
    print(f'   hypotheses: {con.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]}')
    print(f'   data_mappings: {con.execute("SELECT COUNT(*) FROM data_mappings").fetchone()[0]}')

    con.close()

    print('\n✅ 清理完成！现在 generator 可以从头生成新的候选')

if __name__ == '__main__':
    main()
