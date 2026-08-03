#!/usr/bin/env python3
"""检查为什么simulation没有运行"""
import sqlite3

con = sqlite3.connect('alpha_state.sqlite3')

print('=' * 60)
print('🔍 诊断 simulation 缺失问题')
print('=' * 60)

# 1. 检查 expressions
print('\n📝 Expressions:')
exprs = con.execute('SELECT expression_id, expression_text FROM expressions ORDER BY expression_id DESC LIMIT 3').fetchall()
for eid, text in exprs:
    print(f'   ID {eid}: {text[:60]}...')

# 2. 检查 simulation_requests
print('\n📋 Simulation requests:')
req_status = con.execute('SELECT status, COUNT(*) FROM simulation_requests GROUP BY status').fetchall()
for status, count in req_status:
    print(f'   {status}: {count}')

if len(req_status) > 0:
    recent_req = con.execute('SELECT request_hash, status, created_at FROM simulation_requests ORDER BY created_at DESC LIMIT 3').fetchall()
    print('\n   最近3条:')
    for hash, status, created in recent_req:
        print(f'     {hash[:16]}... | {status} | {created}')

# 3. 检查 factory_candidate_claims
print('\n🏭 Factory candidate claims:')
claim_status = con.execute('SELECT status, COUNT(*) FROM factory_candidate_claims GROUP BY status').fetchall()
for status, count in claim_status:
    print(f'   {status}: {count}')

# 4. 检查 factory_control
print('\n⚙️ Factory control:')
fc = con.execute('SELECT hard_stop, reason, execute_submit FROM factory_control').fetchone()
if fc:
    hard_stop, reason, execute_submit = fc
    print(f'   hard_stop: {hard_stop}')
    print(f'   reason: {reason}')
    print(f'   execute_submit: {execute_submit}')

# 5. 检查 platform_access_state
print('\n🔐 Platform access state:')
pas = con.execute('SELECT state, last_successful_auth, last_401 FROM platform_access_state').fetchone()
if pas:
    state, last_auth, last_401 = pas
    print(f'   state: {state}')
    print(f'   last_successful_auth: {last_auth}')
    print(f'   last_401: {last_401}')

# 6. 检查 loop_health
print('\n💓 Loop health:')
lh = con.execute('SELECT current_cycle, consecutive_cycle_failures, last_success_at, last_failure_at FROM loop_health').fetchone()
if lh:
    cycle, failures, last_success, last_failure = lh
    print(f'   current_cycle: {cycle}')
    print(f'   consecutive_failures: {failures}')
    print(f'   last_success_at: {last_success}')
    print(f'   last_failure_at: {last_failure}')

con.close()

print('\n' + '=' * 60)
print('💡 可能的原因:')
print('=' * 60)
print('1. factory_control.hard_stop = 1 (工厂停止)')
print('2. platform_access_state.state != "open" (认证问题)')
print('3. simulation_requests 都失败了')
print('4. run_pipeline_loop.py 没有真正运行')
