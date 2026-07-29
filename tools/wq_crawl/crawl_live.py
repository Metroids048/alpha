"""连接到用户当前打开的 Chrome，复用已登录会话，爬取 WorldQuant 文档和论坛。

前提：用户已在 Chrome 里登录 platform.worldquantbrain.com 和 support.worldquantbrain.com。

启动 Chrome 时需要加 --remote-debugging-port=9222，然后运行本脚本。

Usage:
    python tools/wq_crawl/crawl_live.py --docs --forum
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUTPUT = REPO / "World quant"

PLATFORM = "https://platform.worldquantbrain.com"
SUPPORT = "https://support.worldquantbrain.com"

# 文档站的主要路径（从左侧导航栏整理）
DOCS_PATHS = [
    "/learn/documentation/brain-api/brain-api",
    "/learn/documentation/brain-api/simulations",
    "/learn/documentation/brain-api/alphas",
    "/learn/documentation/brain-api/data-fields",
    "/learn/documentation/brain-api/teams",
    "/learn/documentation/create-alphas/how-brain-platform-works",
    "/learn/documentation/create-alphas/alpha-syntax",
    "/learn/documentation/create-alphas/alpha-data-fields",
    "/learn/documentation/create-alphas/operators",
    "/learn/documentation/create-alphas/simulation-settings",
]

# 你给的中文论坛帖子 URL
FORUM_POSTS = [
    "42274443124119",  # 工作流分享 - 5 个 Agent Skill
    "42215210781719",  # Grandmaster 复盘 Q2 的 224 个 Alpha
    "41065497021335",  # MCP brain-forum-browse Skill
    "40604106677271",  # 玩转 Osmosis
    "39319955780887",  # 用 AI 跑几百次回测
    "35954766785175",  # 05 AI 精选合集
    "32032776365079",  # 04 永久置顶 - 高 Value Factor 顾问分享合集
    "19273239621399",  # Alpha 灵感启示录 - 合集
    "24472160509719",
    "21728222349335",
    "24497520676119",  # Machine Alpha 基础知识1
    "25066216209687",  # Machine Alpha 基础知识2
    "25066287753367",  # Machine Alpha 基础知识3
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", action="store_true", help="爬取平台文档")
    parser.add_argument("--forum", action="store_true", help="爬取中文论坛帖子")
    parser.add_argument("--cdp", default="http://localhost:9222", help="Chrome DevTools Protocol endpoint")
    args = parser.parse_args()

    if not args.docs and not args.forum:
        print("请至少指定 --docs 或 --forum")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 playwright，运行: pip install playwright && playwright install chromium")
        return 1

    OUTPUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(args.cdp)
        except Exception as e:
            print(f"连接失败: {e}")
            print("\n请先完全退出 Chrome，然后用以下命令重启（注意开头的 & 不能省）：")
            print('  & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" `')
            print('      --remote-debugging-port=9222 `')
            print('      --user-data-dir="$env:LOCALAPPDATA\\Google\\Chrome\\User Data"')
            return 1

        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if args.docs:
            print("=== 开始爬取文档 ===")
            crawl_docs(page)

        if args.forum:
            print("=== 开始爬取论坛 ===")
            crawl_forum(page, ctx)

        browser.close()

    print(f"\n✅ 完成！内容已保存到: {OUTPUT}")
    return 0


def crawl_docs(page) -> None:
    docs_dir = OUTPUT / "platform_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    for path in DOCS_PATHS:
        url = f"{PLATFORM}{path}"
        print(f"  {path}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)  # 等 SPA 渲染

            # 提取正文 HTML
            body_html = page.inner_html("body")
            safe_name = path.replace("/", "_").strip("_") + ".html"
            (docs_dir / safe_name).write_text(body_html, encoding="utf-8")

            # 同时保存纯文本版
            body_text = page.inner_text("body")
            (docs_dir / (safe_name.replace(".html", ".txt"))).write_text(body_text, encoding="utf-8")

        except Exception as e:
            print(f"    ❌ {e}")


def crawl_forum(page, ctx) -> None:
    forum_dir = OUTPUT / "support_forum"
    forum_dir.mkdir(parents=True, exist_ok=True)

    for post_id in FORUM_POSTS:
        url = f"{SUPPORT}/hc/en-us/community/posts/{post_id}"
        print(f"  {post_id}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)

            # 保存 HTML
            html = page.content()
            (forum_dir / f"{post_id}.html").write_text(html, encoding="utf-8")

            # 保存纯文本
            text = page.inner_text("body")
            (forum_dir / f"{post_id}.txt").write_text(text, encoding="utf-8")

            # 尝试调用 Zendesk API 拿 JSON（如果会话有效）
            try:
                resp = ctx.request.get(
                    f"{SUPPORT}/api/v2/community/posts/{post_id}.json",
                    timeout=30000,
                )
                if resp.status == 200:
                    data = resp.json()
                    (forum_dir / f"{post_id}.json").write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            except Exception:
                pass  # API 可能需要额外权限，HTML 已经够用

        except Exception as e:
            print(f"    ❌ {e}")


if __name__ == "__main__":
    raise SystemExit(main())

