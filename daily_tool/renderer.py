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


def enforce_inner_side_spacing(html_fragment: str, px: int = 2) -> str:
    """Enforce inner side spacing in HTML."""
    soup = BeautifulSoup(html_fragment or "", "html.parser")
    side_style = (
        f"padding-left:{px}px;padding-right:{px}px;"
        f"margin-left:{px}px;margin-right:{px}px;"
    )
    skip_tags = {"img", "br", "hr", "source"}
    for tag in soup.find_all(True):
        if tag.name.lower() in skip_tags:
            continue
        style = str(tag.get("style", "")).strip()
        if style:
            style = re.sub(
                r"(?i)(?:^|;)\s*(padding-left|padding-right|margin-left|margin-right)\s*:[^;]*",
                "",
                style,
            )
            style = re.sub(r";{2,}", ";", style).strip(" ;")
        if style:
            style += ";"
        tag["style"] = style + side_style
    return str(soup)


def ensure_wxhtml(
    wxhtml: str,
    title: str,
    summary: str,
    post: Any,
    theme_key: str,
    github_images: list[str],
) -> str:
    """Ensure WeChat HTML has all required elements."""
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
            "<figure style='margin:0 0 14px 0;padding:6px 2px;border-radius:10px;border:1px solid #e5e7eb;'>"
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

    missing_images = [u for u in github_images if u not in body]
    for u in missing_images:
        body += (
            f"<figure style='margin:16px 0;padding:8px 2px;border:1px solid {theme['accent']};"
            "border-radius:10px;'>"
            f"<img src='{escape(u)}' style='width:100%;height:auto;border-radius:6px;'/>"
            "</figure>"
        )

    tags = build_tags(post)
    tags_text = " ".join(tags)
    website_block = (
        f"<section style='margin-top:10px;padding:10px 2px;border:1px dashed #cbd5e1;border-radius:10px;'>"
        f"<p style='margin:0;font-size:14px;'>工具官网：<a href='{escape(post.website)}'>{escape(post.website)}</a></p>"
        "</section>"
        if post.website
        else ""
    )

    body += (
        f"<section style='margin-top:16px;padding:12px 2px;border:1px solid {theme['accent']};"
        "border-radius:10px;background:#f8fafc;'>"
        "<h3 style='margin:0 0 8px;font-size:18px;'>快速结论</h3>"
        f"<p style='margin:0;'>{escape(summary)}</p>"
        f"<p style='margin:10px 0 0;color:#334155;font-size:14px;'>标签：{escape(tags_text)}</p>"
        "</section>"
        f"{website_block}"
    )
    body = enforce_inner_side_spacing(body, px=2)

    return (
        f"<section style='font-size:16px;line-height:1.78;color:{theme['fg']};padding:0 2px;'>"
        "<section style='margin:0 0 14px;'>"
        f"<img src='{HEADER_IMG}' style='width:100%;height:auto;display:block;border-radius:12px;'/>"
        "</section>"
        f"<section style='padding:14px 4px;border-radius:12px;background:{theme['bg']};"
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
        "<section style='padding:14px 2px;border-radius:12px;background:#ffffff;color:#111827;border:1px solid #e5e7eb;'>"
        f"{body}"
        "</section>"
        "</section>"
    )
