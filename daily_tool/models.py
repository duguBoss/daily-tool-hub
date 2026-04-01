"""Data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolPost:
    """Product Hunt tool post."""

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


def to_int(v: Any) -> int:
    """Convert value to int safely."""
    try:
        return int(v)
    except Exception:
        return 0
