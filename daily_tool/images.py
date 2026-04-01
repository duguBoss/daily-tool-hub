"""Image download and processing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from .config import ASSETS_DIR, ROOT, to_github_raw_url
from .utils import guess_ext, log


def download_images(
    session: requests.Session, image_urls: list[str], limit: int = 12
) -> list[str]:
    """Download images and return GitHub raw URLs."""
    from .config import USER_AGENT

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


def create_fallback_cover(post: Any, theme_key: str) -> str:
    """Create fallback cover image using Pillow."""
    from .config import THEMES

    theme = THEMES.get(theme_key, THEMES["builder"])
    title = (post.name or "Product Hunt Tool")[:48]
    subtitle = (post.tagline or post.description or "Today on Product Hunt")[:92]

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
