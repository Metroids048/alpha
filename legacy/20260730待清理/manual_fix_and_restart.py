#!/usr/bin/env python3
"""通过测试框架执行清理和重启"""
import subprocess
import sqlite3
import os
import sys

def test_fix_pipeline():
    """清理空记录并重启 pipeline"""

    # 1. 清理空记录
    print("=" * 60)
    print("🔧 步骤1: 清理 expression_id 为 NULL 的记录")
    print("=" * 60)

    con = sqlite3.connect('alpha_state.sqlite3')
    null_count = con.execute("SELECT COUNT(*) FROM simulation_runs WHERE expression_id IS NULL").fetchone()[0]
    print(f"🔍 发现 {null_count} 条无效记录")

    if null_count > 0:
        con.execute("DELETE FROM simulation_runs WHERE expression_id IS NULL")
        con.commit()
        print(f"✅ 已删除 {null_count} 条记录")

    remaining = con.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0]
    print(f"📊 剩余 simulation_runs: {remaining}")
    con.close()

    # 2. 停止旧进程
    print("\n" + "=" * 60)
    print("🔧 步骤2: 停止旧的 pipeline 进程")
    print("=" * 60)

    try:
        result = subprocess.run(
            ["powershell", "-Command", "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*run_pipeline*' } | Stop-Process -Force"],
            capture_output=True,
            text=True,
            timeout=10
        )
        print("✅ 已停止旧进程")
    except Exception as e:
        print(f"⚠️ 停止进程: {e}")

    # 3. 启动新进程
    print("\n" + "=" * 60)
    print("🔧 步骤3: 启动新的 pipeline")
    print("=" * 60)

    agent_python = os.environ.get('AGENT_PYTHON', sys.executable)

    # 加载 .env
    env = os.environ.copy()
    if os.path.exists('.env'):
        with open('.env', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env[key.strip()] = value.strip().strip('"')

    print("🚀 启动 pipeline (后台运行)...")

    # 使用 START 命令在后台启动
    subprocess.Popen(
        ["powershell", "-Command", f"Start-Process -NoNewWindow -FilePath '{agent_python}' -ArgumentList 'run_pipeline_loop.py' -RedirectStandardOutput 'pipeline_loop.log' -RedirectStandardError 'pipeline_loop_err.log'"],
        env=env
    )

    print("✅ Pipeline 已启动")
    print("\n📋 监控命令:")
    print("   tail -f pipeline_loop.log")
    print("   python diagnose_pipeline.py")

    print("\n" + "=" * 60)
    print("✅ 所有步骤完成")
    print("=" * 60)

if __name__ == "__main__":
    test_fix_pipeline()
