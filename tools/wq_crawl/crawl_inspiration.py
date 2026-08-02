"""Crawl Alpha Inspiration hub post children into local Markdown + JSON.

Primary path: browser cookie + Zendesk API (requests).
Fallback: Chrome DevTools Protocol (Playwright connect_over_cdp) when
Cloudflare binds clearance to the real browser TLS fingerprint.

Read-only GETs only. Never logs or writes cookie values.

Usage:
    python tools/wq_crawl/crawl_inspiration.py
    python tools/wq_crawl/crawl_inspiration.py --cdp http://127.0.0.1:9222
    python tools/wq_crawl/crawl_inspiration.py --limit 3
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import requests

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from zendesk_crawl import (  # noqa: E402
    BASE,
    COOKIE_FILE,
    REQUEST_PAUSE,
    html_to_markdown,
    load_session,
    log,
    paginated_get,
    verify_auth,
)

REPO = Path(__file__).resolve().parents[2]
OUTPUT = REPO / "World quant" / "alpha_inspiration"
DEFAULT_HUB_ID = "19273239621399"
DEFAULT_CDP = "http://127.0.0.1:9222"
PAUSE = max(REQUEST_PAUSE, 0.5)

POST_URL_RE = re.compile(
    r"(?:https?://support\.worldquantbrain\.com)?/hc/[^/\s\"']+/community/posts/(\d+)",
    re.I,
)
IMG_SRC_RE = re.compile(
    r"""(?is)<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"'][^>]*>"""
)
ATTACHMENT_HREF_RE = re.compile(
    r"""(?is)<a\b[^>]*?\bhref\s*=\s*[\"']([^\"']*(?:attachment|attachments|/hc/article_attachments/)[^\"']*)[\"'][^>]*>"""
)


@dataclass
class HubEntry:
    post_id: str
    title: str = ""
    href: str = ""
    recommended: bool = False
    locked: bool = False
    order: int = 0


@dataclass
class Failure:
    id: str
    title: str
    stage: str
    status: int | str
    error: str


@dataclass
class PostResult:
    post_id: str
    title: str = ""
    href: str = ""
    recommended: bool = False
    locked: bool = False
    status: str = "pending"
    md_path: str = ""
    json_path: str = ""
    image_count: int = 0
    comment_count: int = 0
    error: str = ""


class FetchClient(Protocol):
    def get_json(self, url: str) -> dict[str, Any]: ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None, str]: ...

    def get_collection(self, path: str) -> list[dict[str, Any]]: ...


class RequestsClient:
    def __init__(self, sess: requests.Session) -> None:
        self.sess = sess

    def get_json(self, url: str) -> dict[str, Any]:
        r = self.sess.get(url, timeout=60, allow_redirects=True)
        if "/restricted" in r.url:
            raise RuntimeError(f"redirected to restricted: {r.url}")
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError("non-object JSON")
        return data

    def get_bytes(self, url: str) -> tuple[bytes, str | None, str]:
        r = self.sess.get(url, timeout=60, allow_redirects=True)
        if "/restricted" in r.url or r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} url={r.url}")
        return r.content, r.headers.get("Content-Type"), r.url

    def get_collection(self, path: str) -> list[dict[str, Any]]:
        return list(paginated_get(self.sess, path))


class CdpClient:
    """HTTP via Playwright browser context (inherits live Chrome cookies + TLS)."""

    def __init__(self, request_ctx: Any, page: Any | None = None) -> None:
        self.req = request_ctx
        self.page = page

    def get_json(self, url: str) -> dict[str, Any]:
        r = self.req.get(url, timeout=60000)
        final = getattr(r, "url", url)
        if "/restricted" in str(final):
            raise RuntimeError(f"redirected to restricted: {final}")
        if r.status >= 400:
            raise RuntimeError(f"HTTP {r.status}")
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError("non-object JSON")
        return data

    def get_bytes(self, url: str) -> tuple[bytes, str | None, str]:
        # APIRequestContext often gets HTTP 403 on /hc/user_images/*;
        # in-page fetch() with credentials succeeds.
        if self.page is not None:
            result = self.page.evaluate(
                """async (u) => {
                  const resp = await fetch(u, {credentials: 'include'});
                  const buf = await resp.arrayBuffer();
                  const bytes = new Uint8Array(buf);
                  let binary = '';
                  const chunk = 0x8000;
                  for (let i = 0; i < bytes.length; i += chunk) {
                    binary += String.fromCharCode.apply(
                      null, bytes.subarray(i, Math.min(i + chunk, bytes.length))
                    );
                  }
                  return {
                    status: resp.status,
                    contentType: resp.headers.get('content-type'),
                    b64: btoa(binary),
                    finalUrl: resp.url,
                  };
                }""",
                url,
            )
            status = int(result.get("status") or 0)
            if status >= 400:
                raise RuntimeError(f"HTTP {status} url={url}")
            content = base64.b64decode(result.get("b64") or "")
            return content, result.get("contentType"), str(result.get("finalUrl") or url)

        r = self.req.get(url, timeout=60000)
        final = str(getattr(r, "url", url))
        if "/restricted" in final or r.status >= 400:
            raise RuntimeError(f"HTTP {r.status} url={final}")
        ctype = None
        try:
            ctype = r.headers.get("content-type") or r.headers.get("Content-Type")
        except Exception:
            ctype = None
        return r.body(), ctype, final

    def get_collection(self, path: str) -> list[dict[str, Any]]:
        url = f"{BASE}{path}"
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        while url and url not in seen:
            seen.add(url)
            data = self.get_json(url)
            key = next((k for k in data if isinstance(data.get(k), list)), None)
            if key:
                items.extend(data[key])
            url = str(data.get("next_page") or "")
            if url:
                time.sleep(PAUSE)
        return items


def _safe_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_failure(path: Path, fail: Failure) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(fail), ensure_ascii=False) + "\n")


def fetch_post(client: FetchClient, post_id: str) -> dict[str, Any]:
    data = client.get_json(f"{BASE}/api/v2/community/posts/{post_id}.json")
    post = data.get("post") if isinstance(data, dict) else None
    if not isinstance(post, dict):
        if isinstance(data, dict) and data.get("id"):
            post = data
        else:
            raise RuntimeError("unexpected post JSON shape")
    return post


def fetch_comments(client: FetchClient, post_id: str) -> list[dict[str, Any]]:
    path = f"/api/v2/community/posts/{post_id}/comments.json?per_page=100"
    return client.get_collection(path)


def parse_hub_entries(hub_html: str, hub_id: str) -> list[HubEntry]:
    if not hub_html:
        return []
    entries: list[HubEntry] = []
    seen: set[str] = set()
    anchor_re = re.compile(
        r"""(?is)<a\b([^>]*)\bhref\s*=\s*[\"']([^\"']+)[\"']([^>]*)>(.*?)</a>"""
    )
    for m in anchor_re.finditer(hub_html):
        href = html_unescape_attr(m.group(2).strip())
        inner = m.group(4) or ""
        pid_m = POST_URL_RE.search(href)
        if not pid_m:
            continue
        pid = pid_m.group(1)
        if pid == hub_id or pid in seen:
            continue
        seen.add(pid)
        start = max(0, m.start() - 120)
        end = min(len(hub_html), m.end() + 40)
        window = hub_html[start:end]
        recommended = ("⭐" in window) or ("★" in window) or ("star" in window.lower())
        locked = ("🔒" in window) or ("lock" in window.lower())
        title = re.sub(r"<[^>]+>", "", inner)
        title = re.sub(r"\s+", " ", title).strip().lstrip("⭐★🔒 ").strip()
        abs_href = href if href.startswith("http") else urljoin(BASE, href)
        entries.append(
            HubEntry(
                post_id=pid,
                title=title,
                href=abs_href,
                recommended=recommended,
                locked=locked,
                order=len(entries) + 1,
            )
        )
    return entries


def html_unescape_attr(value: str) -> str:
    import html as html_mod

    return html_mod.unescape(value)


def collect_media_urls(html: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for regex in (IMG_SRC_RE, ATTACHMENT_HREF_RE):
        for m in regex.finditer(html or ""):
            raw = html_unescape_attr(m.group(1).strip())
            if not raw or raw.startswith("data:"):
                continue
            abs_url = raw if raw.startswith("http") else urljoin(BASE, raw)
            if abs_url in seen:
                continue
            seen.add(abs_url)
            urls.append(abs_url)
    return urls


def _ext_from_response(url: str, content_type: str | None) -> str:
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        ext = mimetypes.guess_extension(ct)
        if ext == ".jpe":
            ext = ".jpg"
        if ext:
            return ext
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".bmp"}:
        return suffix
    return ".bin"


def download_media(
    client: FetchClient,
    urls: list[str],
    assets_dir: Path,
    failures_path: Path,
    post_id: str,
    title: str,
    rel_prefix: str = "assets",
) -> tuple[dict[str, str], int]:
    assets_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    ok = 0
    for i, url in enumerate(urls, 1):
        try:
            content, ctype, final_url = client.get_bytes(url)
            ext = _ext_from_response(final_url or url, ctype)
            digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
            name = f"{i:03d}_{digest}{ext}"
            (assets_dir / name).write_bytes(content)
            rel = f"{rel_prefix}/{name}"
            mapping[url] = rel
            parsed = urlparse(url)
            if parsed.path:
                mapping[parsed.path] = rel
            ok += 1
        except Exception as exc:  # noqa: BLE001
            _append_failure(
                failures_path,
                Failure(
                    id=post_id,
                    title=title,
                    stage="image",
                    status="error",
                    error=f"{url[:160]} :: {exc}",
                ),
            )
        time.sleep(0.15)
    return mapping, ok


def rewrite_html_urls(html: str, mapping: dict[str, str]) -> str:
    if not html or not mapping:
        return html or ""

    def repl_src(m: re.Match[str]) -> str:
        full = m.group(0)
        raw = html_unescape_attr(m.group(1).strip())
        abs_url = raw if raw.startswith("http") else urljoin(BASE, raw)
        local = mapping.get(abs_url) or mapping.get(raw) or mapping.get(urlparse(abs_url).path)
        if not local:
            return full
        return full.replace(m.group(1), local)

    out = IMG_SRC_RE.sub(repl_src, html)

    def repl_href(m: re.Match[str]) -> str:
        full = m.group(0)
        raw = html_unescape_attr(m.group(1).strip())
        abs_url = raw if raw.startswith("http") else urljoin(BASE, raw)
        local = mapping.get(abs_url) or mapping.get(raw) or mapping.get(urlparse(abs_url).path)
        if not local:
            return full
        return full.replace(m.group(1), local)

    return ATTACHMENT_HREF_RE.sub(repl_href, out)


def render_markdown(
    post: dict[str, Any],
    comments: list[dict[str, Any]],
    body_md: str,
    comment_mds: list[str],
    meta: dict[str, Any],
) -> str:
    lines = [
        f"# {post.get('title') or meta.get('post_id')}",
        "",
        f"**URL**: {post.get('html_url') or meta.get('href') or ''}",
        f"**Created**: {post.get('created_at') or ''}  "
        f"**Votes**: {post.get('vote_sum') or 0}  "
        f"**Comments**: {post.get('comment_count') or len(comments)}",
        f"**Recommended**: {meta.get('recommended', False)}  "
        f"**Locked**: {meta.get('locked', False)}",
        "",
        body_md,
        "",
    ]
    if comment_mds:
        lines.append("---")
        lines.append("")
        for i, c_md in enumerate(comment_mds, 1):
            c = comments[i - 1] if i - 1 < len(comments) else {}
            lines += [
                f"## Comment {i}",
                f"**Created**: {c.get('created_at') or ''}  **Votes**: {c.get('vote_sum') or 0}",
                "",
                c_md,
                "",
            ]
    return "\n".join(lines).strip() + "\n"


def crawl_one(
    client: FetchClient,
    entry: HubEntry,
    out_root: Path,
    failures_path: Path,
) -> PostResult:
    result = PostResult(
        post_id=entry.post_id,
        title=entry.title,
        href=entry.href,
        recommended=entry.recommended,
        locked=entry.locked,
    )
    post_dir = out_root / "posts" / entry.post_id
    assets_dir = post_dir / "assets"
    try:
        post = fetch_post(client, entry.post_id)
        time.sleep(PAUSE)
        comments = fetch_comments(client, entry.post_id)
        time.sleep(PAUSE)
    except Exception as exc:  # noqa: BLE001
        result.status = "fail"
        result.error = str(exc)
        _append_failure(
            failures_path,
            Failure(
                id=entry.post_id,
                title=entry.title,
                stage="fetch",
                status="error",
                error=str(exc),
            ),
        )
        return result

    title = str(post.get("title") or entry.title or entry.post_id)
    result.title = title
    result.href = str(post.get("html_url") or entry.href)
    result.comment_count = len(comments)

    body_html = str(post.get("details") or post.get("body") or "")
    comment_htmls = [str(c.get("body") or "") for c in comments]
    all_html = body_html + "\n" + "\n".join(comment_htmls)
    media_urls = collect_media_urls(all_html)

    mapping, img_ok = download_media(
        client, media_urls, assets_dir, failures_path, entry.post_id, title
    )
    result.image_count = img_ok

    body_local = rewrite_html_urls(body_html, mapping)
    comment_locals = [rewrite_html_urls(h, mapping) for h in comment_htmls]
    body_md = html_to_markdown(body_local)
    comment_mds = [html_to_markdown(h) for h in comment_locals]

    meta = {
        "post_id": entry.post_id,
        "recommended": entry.recommended,
        "locked": entry.locked,
        "hub_order": entry.order,
        "hub_title": entry.title,
        "href": result.href,
        "image_map": mapping,
    }
    record = {"post": post, "comments": comments, "meta": meta}

    try:
        post_dir.mkdir(parents=True, exist_ok=True)
        json_path = post_dir / f"{entry.post_id}.json"
        md_path = post_dir / f"{entry.post_id}.md"
        _safe_write_json(json_path, record)
        md_path.write_text(
            render_markdown(post, comments, body_md, comment_mds, meta),
            encoding="utf-8",
        )
        result.json_path = str(json_path.relative_to(out_root)).replace("\\", "/")
        result.md_path = str(md_path.relative_to(out_root)).replace("\\", "/")
        result.status = "ok"
    except Exception as exc:  # noqa: BLE001
        result.status = "fail"
        result.error = str(exc)
        _append_failure(
            failures_path,
            Failure(
                id=entry.post_id,
                title=title,
                stage="write",
                status="error",
                error=str(exc),
            ),
        )
    return result


def save_hub(
    client: FetchClient,
    hub_id: str,
    out_root: Path,
    failures_path: Path,
) -> tuple[dict[str, Any], str, list[HubEntry]]:
    try:
        post = fetch_post(client, hub_id)
    except Exception as exc:  # noqa: BLE001
        _append_failure(
            failures_path,
            Failure(id=hub_id, title="hub", stage="fetch", status="error", error=str(exc)),
        )
        raise

    body_html = str(post.get("details") or post.get("body") or "")
    entries = parse_hub_entries(body_html, hub_id)
    comments = fetch_comments(client, hub_id)
    time.sleep(PAUSE)

    assets_dir = out_root / "hub_assets"
    media_urls = collect_media_urls(
        body_html + "\n" + "\n".join(str(c.get("body") or "") for c in comments)
    )
    mapping, _ = download_media(
        client, media_urls, assets_dir, failures_path, hub_id, "hub", rel_prefix="hub_assets"
    )
    body_md = html_to_markdown(rewrite_html_urls(body_html, mapping))

    hub_record = {
        "post": post,
        "comments": comments,
        "meta": {
            "hub_id": hub_id,
            "entry_count": len(entries),
            "entries": [asdict(e) for e in entries],
            "image_map": mapping,
        },
    }
    _safe_write_json(out_root / "hub.json", hub_record)
    hub_md = render_markdown(
        post,
        comments,
        body_md,
        [],
        {"post_id": hub_id, "recommended": False, "locked": False, "href": post.get("html_url")},
    )
    toc_lines = ["", "## Parsed child posts", ""]
    for e in entries:
        flags = []
        if e.recommended:
            flags.append("star")
        if e.locked:
            flags.append("lock")
        flag_s = f" ({', '.join(flags)})" if flags else ""
        toc_lines.append(f"{e.order}. [{e.title or e.post_id}]({e.href}){flag_s} — `{e.post_id}`")
    toc_lines.append("")
    (out_root / "hub.md").write_text(hub_md.rstrip() + "\n" + "\n".join(toc_lines), encoding="utf-8")
    return post, body_html, entries


def write_index(out_root: Path, results: list[PostResult], hub_id: str) -> None:
    lines = [
        "# Alpha Inspiration crawl index",
        "",
        f"Hub post: `{hub_id}`",
        f"Total: {len(results)}  "
        f"OK: {sum(1 for r in results if r.status == 'ok')}  "
        f"Fail: {sum(1 for r in results if r.status != 'ok')}",
        "",
        "| # | Title | Rec | Lock | Status | Local MD |",
        "|---|-------|-----|------|--------|----------|",
    ]
    for i, r in enumerate(results, 1):
        title = (r.title or r.post_id).replace("|", "/")
        md = r.md_path or "-"
        lines.append(
            f"| {i} | [{title}]({r.href or '#'}) | "
            f"{'Y' if r.recommended else ''} | "
            f"{'Y' if r.locked else ''} | "
            f"{r.status} | `{md}` |"
        )
    lines.append("")
    (out_root / "index.md").write_text("\n".join(lines), encoding="utf-8")


def backfill_images(client: FetchClient, out_root: Path, failures_path: Path) -> dict[str, int]:
    """Re-download images for already-saved post JSON and rewrite Markdown."""
    posts_dir = out_root / "posts"
    stats = {"posts": 0, "with_media": 0, "images_ok": 0, "rewritten": 0}
    if failures_path.exists():
        failures_path.unlink()

    for json_path in sorted(posts_dir.glob("*/*.json")):
        stats["posts"] += 1
        record = json.loads(json_path.read_text(encoding="utf-8"))
        post = record.get("post") or {}
        comments = record.get("comments") or []
        meta = record.get("meta") or {}
        post_id = str(meta.get("post_id") or json_path.stem)
        title = str(post.get("title") or post_id)
        post_dir = json_path.parent
        assets_dir = post_dir / "assets"

        body_html = str(post.get("details") or post.get("body") or "")
        comment_htmls = [str(c.get("body") or "") for c in comments]
        media_urls = collect_media_urls(body_html + "\n" + "\n".join(comment_htmls))
        if not media_urls:
            continue
        stats["with_media"] += 1

        # Warm page on the post URL so fetch has correct origin/referrer context
        if isinstance(client, CdpClient) and client.page is not None:
            href = str(post.get("html_url") or f"{BASE}/hc/en-us/community/posts/{post_id}")
            try:
                client.page.goto(href, wait_until="domcontentloaded", timeout=120000)
                client.page.wait_for_timeout(800)
            except Exception as exc:  # noqa: BLE001
                log(f"  warm goto warn {post_id}: {exc}")

        mapping, img_ok = download_media(
            client, media_urls, assets_dir, failures_path, post_id, title
        )
        stats["images_ok"] += img_ok
        if not mapping:
            continue

        meta["image_map"] = mapping
        record["meta"] = meta
        _safe_write_json(json_path, record)

        body_md = html_to_markdown(rewrite_html_urls(body_html, mapping))
        comment_mds = [html_to_markdown(rewrite_html_urls(h, mapping)) for h in comment_htmls]
        md_path = post_dir / f"{post_id}.md"
        md_path.write_text(
            render_markdown(post, comments, body_md, comment_mds, meta),
            encoding="utf-8",
        )
        stats["rewritten"] += 1
        log(f"  backfill {post_id}: images_ok={img_ok}/{len(media_urls)}")

    return stats


def print_auth_help() -> None:
    log("")
    log("Auth failed for requests cookie path (Cloudflare often blocks non-browser TLS).")
    log("Options:")
    log("  A) Start Chrome with debugging, login, then re-run with --cdp:")
    log('       chrome.exe --remote-debugging-port=9222 --profile-directory="Profile 2"')
    log(f"       python tools/wq_crawl/crawl_inspiration.py --cdp {DEFAULT_CDP}")
    log(f"  B) Refresh {COOKIE_FILE.name} from a logged-in browser Cookie header.")


def try_requests_client() -> RequestsClient | None:
    if not COOKIE_FILE.is_file():
        log(f"cookie file missing: {COOKIE_FILE.name}")
        return None
    try:
        sess, _ = load_session()
    except SystemExit:
        return None
    if not verify_auth(sess):
        return None
    return RequestsClient(sess)


def open_cdp_client(cdp: str) -> tuple[CdpClient, Any, Any]:
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(cdp)
    ctx = browser.contexts[0]
    # Touch hub once so session is warm
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto(
            f"{BASE}/hc/en-us/community/posts/{DEFAULT_HUB_ID}",
            wait_until="domcontentloaded",
            timeout=120000,
        )
        page.wait_for_timeout(1500)
    except Exception as exc:  # noqa: BLE001
        log(f"cdp warm goto warn: {exc}")
    # Verify API via browser
    probe = ctx.request.get(f"{BASE}/api/v2/community/posts/{DEFAULT_HUB_ID}.json", timeout=60000)
    if probe.status != 200:
        pw.stop()
        raise RuntimeError(f"CDP API probe HTTP {probe.status} — login may have expired")
    log("cdp auth check OK")
    return CdpClient(ctx.request, page=page), browser, pw


def run_crawl(client: FetchClient, args: argparse.Namespace, transport: str) -> int:
    out_root: Path = args.out
    out_root.mkdir(parents=True, exist_ok=True)
    failures_path = out_root / "failures.jsonl"
    if failures_path.exists():
        failures_path.unlink()

    started = time.time()
    log("")
    log(f"Fetching hub {args.hub_id} via {transport} ...")
    try:
        _hub_post, _hub_html, entries = save_hub(client, args.hub_id, out_root, failures_path)
    except Exception as exc:  # noqa: BLE001
        log(f"hub fetch failed: {exc}")
        return 1

    log(f"Parsed {len(entries)} child posts from hub")
    if args.limit and args.limit > 0:
        entries = entries[: args.limit]
        log(f"Limiting to first {len(entries)} (--limit)")

    results: list[PostResult] = []
    images_total = 0
    for i, entry in enumerate(entries, 1):
        log(f"[{i}/{len(entries)}] {entry.post_id}  {entry.title[:60]}")
        res = crawl_one(client, entry, out_root, failures_path)
        results.append(res)
        images_total += res.image_count
        mark = "OK" if res.status == "ok" else f"FAIL ({res.error})"
        log(f"    -> {mark}  images={res.image_count} comments={res.comment_count}")

    write_index(out_root, results, args.hub_id)
    ok_n = sum(1 for r in results if r.status == "ok")
    fail_n = sum(1 for r in results if r.status != "ok")
    summary = {
        "hub_id": args.hub_id,
        "transport": transport,
        "total": len(results),
        "ok": ok_n,
        "fail": fail_n,
        "images": images_total,
        "elapsed_sec": int(time.time() - started),
        "output": str(out_root),
        "results": [asdict(r) for r in results],
    }
    _safe_write_json(out_root / "summary.json", summary)
    log("")
    log("=" * 60)
    log(
        f"DONE  total={len(results)} ok={ok_n} fail={fail_n} "
        f"images={images_total}  {summary['elapsed_sec']}s  via={transport}"
    )
    log(f"output: {out_root}")
    log("=" * 60)
    if ok_n == 0 and len(results) > 0:
        return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Crawl Alpha Inspiration hub child posts.")
    ap.add_argument("--hub-id", default=DEFAULT_HUB_ID, help="Hub post id")
    ap.add_argument("--limit", type=int, default=0, help="Only crawl first N children (debug)")
    ap.add_argument("--out", type=Path, default=OUTPUT, help="Output directory")
    ap.add_argument(
        "--cdp",
        nargs="?",
        const=DEFAULT_CDP,
        default=None,
        help=f"Use Chrome CDP (default endpoint {DEFAULT_CDP}). Auto-fallback if cookie auth fails.",
    )
    ap.add_argument(
        "--cdp-fallback",
        action="store_true",
        default=True,
        help=argparse.SUPPRESS,
    )
    ap.add_argument("--no-cdp-fallback", action="store_true", help="Do not auto-use CDP on cookie auth failure")
    ap.add_argument(
        "--backfill-images",
        action="store_true",
        help="Only re-download images for existing posts under --out and rewrite Markdown",
    )
    args = ap.parse_args()

    def with_client(client: FetchClient, transport: str) -> int:
        if args.backfill_images:
            out_root: Path = args.out
            failures_path = out_root / "failures_images.jsonl"
            log(f"Backfilling images via {transport} ...")
            stats = backfill_images(client, out_root, failures_path)
            log(f"backfill done: {stats}")
            # Update summary images count if present
            summary_path = out_root / "summary.json"
            if summary_path.exists():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    summary["images"] = stats["images_ok"]
                    summary["image_backfill"] = stats
                    _safe_write_json(summary_path, summary)
                except Exception:
                    pass
            return 0 if stats["images_ok"] > 0 or stats["with_media"] == 0 else 2
        return run_crawl(client, args, transport)

    # Explicit --cdp
    if args.cdp:
        try:
            client, browser, pw = open_cdp_client(args.cdp)
        except Exception as exc:  # noqa: BLE001
            log(f"CDP connect failed: {exc}")
            print_auth_help()
            return 1
        try:
            return with_client(client, f"cdp:{args.cdp}")
        finally:
            try:
                browser.close()
            except Exception:
                pass
            pw.stop()

    # Cookie / requests first
    client = try_requests_client()
    if client is not None and not args.backfill_images:
        return with_client(client, "requests+cookie")
    if client is not None and args.backfill_images:
        # Image CDN usually needs browser fetch; prefer CDP even if cookie works.
        log("backfill-images prefers CDP page.fetch; trying CDP first...")

    if args.no_cdp_fallback and not args.cdp:
        if client is not None:
            return with_client(client, "requests+cookie")
        print_auth_help()
        return 1

    log("opening CDP ...")
    try:
        client2, browser, pw = open_cdp_client(DEFAULT_CDP)
    except Exception as exc:  # noqa: BLE001
        log(f"CDP failed: {exc}")
        if client is not None:
            return with_client(client, "requests+cookie")
        print_auth_help()
        return 1
    try:
        return with_client(client2, f"cdp:{DEFAULT_CDP}")
    finally:
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()


if __name__ == "__main__":
    raise SystemExit(main())
