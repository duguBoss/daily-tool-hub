"""Utility functions."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import DATA_DIR, ROOT, SEEN_FILE


def log(msg: str) -> None:
    """Print log message."""
    print(f"[daily-tool-hub] {msg}", flush=True)


def normalize_url(url: Any) -> str | None:
    """Normalize URL string."""
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
    """Load seen post IDs and fingerprints."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SEEN_FILE.exists():
        return set(), set()
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
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
    """Save seen post IDs and fingerprints."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "seen_ids": sorted(ids),
        "seen_fingerprints": sorted(fingerprints),
    }
    SEEN_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def tool_fingerprint(post: Any) -> str:
    """Generate fingerprint for deduplication."""
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


def clamp_summary(text: str, max_len: int = 30) -> str:
    """Clamp summary text length."""
    s = re.sub(r"\s+", "", str(text or "")).strip()
    s = re.sub(r"[。！？!?.]+$", "", s)
    if not s:
        return "今日工具速览：值得试用的新工具"
    return s[:max_len]


def make_click_title(text: str, post: Any) -> str:
    """Make click-worthy title."""
    raw = re.sub(r"\s+", "", str(text or "")).strip()
    click_words = ["实测", "爆火", "效率翻倍", "别错过", "神器", "上手"]
    if not raw:
        raw = f"实测{post.name}：这工具真能提效"
    if not any(word in raw for word in click_words):
        raw = f"实测{raw}"
    raw = re.sub(r"[。！？!?.]+$", "", raw)
    return raw[:30]


def guess_ext(url: str, content_type: str | None) -> str:
    """Guess file extension from URL and content type."""
    ext = Path(urlparse(url).path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg"}:
        return ext
    if content_type:
        import mimetypes

        guess = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guess:
            return ".jpg" if guess == ".jpe" else guess
    return ".jpg"
