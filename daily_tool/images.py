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
    """Download images, process them (to JPG, <5MB), and return GitHub raw URLs."""
    from PIL import Image
    import io
    from .config import USER_AGENT

    saved: list[str] = []
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    for idx, url in enumerate(image_urls, start=1):
        if len(saved) >= limit:
            break
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, stream=True)
            if resp.status_code != 200:
                continue
            
            # Read content into memory to check size and process
            content = resp.content
            if not content:
                continue

            try:
                img = Image.open(io.BytesIO(content))
                # Convert to RGB if necessary (Alpha channel addressed by RGB conversion)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                file_name = f"cover_{len(saved) + 1:02d}.jpg"
                file_path = ASSETS_DIR / file_name

                # Initial save
                out_io = io.BytesIO()
                img.save(out_io, format="JPEG", quality=85)
                
                # Check size, if > 5MB, reduce quality or resize
                max_size = 5 * 1024 * 1024
                if out_io.tell() > max_size:
                    # Resize if it's huge
                    if img.width > 2560:
                        ratio = 2560 / img.width
                        img = img.resize((2560, int(img.height * ratio)), Image.Resampling.LANCZOS)
                    
                    # Try saving with lower quality
                    out_io = io.BytesIO()
                    img.save(out_io, format="JPEG", quality=70)
                
                if out_io.tell() > max_size:
                    log(f"warn: image still too large after processing: {url}")
                    continue

                with open(file_path, "wb") as f:
                    f.write(out_io.getvalue())
                
                saved.append(to_github_raw_url(file_path.relative_to(ROOT)))
                log(f"image saved: {file_name} from {url[:50]}...")
            except Exception as e:
                log(f"warn: image processing failed for {url}: {e}")
                continue

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

    file = ASSETS_DIR / "cover_fallback.jpg"
    image.save(file, format="JPEG", quality=90)
    return to_github_raw_url(file.relative_to(ROOT))
