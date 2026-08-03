#!/usr/bin/env python3
"""修复 platform_access_state"""
import sqlite3
from datetime import datetime, timezone

con = sqlite3.connect('alpha_state.sqlite3')

print('=' * 60)
print('🔧 修复 platform_access_state')
print('=' * 60)

# 1. 查看当前状态
current = con.execute('SELECT state, last_successful_auth FROM platform_access_state').fetchone()
print(f'\n当前状态: {current[0]}')
print(f'最后认证: {current[1]}')

# 2. 更新为 OPEN
now = datetime.now(timezone.utc).isoformat()
con.execute('''
    UPDATE platform_access_state
    SET state = 'OPEN',
        last_successful_auth = ?,
        retry_after_until = NULL,
        recovery_attempts = 0
    WHERE singleton = 1
''', (now,))
con.commit()

print(f'\n✅ 已更新为 OPEN')
print(f'✅ last_successful_auth = {now}')

# 3. 验证
new_state = con.execute('SELECT state, last_successful_auth FROM platform_access_state').fetchone()
print(f'\n新状态: {new_state[0]}')
print(f'新认证时间: {new_state[1]}')

con.close()

print('\n' + '=' * 60)
print('✅ 修复完成！现在 pipeline 应该可以正常运行了')
print('=' * 60)
