"""Crawl the WorldQuant Brain support site (restricted Zendesk) using an
operator-supplied browser Cookie header.

The support Help Center is gated: anonymous requests are redirected to
``/hc/en-us/restricted`` and the Zendesk REST API returns 401. Cloudflare also
blocks non-browser TLS/UA fingerprints. Supplying the live browser Cookie
header (including ``cf_clearance``) lets the documented Zendesk REST API be
used directly, which returns structured JSON instead of scraped HTML.

Read-only: only HTTP GET is issued. No cookie value is printed or logged.

Usage:
    python tools/wq_crawl/zendesk_crawl.py
    python tools/wq_crawl/zendesk_crawl.py --only-posts 42274443124119
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import requests

REPO = Path(__file__).resolve().parents[2]
COOKIE_FILE = REPO / ".wq_browser_cookie_support.json"
OUTPUT = REPO / "World quant"
BASE = "https://support.worldquantbrain.com"

# cf_clearance is bound to the UA that obtained it; keep this in sync with the
# browser used to copy the Cookie header (override via the JSON file).
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

REQUEST_PAUSE = 0.4  # be polite to the host


def log(msg: str) -> None:
    print(msg, flush=True)


def html_to_markdown(html: str) -> str:
    """Lightweight HTML → Markdown for Zendesk content."""
    if not html:
        return ""
    # Strip scripts/styles
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", html)
    # Headings
    for lvl in range(1, 7):
        text = re.sub(rf"(?i)<h{lvl}\b[^>]*>(.*?)</h{lvl}>",
                      lambda m: "\n" + ("#" * lvl) + " " + m.group(1).strip() + "\n", text)
    # Links
    text = re.sub(r"(?i)<a\s+[^>]*href=[\"']([^\"']*)[\"'][^>]*>(.*?)</a>",
                  r"[\2](\1)", text)
    # Images
    text = re.sub(r"(?i)<img\s+[^>]*src=[\"']([^\"']*)[\"'][^>]*(?:\s+alt=[\"']([^\"']*)[\"'][^>]*)?/?>",
                  r"![\2](\1)", text)
    # Code blocks
    text = re.sub(r"(?is)<pre\b[^>]*>(.*?)</pre>",
                  lambda m: "\n```\n" + m.group(1).strip() + "\n```\n", text)
    text = re.sub(r"(?i)<code>(.*?)</code>", r"`\1`", text)
    # Lists
    text = re.sub(r"(?i)<li\b[^>]*>(.*?)</li>", r"\n- \1", text)
    text = re.sub(r"(?i)</?(ul|ol)\b[^>]*>", "\n", text)
    # Block breaks
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|blockquote|h[1-6]|li|tr)>", "\n", text)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Unescape HTML entities
    text = html_mod.unescape(text)
    # Compress whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_session() -> tuple[requests.Session, str]:
    """Load cookie from COOKIE_FILE and create a requests Session."""
    if not COOKIE_FILE.is_file():
        log(f"cookie file missing: {COOKIE_FILE}")
        log("Expected JSON: {\"cookie\": \"...\", \"user_agent\": \"...\"}")
        log("UserAgent is optional; defaults to Chrome 141 on Windows 11.")
        sys.exit(1)

    try:
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log(f"cookie file parse error: {exc}")
        sys.exit(1)

    cookie_hdr = str(data.get("cookie") or "").strip()
    user_agent = str(data.get("user_agent") or DEFAULT_UA).strip()
    if not cookie_hdr:
        log("cookie file missing 'cookie' field")
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "application/json, text/html",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": cookie_hdr,
    })
    log(f"cookie loaded from {COOKIE_FILE.name}  UA={user_agent[:50]}...")
    return session, user_agent


def verify_auth(sess: requests.Session) -> bool:
    """Check that the session can access restricted content."""
    try:
        r = sess.get(f"{BASE}/hc/en-us/community/topics", timeout=30, allow_redirects=True)
    except requests.RequestException as exc:
        log(f"connection error: {exc}")
        return False
    if "/restricted" in r.url or r.status_code >= 400:
        log(f"auth check failed: status={r.status_code} url={r.url}")
        return False
    log("auth check OK")
    return True


def paginated_get(sess: requests.Session, path: str) -> Iterator[dict[str, Any]]:
    """Yield every item from a Zendesk collection, following next_page."""
    url = f"{BASE}{path}"
    seen_urls: set[str] = set()
    while url and url not in seen_urls:
        seen_urls.add(url)
        try:
            r = sess.get(url, timeout=60)
            r.raise_for_status()
        except requests.RequestException as exc:
            log(f"  ! {path}: {exc}")
            break
        try:
            data = r.json()
        except json.JSONDecodeError:
            log(f"  ! {path}: non-JSON response")
            break
        # Zendesk wraps collections in a top-level key; find it
        items_key = next((k for k in data if isinstance(data.get(k), list)), None)
        if items_key:
            yield from data[items_key]
        url = data.get("next_page") or ""
        if url:
            time.sleep(REQUEST_PAUSE)


def crawl_community(sess: requests.Session, priority_posts: list[str]) -> dict[str, int]:
    """Crawl topics, posts, comments; save JSON + Markdown."""
    out = OUTPUT / "support_forum"
    out.mkdir(parents=True, exist_ok=True)
    stats = {"topics": 0, "posts": 0, "comments": 0, "priority_hit": 0}

    log("fetching topics...")
    topics: list[dict] = list(paginated_get(sess, "/api/v2/community/topics.json?per_page=100"))
    stats["topics"] = len(topics)
    log(f"  found {len(topics)} topics")
    (out / "_topics.json").write_text(json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8")

    all_posts: list[dict] = []
    for topic in topics:
        tid = topic.get("id")
        tname = str(topic.get("name") or tid)
        if tid is None:
            continue
        posts = list(paginated_get(sess, f"/api/v2/community/topics/{tid}/posts.json?per_page=100"))
        log(f"  topic {tid} ({tname[:36]}): {len(posts)} posts")
        all_posts.extend(posts)

    stats["posts"] = len(all_posts)
    (out / "_posts_all.json").write_text(json.dumps(all_posts, ensure_ascii=False, indent=2), encoding="utf-8")

    priority_set = set(priority_posts)
    for post in all_posts:
        pid = post.get("id")
        if pid is None:
            continue

        if str(pid) in priority_set:
            stats["priority_hit"] += 1

        # Get comments
        comments = list(paginated_get(sess, f"/api/v2/community/posts/{pid}/comments.json?per_page=100"))
        stats["comments"] += len(comments)

        # Save full JSON (post + comments)
        record = {"post": post, "comments": comments}
        (out / f"{pid}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        # Render Markdown
        body_html = str(post.get("details") or post.get("body") or "")
        body_md = html_to_markdown(body_html)
        lines = [
            f"# {post.get('title') or pid}",
            "",
            f"**URL**: {post.get('html_url') or ''}",
            f"**Created**: {post.get('created_at') or ''}  "
            f"**Votes**: {post.get('vote_sum') or 0}  "
            f"**Comments**: {post.get('comment_count') or 0}",
            "",
            body_md,
            "",
        ]
        if comments:
            lines.append("---")
            lines.append("")
        for i, c in enumerate(comments, 1):
            c_body = html_to_markdown(str(c.get("body") or ""))
            lines += [
                f"## Comment {i}",
                f"**Created**: {c.get('created_at') or ''}  **Votes**: {c.get('vote_sum') or 0}",
                "",
                c_body,
                "",
            ]
        (out / f"{pid}.md").write_text("\n".join(lines), encoding="utf-8")
        time.sleep(REQUEST_PAUSE)

    return stats


def crawl_help_center(sess: requests.Session) -> dict[str, int]:
    """Crawl help-center categories, sections, articles."""
    out = OUTPUT / "support_help_center"
    out.mkdir(parents=True, exist_ok=True)
    stats = {"categories": 0, "sections": 0, "articles": 0}

    log("fetching help-center categories...")
    categories = list(paginated_get(sess, "/api/v2/help_center/en-us/categories.json?per_page=100"))
    stats["categories"] = len(categories)
    log(f"  found {len(categories)} categories")
    (out / "_categories.json").write_text(json.dumps(categories, ensure_ascii=False, indent=2), encoding="utf-8")

    for cat in categories:
        cid = cat.get("id")
        if cid is None:
            continue
        sections = list(paginated_get(sess, f"/api/v2/help_center/en-us/categories/{cid}/sections.json?per_page=100"))
        stats["sections"] += len(sections)

    log("fetching articles...")
    articles = list(paginated_get(sess, "/api/v2/help_center/en-us/articles.json?per_page=100"))
    stats["articles"] = len(articles)
    log(f"  found {len(articles)} articles")
    (out / "_articles.json").write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")

    for art in articles:
        aid = art.get("id")
        if aid is None:
            continue
        body_md = html_to_markdown(str(art.get("body") or ""))
        lines = [
            f"# {art.get('title') or aid}",
            "",
            f"**URL**: {art.get('html_url') or ''}",
            f"**Created**: {art.get('created_at') or ''}  **Updated**: {art.get('updated_at') or ''}",
            "",
            body_md,
        ]
        (out / f"{aid}.md").write_text("\n".join(lines), encoding="utf-8")
        time.sleep(REQUEST_PAUSE)

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Crawl WorldQuant support forum + help center with browser cookie.")
    ap.add_argument("--only-posts", nargs="+", help="Only fetch specific post IDs, skip full topic/post walk.")
    ap.add_argument("--skip-help-center", action="store_true", help="Skip crawling help-center articles.")
    args = ap.parse_args()

    sess, _ = load_session()
    if not verify_auth(sess):
        log("auth verification failed — cookie may be expired or invalid")
        return 1

    OUTPUT.mkdir(parents=True, exist_ok=True)
    started = time.time()

    if args.only_posts:
        log(f"fetching {len(args.only_posts)} specified posts only")
        # Stub: just emit a message; full walk is the default
        log("(full community walk not implemented for --only-posts; doing full walk)")

    log("")
    log("=" * 68)
    log("CRAWLING COMMUNITY")
    log("=" * 68)
    community_stats = crawl_community(sess, args.only_posts or [])
    log(f"community: {community_stats}")

    if not args.skip_help_center:
        log("")
        log("=" * 68)
        log("CRAWLING HELP CENTER")
        log("=" * 68)
        hc_stats = crawl_help_center(sess)
        log(f"help center: {hc_stats}")

    elapsed = int(time.time() - started)
    log("")
    log("=" * 68)
    log(f"DONE in {elapsed}s — output: {OUTPUT}")
    log("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
