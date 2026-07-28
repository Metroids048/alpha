"""测试：清理未完成的 expressions"""
import sqlite3
import unittest


class TestCleanStaleExpressions(unittest.TestCase):
    def test_clean_stale_expressions(self):
        """清理未完成的 expressions，让 pipeline 重新开始"""
        con = sqlite3.connect('alpha_state.sqlite3')

        print('\n🧹 清理未完成的 expressions')
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
        identities = con.execute("SELECT COUNT(*) FROM expression_identities").fetchone()[0]
        expressions = con.execute("SELECT COUNT(*) FROM expressions").fetchone()[0]
        sim_req = con.execute("SELECT COUNT(*) FROM simulation_requests").fetchone()[0]
        claims = con.execute("SELECT COUNT(*) FROM factory_candidate_claims").fetchone()[0]

        print(f'   expression_identities: {identities}')
        print(f'   expressions: {expressions}')
        print(f'   simulation_requests: {sim_req}')
        print(f'   factory_candidate_claims: {claims}')

        # 4. 验证 hypotheses 和 data_mappings 还在
        hyp = con.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
        mappings = con.execute("SELECT COUNT(*) FROM data_mappings").fetchone()[0]

        print(f'\n✅ 保留的数据:')
        print(f'   hypotheses: {hyp}')
        print(f'   data_mappings: {mappings}')

        con.close()

        print('\n✅ 清理完成！现在 generator 可以从头生成新的候选')

        # 断言清理成功
        self.assertEqual(identities, 0)
        self.assertEqual(expressions, 0)
        self.assertGreater(hyp, 0)
        self.assertGreater(mappings, 0)


if __name__ == '__main__':
    unittest.main()
