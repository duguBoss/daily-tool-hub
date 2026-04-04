"""HTML rendering for WeChat."""

from __future__ import annotations

import re
from html import escape
from typing import Any

from bs4 import BeautifulSoup

from .config import HEADER_IMG, THEMES


def choose_theme(post: Any) -> str:
    """Choose theme based on post topics."""
    topic_line = " ".join(post.topics).lower()
    if "ai" in topic_line or "artificial intelligence" in topic_line:
        return "ai"
    if "developer" in topic_line or "code" in topic_line or "no code" in topic_line:
        return "builder"
    if post.votes >= 400:
        return "growth"
    return list(THEMES.keys())[hash(post.id) % len(THEMES)]


def build_tags(post: Any) -> list[str]:
    """Build tag list for post."""
    tags = []
    for t in post.topics[:5]:
        tags.append(f"#{t.replace(' ', '')}")
    base = ["#产品工具", "#效率提升", "#每日上新"]
    for b in base:
        if b not in tags:
            tags.append(b)
    return tags[:8]


def _distribute_images(body: str, images: list[str]) -> str:
    """Distribute up to 3 images gracefully among paragraphs."""
    if not images:
        return body
        
    # Limit to max 3 images as requested
    images = images[:3]
        
    soup = BeautifulSoup(body, "html.parser")
    paragraphs = soup.find_all("p")
    
    missing_images = [u for u in images if escape(u) not in body and str(u) not in body]
    if not missing_images:
        return body

    if not paragraphs:
        for u in missing_images:
            body += f"<section style='margin-top:20px;'><img src='{escape(u)}' style='width:100%;height:auto;margin-bottom:12px;display:block;border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,0.05);'/></section>"
        return body

    num_p = len(paragraphs)
    num_img = len(missing_images)
    
    # Better distribution logic: spacing them out more evenly
    step = max(1, num_p // (num_img + 1))
    
    for i, img_url in enumerate(missing_images):
        idx = (i + 1) * step - 1
        if idx >= num_p:
            idx = num_p - 1
        if idx < 0:
            idx = 0
            
        target_p = paragraphs[idx]
        img_tag = soup.new_tag("img", src=img_url, style="width:100%;height:auto;margin:24px 0;display:block;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.08);")
        target_p.insert_after(img_tag)
        
    for empty_img in soup.find_all("img"):
        src = empty_img.get("src", "")
        if not src or "素材URL" in src or "素材图片URL" in src or empty_img.get("src") == "":
            empty_img.decompose()
            
    return str(soup)


def ensure_wxhtml(
    wxhtml: str,
    title: str,
    summary: str,
    post: Any,
    theme_key: str,
    github_images: list[str],
) -> str:
    """Ensure WeChat HTML has all required premium elements."""
    body = (wxhtml or "").strip()
    if not body:
        body = (
            f"<h2 style='font-size:18px;font-weight:600;color:#0f172a;margin:28px 0 12px 0;border-bottom:1px solid #e2e8f0;padding-bottom:6px;'>{escape(post.name)} 上手建议</h2>"
            "<p style='margin:0 0 16px;color:#334155;font-size:16px;line-height:1.7;'>这是一款值得关注的产品工具，这可能会改变你的工作流。</p>"
        )

    # Clean residual script/html tags from AI output
    body = re.sub(r"<script[\s\S]*?</script>", "", body, flags=re.I)
    body = re.sub(r"</?(html|head|body)[^>]*>", "", body, flags=re.I)

    # Ensure minimum content padding if too short
    text_len = len(BeautifulSoup(body, "html.parser").get_text(" ", strip=True))
    if text_len < 700:
        body += (
             "<h2 style='font-size:18px;font-weight:600;color:#0f172a;margin:28px 0 12px 0;border-bottom:1px solid #e2e8f0;padding-bottom:6px;'>🛠 建议落地方案</h2>"
             "<p style='margin:0 0 16px;color:#334155;font-size:16px;line-height:1.7;'>建议安全团队及效率达人按以下优先级体验：<br><strong style='color:#0369a1;'>1. 极速验证：</strong> 用最小场景验证能否在5分钟内打通你的核心痛点链路。<br><strong style='color:#0369a1;'>2. 指标追踪：</strong> 观察一周该工具带来的时间节省程度与产出转化率提升。<br><strong style='color:#0369a1;'>3. 固化模板：</strong> 建立标准SOP模板，让更多团队成员轻松复用你的跑通经验。</p>"
        )

    # Distribute images (capped at 3 and better spacing)
    body = _distribute_images(body, github_images)

    # Tags with horizontal scroll
    tags = build_tags(post)
    tags_text = "".join([f"<span style='display:inline-block;margin:0 12px 0 0;color:#64748b;font-size:13px;white-space:nowrap;'>{escape(t)}</span>" for t in tags])
    
    # Website link with clear separation (single line, no wrap)
    website_block = (
        f"<p style='margin:0 0 18px 0;font-size:15px;color:#0f172a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'><strong style='color:#0369a1;'>🌍 直达官网：</strong><a href='{escape(post.website)}' style='color:#0284c7;text-decoration:none;font-size:14px;'>{escape(post.website)}</a></p>"
        if post.website
        else ""
    )
    
    # Intelligence summary tail block
    body += (
        "<section style='margin-top:32px;padding:16px 20px;background-color:#f8fafc;border-radius:6px;border-left:4px solid #0369a1;'>"
        "<p style='margin:0;color:#0f172a;font-size:15px;line-height:1.7;font-weight:500;'><span style='color:#0369a1;font-weight:600;margin-right:8px;'>核心点评：</span>"
        f"{escape(summary)}</p>"
        "</section>"
        f"<section style='margin-top:28px;border-top:1px dashed #cbd5e1;padding-top:16px;'>"
        f"{website_block}"
        f"<section style='overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch;padding-bottom:8px;'>{tags_text}</section>"
        f"</section>"
    )

    # Final wrap (Removed H1 Title and Votes/Comments from header per user request)
    return (
        "<section style=\"font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif;font-size:16px;color:#333;line-height:1.7;background-color:#fff;text-align:justify;word-wrap:break-word;\">"
        f"<section style=\"width:100%;margin-bottom:28px;\">"
        f"<img src=\"{HEADER_IMG}\" style=\"width:100%;display:block;\" alt=\"Header\"/>"
        "</section>"
        "<section>"
        f"<section style=\"margin-bottom:24px;\">"
        f"<section style=\"display:flex;gap:8px;align-items:center;\">"
        f"<span style=\"display:inline-block;padding:2px 8px;background-color:#dbeafe;color:#1e3a8a;font-size:12px;font-weight:500;border-radius:2px;letter-spacing:0.5px;\">效率先锋</span>"
        "</section>"
        "</section>"
        f"{body}"
        "</section>"
        "</section>"
    )
