#!/usr/bin/env python3
"""启用提交并重启 pipeline"""
import sqlite3
import subprocess
import os
import sys

print('=' * 60)
print('🚀 启用提交功能并重启 pipeline')
print('=' * 60)

con = sqlite3.connect('alpha_state.sqlite3')

# 1. 启用提交
print('\n🔧 步骤1: 启用提交功能')
con.execute('UPDATE factory_control SET execute_submit = 1 WHERE singleton = 1')
con.commit()
print('✅ execute_submit = 1')

# 2. 验证状态
fc = con.execute('SELECT hard_stop, execute_submit FROM factory_control').fetchone()
pas = con.execute('SELECT state FROM platform_access_state').fetchone()
print(f'\n📊 当前配置:')
print(f'   factory_control.hard_stop: {fc[0]}')
print(f'   factory_control.execute_submit: {fc[1]}')
print(f'   platform_access_state.state: {pas[0]}')

con.close()

# 3. 停止旧进程
print('\n🔧 步骤2: 停止旧进程')
subprocess.run(
    ['powershell', '-Command',
     "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*run_pipeline*' } | Stop-Process -Force"],
    capture_output=True
)
print('✅ 已停止')

# 4. 启动新进程
print('\n🔧 步骤3: 启动新 pipeline')
agent_python = os.environ.get('AGENT_PYTHON', sys.executable)

env = os.environ.copy()
if os.path.exists('.env'):
    with open('.env', 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env[key.strip()] = value.strip().strip('"')

subprocess.Popen(
    ['powershell', '-Command',
     f"Start-Process -NoNewWindow -FilePath '{agent_python}' -ArgumentList 'run_pipeline_loop.py' -RedirectStandardOutput 'pipeline_loop.log' -RedirectStandardError 'pipeline_loop_err.log'"],
    env=env
)

print('✅ Pipeline 已启动')
print('\n' + '=' * 60)
print('✅ 全部完成！')
print('=' * 60)
print('\n📋 监控命令:')
print('   tail -f pipeline_loop.log')
print('   python diagnose_simulation.py')
