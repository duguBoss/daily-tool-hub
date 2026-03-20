#!/usr/bin/env python3
import json
import mimetypes
import os
import re
import shutil
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
ASSETS_DIR = ROOT / "assets" / "today"
POST_JSON = OUTPUT_DIR / "post.json"
SEEN_FILE = DATA_DIR / "seen_posts.json"
HEADER_IMG = (
    "https://mmbiz.qpic.cn/mmbiz_gif/"
    "xm1dT1jCe8lIO3P2oFVtd1x040PKGCRPN033gUTrHQQz0Licdqug5X1QgUPQBRCicoTqdYMrpgk7etibXLkK9rwcg/0"
    "?wx_fmt=gif&from=appmsg"
)

PH_ENDPOINT = "https://api.producthunt.com/v2/api/graphql"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "5"))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

THEMES = {
    "ai": {
        "bg": "#0b1220",
        "fg": "#e5e7eb",
        "accent": "#38bdf8",
        "card": "#0f172a",
        "name": "AI Tool Radar",
    },
    "builder": {
        "bg": "#111827",
        "fg": "#f9fafb",
        "accent": "#f59e0b",
        "card": "#1f2937",
        "name": "Builder Picks",
    },
    "growth": {
        "bg": "#0f172a",
        "fg": "#f8fafc",
        "accent": "#34d399",
        "card": "#1e293b",
        "name": "Growth Stack",
    },
}


@dataclass
class ToolPost:
    id: str
    name: str
    tagline: str
    description: str
    ph_url: str
    website: str | None
    votes: int
    comments: int
    posted_at: str
    topics: list[str]
    image_urls: list[str]


def log(msg: str) -> None:
    print(f"[daily-tool-hub] {msg}", flush=True)


def normalize_url(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u:
        return None
    if u.startswith("//"):
        return "https:" + u
    if not (u.startswith("http://") or u.startswith("https://")):
        return None
    return u


def load_seen_state() -> tuple[set[str], set[str]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SEEN_FILE.exists():
        return set(), set()
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            # Backward compatibility with old format: list of ids only.
            return {str(x).strip() for x in data if str(x).strip()}, set()
        if isinstance(data, dict):
            ids_raw = data.get("seen_ids", [])
            fps_raw = data.get("seen_fingerprints", [])
            ids = {str(x).strip() for x in ids_raw if str(x).strip()}
            fps = {str(x).strip() for x in fps_raw if str(x).strip()}
            return ids, fps
    except Exception as exc:
        log(f"warn: cannot parse seen file: {exc}")
    return set(), set()


def save_seen_state(ids: set[str], fingerprints: set[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "seen_ids": sorted(ids),
        "seen_fingerprints": sorted(fingerprints),
    }
    SEEN_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def tool_fingerprint(post: ToolPost) -> str:
    website = (post.website or "").strip().lower()
    if website:
        parsed = urlparse(website)
        host = parsed.netloc.replace("www.", "")
        path = parsed.path.rstrip("/")
        return f"w:{host}{path}"
    ph_url = (post.ph_url or "").strip().lower()
    if ph_url:
        parsed = urlparse(ph_url)
        path = parsed.path.rstrip("/")
        if path:
            return f"ph:{path}"
    normalized_name = re.sub(r"\s+", "", (post.name or "").strip().lower())
    return f"n:{normalized_name}"


def clean_generated_outputs() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    if ASSETS_DIR.exists():
        shutil.rmtree(ASSETS_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def request_json(
    session: requests.Session, method: str, url: str, **kwargs: Any
) -> dict[str, Any]:
    headers = kwargs.pop("headers", {})
    merged = {"User-Agent": USER_AGENT, **headers}
    resp = session.request(method, url, headers=merged, timeout=40, **kwargs)
    resp.raise_for_status()
    return resp.json()


def ph_graphql(
    session: requests.Session, token: str, query: str, variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = {"query": query, "variables": variables or {}}
    return request_json(
        session,
        "POST",
        PH_ENDPOINT,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )


def parse_topics(node: dict[str, Any]) -> list[str]:
    topics: list[str] = []
    topic_obj = node.get("topic")
    if isinstance(topic_obj, dict):
        name = (topic_obj.get("name") or "").strip()
        if name:
            topics.append(name)
    topics_obj = node.get("topics")
    if isinstance(topics_obj, dict):
        for edge in topics_obj.get("edges", []) or []:
            n = (edge.get("node", {}) or {}).get("name")
            if isinstance(n, str) and n.strip():
                topics.append(n.strip())
    return list(dict.fromkeys(topics))


def parse_node_images(node: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("thumbnailUrl", "screenshotUrl"):
        u = normalize_url(node.get(key))
        if u:
            urls.append(u)
    thumbnail = node.get("thumbnail")
    if isinstance(thumbnail, dict):
        for key in ("url", "imageUrl"):
            u = normalize_url(thumbnail.get(key))
            if u:
                urls.append(u)

    def append_media_item(media_item: dict[str, Any]) -> None:
        if not isinstance(media_item, dict):
            return
        m_type = str(media_item.get("type", "")).lower()
        if m_type and "image" not in m_type and "screenshot" not in m_type:
            return
        for k in ("url", "imageUrl"):
            u = normalize_url(media_item.get(k))
            if u:
                urls.append(u)

    media = node.get("media")
    if isinstance(media, list):
        for m in media:
            append_media_item(m)
    elif isinstance(media, dict):
        for edge in media.get("edges", []) or []:
            append_media_item((edge or {}).get("node", {}) or {})
    return list(dict.fromkeys(urls))


def to_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def parse_posts_from_response(data: dict[str, Any]) -> list[ToolPost]:
    posts_obj = (data.get("data", {}) or {}).get("posts")
    if not isinstance(posts_obj, dict):
        return []
    posts: list[ToolPost] = []
    for edge in posts_obj.get("edges", []) or []:
        node = (edge or {}).get("node", {}) or {}
        pid = str(node.get("id") or "").strip()
        name = str(node.get("name") or "").strip()
        ph_url = normalize_url(node.get("url"))
        if not pid or not name or not ph_url:
            continue

        website = normalize_url(node.get("website"))
        post = ToolPost(
            id=pid,
            name=name,
            tagline=str(node.get("tagline") or "").strip(),
            description=str(node.get("description") or "").strip(),
            ph_url=ph_url,
            website=website,
            votes=to_int(node.get("votesCount")),
            comments=to_int(node.get("commentsCount")),
            posted_at=str(node.get("postedAt") or node.get("createdAt") or "").strip(),
            topics=parse_topics(node),
            image_urls=parse_node_images(node),
        )
        posts.append(post)
    return posts


def fetch_posts(session: requests.Session, token: str, first: int = 30) -> list[ToolPost]:
    queries = [
        """
        query FetchPosts($first: Int!) {
          posts(first: $first, order: NEWEST) {
            edges {
              node {
                id
                name
                tagline
                description
                url
                website
                votesCount
                commentsCount
                postedAt
                thumbnailUrl
                thumbnail { url }
                topic { name }
                topics(first: 5) { edges { node { name } } }
                media(first: 8) {
                  edges {
                    node {
                      type
                      url
                      imageUrl
                      videoUrl
                    }
                  }
                }
              }
            }
          }
        }
        """,
        """
        query FetchPosts($first: Int!) {
          posts(first: $first, order: NEWEST) {
            edges {
              node {
                id
                name
                tagline
                description
                url
                website
                votesCount
                commentsCount
                postedAt
              }
            }
          }
        }
        """,
        """
        query FetchPosts($first: Int!) {
          posts(first: $first, order: NEWEST) {
            edges {
              node {
                id
                name
                tagline
                url
                website
              }
            }
          }
        }
        """,
    ]

    errors: list[str] = []
    for q in queries:
        try:
            data = ph_graphql(session, token=token, query=q, variables={"first": first})
            gql_errors = data.get("errors") or []
            if gql_errors:
                errors.append("; ".join(str(e.get("message", e)) for e in gql_errors))
                continue
            posts = parse_posts_from_response(data)
            if posts:
                return posts
        except Exception as exc:
            errors.append(str(exc))
            continue
    raise RuntimeError("Failed to fetch Product Hunt posts. " + " | ".join(errors[:3]))


def scrape_meta_images(session: requests.Session, url: str) -> list[str]:
    try:
        html = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30).text
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for meta in soup.select("meta[property='og:image'],meta[name='twitter:image']"):
        u = normalize_url(meta.get("content"))
        if u:
            urls.append(u)
    for img in soup.select("img"):
        src = img.get("src") or img.get("data-src")
        u = normalize_url(src)
        if not u:
            continue
        lu = u.lower()
        if any(k in lu for k in ("logo", "avatar", "icon", "sprite")):
            continue
        urls.append(u)
        if len(urls) >= 6:
            break
    return list(dict.fromkeys(urls))


def enrich_post(session: requests.Session, post: ToolPost) -> ToolPost:
    image_urls = list(post.image_urls)
    if not image_urls:
        image_urls.extend(scrape_meta_images(session, post.ph_url))
    if post.website:
        image_urls.extend(scrape_meta_images(session, post.website))
    image_urls = list(dict.fromkeys(image_urls))
    return ToolPost(
        id=post.id,
        name=post.name,
        tagline=post.tagline,
        description=post.description,
        ph_url=post.ph_url,
        website=post.website,
        votes=post.votes,
        comments=post.comments,
        posted_at=post.posted_at,
        topics=post.topics,
        image_urls=image_urls,
    )


def guess_ext(url: str, content_type: str | None) -> str:
    ext = Path(urlparse(url).path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg"}:
        return ext
    if content_type:
        guess = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guess:
            return ".jpg" if guess == ".jpe" else guess
    return ".jpg"


def to_github_raw_url(rel_path: Path) -> str:
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    branch = os.getenv("GITHUB_REF_NAME", "").strip() or "main"
    path = rel_path.as_posix().lstrip("/")
    if repo:
        return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
    return path


def download_images(
    session: requests.Session, image_urls: list[str], limit: int = 12
) -> list[str]:
    saved: list[str] = []
    for idx, url in enumerate(image_urls[:limit], start=1):
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=40, stream=True)
            if resp.status_code != 200:
                continue
            ext = guess_ext(url, resp.headers.get("content-type"))
            file = ASSETS_DIR / f"cover_{idx:02d}{ext}"
            with file.open("wb") as fw:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        fw.write(chunk)
            saved.append(to_github_raw_url(file.relative_to(ROOT)))
        except Exception as exc:
            log(f"warn: image download failed: {url} ({exc})")
    return saved


def choose_theme(post: ToolPost) -> str:
    topic_line = " ".join(post.topics).lower()
    if "ai" in topic_line or "artificial intelligence" in topic_line:
        return "ai"
    if "developer" in topic_line or "code" in topic_line or "no code" in topic_line:
        return "builder"
    if post.votes >= 400:
        return "growth"
    return list(THEMES.keys())[hash(post.id) % len(THEMES)]


def build_tags(post: ToolPost) -> list[str]:
    tags = []
    for t in post.topics[:5]:
        tags.append(f"#{t.replace(' ', '')}")
    base = ["#产品工具", "#效率提升", "#每日上新"]
    for b in base:
        if b not in tags:
            tags.append(b)
    return tags[:8]


def clamp_summary(text: str, max_len: int = 30) -> str:
    s = re.sub(r"\s+", "", str(text or "")).strip()
    s = re.sub(r"[。！？!?.]+$", "", s)
    if not s:
        return "今日工具速览：值得试用的新工具"
    return s[:max_len]


def make_click_title(text: str, post: ToolPost) -> str:
    raw = re.sub(r"\s+", "", str(text or "")).strip()
    click_words = ["实测", "爆火", "效率翻倍", "别错过", "神器", "上手"]
    if not raw:
        raw = f"实测{post.name}：这工具真能提效"
    if not any(word in raw for word in click_words):
        raw = f"实测{raw}"
    raw = re.sub(r"[。！？!?.]+$", "", raw)
    return raw[:30]


def call_gemini(
    session: requests.Session,
    api_key: str,
    post: ToolPost,
    image_urls: list[str],
) -> dict[str, Any]:
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )
    prompt = f"""
You are a product analyst and Chinese tech editor.
Turn today's Product Hunt launch into a practical WeChat article.

Output JSON only with exactly:
- title
- summary
- wxhtml

Requirements:
1) title: Simplified Chinese, 20-30 chars, attractive but factual, with high-click hook style suitable for WeChat recommendation feed.
2) summary: Simplified Chinese, 15-30 chars.
3) wxhtml: body fragment only, no markdown, no script.
4) Content should be practical and detailed (around 1000+ Chinese chars).
5) Structure suggestion: one-line positioning, core highlights, target users, use cases, 3-step onboarding, alternatives.
6) Use provided image URLs as much as possible via <img>.
7) The layout must be mixed and visual: use cards/lists/checklists/quote blocks; avoid long pure-text wall.
8) WeChat recommendation style:
   - first screen must have a strong hook sentence;
   - use short paragraphs and numbered section headers;
   - include explicit benefit statements and scenario-based copy;
   - style should be eye-catching but not fake/exaggerated.
9) Avoid repetitive or generic AI-style wording.
10) Do not output anything outside JSON.

Tool name: {post.name}
Tagline: {post.tagline}
Description: {post.description}
Votes: {post.votes}
Comments: {post.comments}
Topics: {json.dumps(post.topics, ensure_ascii=False)}
Product Hunt URL: {post.ph_url}
Official Website: {post.website or ''}
Available image URLs: {json.dumps(image_urls, ensure_ascii=False)}
""".strip()

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.75, "responseMimeType": "application/json"},
    }

    data: dict[str, Any] | None = None
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        resp = session.post(
            endpoint,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if resp.status_code < 400:
            data = resp.json()
            break
        should_retry = resp.status_code == 429 or 500 <= resp.status_code <= 599
        if not should_retry or attempt == GEMINI_MAX_RETRIES:
            resp.raise_for_status()
        retry_after = resp.headers.get("Retry-After", "").strip()
        delay = min(60, int(retry_after)) if retry_after.isdigit() else min(60, 2 ** attempt)
        log(f"warn: Gemini status={resp.status_code}, retry in {delay}s ({attempt}/{GEMINI_MAX_RETRIES})")
        time.sleep(delay)

    if data is None:
        raise RuntimeError("Gemini request failed after retries.")

    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError("Gemini returned empty content.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise RuntimeError("Gemini response is not valid JSON.")
        return json.loads(match.group(0))


def ensure_wxhtml(
    wxhtml: str,
    title: str,
    summary: str,
    post: ToolPost,
    theme_key: str,
    github_images: list[str],
) -> str:
    theme = THEMES[theme_key]
    body = (wxhtml or "").strip()
    if not body:
        body = (
            f"<section><h3>{escape(post.name)}</h3>"
            "<p>这是一款值得关注的产品工具，以下为你整理核心价值与上手建议。</p>"
            "</section>"
        )

    body = re.sub(r"<script[\s\S]*?</script>", "", body, flags=re.I)
    body = re.sub(r"</?(html|head|body)[^>]*>", "", body, flags=re.I)

    # If model output has no image at all, inject one near the top.
    if "<img" not in body.lower() and github_images:
        body = (
            "<figure style='margin:0 0 14px 0;padding:6px;border-radius:10px;border:1px solid #e5e7eb;'>"
            f"<img src='{escape(github_images[0])}' style='width:100%;height:auto;border-radius:8px;'/>"
            "</figure>"
            + body
        )

    text_len = len(BeautifulSoup(body, "html.parser").get_text(" ", strip=True))
    if text_len < 700:
        body += (
            "<section style='margin-top:14px;'>"
            "<h3 style='margin:0 0 8px;font-size:20px;'>上手建议（3步）</h3>"
            "<p>第一步：先用最小场景快速验证工具是否解决你的核心问题，不要一次性迁移全部流程。"
            "第二步：设置1-2个可量化指标（如节省时间、转化率、交付速度）做一周观察。"
            "第三步：把稳定动作沉淀成模板，让团队其他成员可复制，避免只停留在尝鲜阶段。</p>"
            "</section>"
        )

    overview = (
        f"<section style='margin:0 0 14px 0;padding:12px;border-radius:10px;background:{theme['card']};color:#e5e7eb;'>"
        "<div style='display:flex;flex-wrap:wrap;gap:8px;'>"
        f"<span style='padding:4px 8px;border-radius:999px;background:{theme['accent']};color:#0b1220;font-size:12px;'>"
        f"{escape(post.name)}</span>"
        f"<span style='padding:4px 8px;border-radius:999px;background:#334155;font-size:12px;'>▲ {post.votes} votes</span>"
        f"<span style='padding:4px 8px;border-radius:999px;background:#334155;font-size:12px;'>💬 {post.comments} comments</span>"
        "</div>"
        f"<p style='margin:10px 0 0 0;font-size:18px;line-height:1.55;font-weight:700;color:#f8fafc;'>"
        f"别只看热度，{escape(post.name)}值不值得用？</p>"
        f"<p style='margin:8px 0 0 0;font-size:14px;line-height:1.8;color:#e2e8f0;'>{escape(post.tagline or post.description or '')}</p>"
        "</section>"
    )
    body = overview + body

    missing_images = [u for u in github_images if u not in body]
    for u in missing_images:
        body += (
            f"<figure style='margin:16px 0;padding:8px;border:1px solid {theme['accent']};"
            "border-radius:10px;'>"
            f"<img src='{escape(u)}' style='width:100%;height:auto;border-radius:6px;'/>"
            "</figure>"
        )

    tags = build_tags(post)
    tags_text = " ".join(tags)
    website_block = (
        f"<section style='margin-top:10px;padding:10px;border:1px dashed #cbd5e1;border-radius:10px;'>"
        f"<p style='margin:0;font-size:14px;'>工具官网：<a href='{escape(post.website)}'>{escape(post.website)}</a></p>"
        "</section>"
        if post.website
        else ""
    )

    body += (
        f"<section style='margin-top:16px;padding:12px;border:1px solid {theme['accent']};"
        "border-radius:10px;background:#f8fafc;'>"
        "<h3 style='margin:0 0 8px;font-size:18px;'>快速结论</h3>"
        f"<p style='margin:0;'>{escape(summary)}</p>"
        f"<p style='margin:10px 0 0;color:#334155;font-size:14px;'>标签：{escape(tags_text)}</p>"
        "</section>"
        f"{website_block}"
    )

    return (
        f"<section style='font-size:16px;line-height:1.78;color:{theme['fg']};'>"
        "<section style='margin:0 0 14px;'>"
        f"<img src='{HEADER_IMG}' style='width:100%;height:auto;display:block;border-radius:12px;'/>"
        "</section>"
        f"<section style='padding:14px;border-radius:12px;background:{theme['bg']};"
        "border:1px solid #1f2937;margin-bottom:14px;'>"
        f"<p style='margin:0 0 6px;font-size:13px;color:{theme['accent']};'>{escape(theme['name'])}</p>"
        f"<h2 style='margin:0 0 10px;font-size:22px;line-height:1.4;'>{escape(title)}</h2>"
        "<div style='display:flex;gap:8px;flex-wrap:wrap;'>"
        f"<span style='padding:4px 8px;border-radius:999px;background:{theme['card']};font-size:12px;'>"
        f"▲ {post.votes} votes</span>"
        f"<span style='padding:4px 8px;border-radius:999px;background:{theme['card']};font-size:12px;'>"
        f"💬 {post.comments} comments</span>"
        "</div>"
        "</section>"
        "<section style='padding:14px;border-radius:12px;background:#ffffff;color:#111827;border:1px solid #e5e7eb;'>"
        f"{body}"
        "</section>"
        "</section>"
    )


def create_fallback_cover(post: ToolPost, theme_key: str) -> str:
    theme = THEMES.get(theme_key, THEMES["builder"])
    title = (post.name or "Product Hunt Tool")[:48]
    subtitle = (post.tagline or post.description or "Today on Product Hunt")[:92]
    # Lazy import so local lint/compile does not require Pillow until runtime.
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1280, 720
    image = Image.new("RGB", (width, height), theme["bg"])
    draw = ImageDraw.Draw(image)

    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 64)
        sub_font = ImageFont.truetype("DejaVuSans.ttf", 34)
        badge_font = ImageFont.truetype("DejaVuSans.ttf", 32)
        foot_font = ImageFont.truetype("DejaVuSans.ttf", 28)
    except Exception:
        title_font = ImageFont.load_default()
        sub_font = ImageFont.load_default()
        badge_font = ImageFont.load_default()
        foot_font = ImageFont.load_default()

    draw.rectangle((52, 52, 1228, 668), outline=theme["accent"], width=2)
    draw.text((96, 110), "DAILY TOOL RADAR", fill=theme["accent"], font=badge_font)
    draw.text((96, 220), title, fill="#FFFFFF", font=title_font)
    draw.text((96, 320), subtitle, fill="#D1D5DB", font=sub_font)
    draw.text((96, 622), "producthunt.com", fill="#93C5FD", font=foot_font)

    file = ASSETS_DIR / "cover_fallback.png"
    image.save(file, format="PNG")
    return to_github_raw_url(file.relative_to(ROOT))


def main() -> int:
    ph_token = os.getenv("PRODUCT_HUNT_TOKEN", "").strip()
    if not ph_token:
        raise RuntimeError("Missing PRODUCT_HUNT_TOKEN environment variable.")
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_api_key:
        raise RuntimeError("Missing GEMINI_API_KEY environment variable.")

    clean_generated_outputs()
    seen_ids, seen_fingerprints = load_seen_state()
    log(f"loaded seen state: ids={len(seen_ids)} fingerprints={len(seen_fingerprints)}")

    session = requests.Session()
    posts = fetch_posts(session, token=ph_token, first=30)
    log(f"fetched posts: {len(posts)}")

    unseen_posts = [
        p for p in posts if p.id not in seen_ids and tool_fingerprint(p) not in seen_fingerprints
    ]
    if not unseen_posts:
        # Fallback to id-based dedupe if all fingerprints hit (for compatibility).
        unseen_posts = [p for p in posts if p.id not in seen_ids]
    if not unseen_posts:
        raise RuntimeError("No unseen Product Hunt post in fetched results.")
    selected: ToolPost | None = None
    fallback_selected: ToolPost | None = None
    for candidate in unseen_posts[:10]:
        enriched = enrich_post(session, candidate)
        if fallback_selected is None:
            fallback_selected = enriched
        if enriched.image_urls:
            selected = enriched
            break
    if selected is None:
        selected = fallback_selected
    if selected is None:
        raise RuntimeError("No valid Product Hunt post after enrichment.")
    selected_fp = tool_fingerprint(selected)
    log(f"selected: {selected.name} ({selected.id}) fp={selected_fp}")
    log(f"source images found: {len(selected.image_urls)}")
    theme_key = choose_theme(selected)

    github_images = download_images(session, selected.image_urls, limit=12)
    if not github_images:
        fallback_cover = create_fallback_cover(selected, theme_key=theme_key)
        github_images = [fallback_cover]
        log("warn: no remote images found, generated fallback PNG cover.")
    log(f"images downloaded: {len(github_images)}")

    gemini = call_gemini(session, api_key=gemini_api_key, post=selected, image_urls=github_images)
    title = make_click_title(
        str(gemini.get("title", "")).strip() or f"{selected.name}：今天值得试的效率工具",
        selected,
    )
    summary = clamp_summary(str(gemini.get("summary", "")).strip())
    wxhtml_raw = str(gemini.get("wxhtml", "")).strip()

    wxhtml = ensure_wxhtml(
        wxhtml=wxhtml_raw,
        title=title,
        summary=summary,
        post=selected,
        theme_key=theme_key,
        github_images=github_images,
    )

    covers = list(dict.fromkeys(github_images + [selected.ph_url]))
    post_data = {"title": title, "covers": covers, "wxhtml": wxhtml, "summary": summary}
    POST_JSON.write_text(json.dumps(post_data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"written: {POST_JSON}")

    seen_ids.add(selected.id)
    seen_fingerprints.add(selected_fp)
    save_seen_state(seen_ids, seen_fingerprints)
    log(f"seen state updated: ids={len(seen_ids)} fingerprints={len(seen_fingerprints)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"error: {exc}")
        raise

