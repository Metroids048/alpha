import os
import time
import signal
import shutil
import subprocess

# ====================================================
# Claude Code 百炼自动切模型版（最终稳定版）
# ====================================================

MODELS = [

    # ===== 主力 =====

    "qwen3.6-plus",
    "glm-5.1",
    "deepseek-v3.2",
    "kimi-k2.5",

    # ===== 备用 =====

    "qwen-max",
    "qwen-plus",
    "qwen-turbo",

    # ===== 最后备用 =====

    "deepseek-v3",
    "deepseek-r1",
]

# ====================================================
# 百炼 API
# ====================================================

OPENAI_BASE_URL = os.environ.get("BAILIAN_OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/apps/anthropic")

OPENAI_API_KEY = os.environ.get("BAILIAN_OPENAI_API_KEY", "")

# ====================================================
# Claude 命令
# ====================================================

CLAUDE_CMD = shutil.which("claude") or "claude"

# ====================================================
# 检测错误关键词
# ====================================================

ERROR_MARKERS = [

    # quota
    "freetieronly",
    "allocationquota",
    "quota exceeded",
    "insufficient quota",

    # http
    "429",
    "403",

    # rate limit
    "rate limit",
    "too many requests",

    # common
    "api error",
    "request failed",
    "connection error",

]

# ====================================================
# 结束进程
# ====================================================

def kill_process(p):

    try:
        if os.name == "nt":
            p.kill()
        else:
            p.send_signal(signal.SIGTERM)
    except:
        pass

# ====================================================
# 主逻辑
# ====================================================

def main():

    if not OPENAI_API_KEY:
        print("[WARN] BAILIAN_OPENAI_API_KEY 未设置，请在环境变量或 .env 中配置后重试。")

    model_index = 0

    while model_index < len(MODELS):

        model = MODELS[model_index]

        print("\n====================================")
        print(f"当前模型: {model}")
        print("====================================\n")

        env = os.environ.copy()

        # 百炼 OpenAI
        env["OPENAI_BASE_URL"] = OPENAI_BASE_URL
        env["OPENAI_API_KEY"] = OPENAI_API_KEY

        # 强制 Claude 当前模型
        env["ANTHROPIC_MODEL"] = model

        try:

            # 注意：
            # 这里只启动交互模式
            # 不加 --print
            # 不加 stdin
            # 不做 pipe
            process = subprocess.Popen(
                [CLAUDE_CMD],
                env=env
            )

            # 等待 Claude 退出
            process.wait()

            code = process.returncode

            # 正常退出
            if code == 0:

                print("\nClaude Code 已退出")
                return

            print(f"\nClaude 异常退出，code={code}")

        except Exception as e:

            print(e)

        # ====================================================
        # 自动切下一个模型
        # ====================================================

        print(f"\n模型 {model} 可能已无额度")
        print("自动切换下一个模型...\n")

        try:
            kill_process(process)
        except:
            pass

        model_index += 1

        time.sleep(2)

    print("\n====================================")
    print("所有模型都已经没有免费额度")
    print("====================================")

    input("\n按回车退出...")


if __name__ == "__main__":

    main()