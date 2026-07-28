"""Monitor alpha generation in real-time"""
import sqlite3
import time
import sys

db_path = "alpha_state.sqlite3"
prev_count = 0

print("🔍 监控 alpha 生成...", flush=True)

while True:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 统计各状态的 alpha 数量
        cursor.execute("""
            SELECT status, COUNT(*)
            FROM alphas
            GROUP BY status
        """)
        stats = dict(cursor.fetchall())

        # 获取最近 5 个 alpha
        cursor.execute("""
            SELECT alpha_id, status, sharpe, turnover, fitness, created_at
            FROM alphas
            ORDER BY created_at DESC
            LIMIT 5
        """)
        recent = cursor.fetchall()

        conn.close()

        total = sum(stats.values())

        if total != prev_count:
            print(f"\n📊 [{time.strftime('%H:%M:%S')}] Total: {total} alphas", flush=True)
            for status, count in stats.items():
                print(f"  {status}: {count}", flush=True)

            if recent:
                print("\n  最近生成:", flush=True)
                for aid, status, sharpe, turn, fit, created in recent[:3]:
                    print(f"    {aid[:8]}.. {status} Sharpe={sharpe:.3f} turn={turn:.4f} fit={fit:.3f}", flush=True)

            prev_count = total

        time.sleep(10)

    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"❌ Error: {e}", flush=True)
        time.sleep(10)
