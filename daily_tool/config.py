"""Configuration and constants."""

from __future__ import annotations

import os
from pathlib import Path

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
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

# Model policy requested by user (Sync with NASA):
PRIMARY_MODEL_NAME = "google/gemini-2.0-flash-001"
FALLBACK_MODEL_NAME = "google/gemini-2.0-flash-lite-001"

# OpenRouter Pool
OPENROUTER_MODELS = [
    "deepseek/deepseek-chat",
    "qwen/qwen-2.5-72b-instruct",
    "minimax/minimax-6b",
    "stepfun/step-3.5-flash:free",
]

# Gemini fallback
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "5"))

# Limits
MAX_PRODUCTIVITY_CHECKS = int(os.getenv("MAX_PRODUCTIVITY_CHECKS", "50"))
PH_FETCH_FIRST = int(os.getenv("PH_FETCH_FIRST", "50"))

# Themes
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


def get_env(key: str, default: str = "") -> str:
    """Get environment variable."""
    return os.getenv(key, default).strip()


def to_github_raw_url(rel_path: Path) -> str:
    """Convert local path to GitHub raw URL."""
    repo = get_env("GITHUB_REPOSITORY")
    branch = get_env("GITHUB_REF_NAME", "main")
    path = rel_path.as_posix().lstrip("/")
    if repo:
        return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
    return path
