"""AI model clients - OpenRouter prioritized, Gemini fallback."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

import requests

from .config import (
    GEMINI_MAX_RETRIES,
    GEMINI_MODEL,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODELS,
)
from .utils import log


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace in text."""
    return re.sub(r"\s+", " ", text).strip()


def _gemini_endpoint(model: str, api_key: str) -> str:
    """Build Gemini API endpoint."""
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )


def _openrouter_endpoint() -> str:
    """Build OpenRouter API endpoint."""
    return f"{OPENROUTER_BASE_URL}/chat/completions"


def _parse_json_response(text: str) -> dict[str, Any]:
    """Parse JSON from response text."""
    text = normalize_whitespace(text)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def call_openrouter(
    session: requests.Session,
    api_key: str,
    prompt: str,
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 8192,
) -> dict[str, Any]:
    """Call OpenRouter API."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_REFERER", "https://github.com"),
        "X-Title": "Daily Tool Hub",
    }

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    resp = session.post(
        _openrouter_endpoint(),
        headers=headers,
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("OpenRouter returned empty choices")

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("OpenRouter returned empty content")

    return _parse_json_response(content)


def call_gemini(
    session: requests.Session,
    api_key: str,
    prompt: str,
    model: str = GEMINI_MODEL,
    temperature: float = 0.7,
    retries: int | None = None,
) -> dict[str, Any]:
    """Call Gemini API with retries."""
    endpoint = _gemini_endpoint(model, api_key)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt.strip()}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }

    max_retries = max(1, retries if retries is not None else GEMINI_MAX_RETRIES)
    data: dict[str, Any] | None = None

    for attempt in range(1, max_retries + 1):
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
        if not should_retry or attempt == max_retries:
            resp.raise_for_status()
        retry_after = resp.headers.get("Retry-After", "").strip()
        delay = min(60, int(retry_after)) if retry_after.isdigit() else min(60, 2**attempt)
        log(f"warn: Gemini status={resp.status_code}, retry in {delay}s ({attempt}/{max_retries})")
        time.sleep(delay)

    if data is None:
        raise RuntimeError("Gemini request failed after retries.")

    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError("Gemini returned empty content.")

    return _parse_json_response(text)


def generate_json_with_fallback(
    session: requests.Session,
    prompt: str,
    openrouter_key: str | None,
    gemini_key: str | None,
    temperature: float = 0.7,
) -> dict[str, Any]:
    """Generate JSON using OpenRouter first, fallback to Gemini."""
    errors: list[str] = []

    # Try OpenRouter first
    if openrouter_key:
        models = OPENROUTER_MODELS.copy()
        env_model = os.getenv("OPENROUTER_MODEL_NAME", "").strip()
        if env_model:
            models = [env_model] + [m for m in models if m != env_model]

        for model in models:
            try:
                log(f"Trying OpenRouter model: {model}")
                return call_openrouter(
                    session, openrouter_key, prompt, model, temperature=temperature
                )
            except Exception as exc:
                errors.append(f"OpenRouter {model}: {exc}")
                continue

    # Fallback to Gemini
    if gemini_key:
        try:
            log("Falling back to Gemini")
            return call_gemini(session, gemini_key, prompt, temperature=temperature)
        except Exception as exc:
            errors.append(f"Gemini: {exc}")

    raise RuntimeError("All AI models failed. " + " | ".join(errors[:5]))


def build_classifier_prompt(post: Any) -> str:
    """Build prompt for productivity classification."""
    return f"""
You are a strict Product Hunt classifier.
Decide whether this launch is primarily a productivity/work-efficiency tool.

Output JSON only:
{{
  "related": true or false,
  "reason": "short Chinese reason within 20 chars"
}}

Judging standard:
- related=true only when the core value is improving work/study/creation efficiency
  (e.g. writing, coding, automation, task/project management, notes, scheduling, knowledge workflow).
- related=false for games, entertainment, pure social, shopping, finance/crypto speculation,
  media consumption, wallpapers/avatars, and tools not focused on productivity.

Name: {post.name}
Tagline: {post.tagline}
Description: {post.description}
Topics: {json.dumps(post.topics, ensure_ascii=False)}
Product Hunt URL: {post.ph_url}
Website: {post.website or ""}
""".strip()


def build_writer_prompt(post: Any, image_urls: list[str]) -> str:
    """Build prompt for article generation."""
    return f"""
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
10) Keep left/right spacing very small in your inline styles (1-2px), because WeChat already applies page padding.
11) Do not output anything outside JSON.

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


def classify_productivity(
    session: requests.Session,
    post: Any,
    openrouter_key: str | None,
    gemini_key: str | None,
) -> tuple[bool, str]:
    """Classify if post is productivity-related."""
    prompt = build_classifier_prompt(post)
    result = generate_json_with_fallback(
        session, prompt, openrouter_key, gemini_key, temperature=0.1
    )

    related_raw = result.get("related")
    if isinstance(related_raw, bool):
        related = related_raw
    elif isinstance(related_raw, (int, float)):
        related = bool(related_raw)
    else:
        related = str(related_raw or "").strip().lower() in {"true", "1", "yes", "related"}
    reason = str(result.get("reason", "")).strip()[:40]
    return related, reason


def generate_article(
    session: requests.Session,
    post: Any,
    image_urls: list[str],
    openrouter_key: str | None,
    gemini_key: str | None,
) -> dict[str, Any]:
    """Generate article content."""
    prompt = build_writer_prompt(post, image_urls)
    return generate_json_with_fallback(
        session, prompt, openrouter_key, gemini_key, temperature=0.75
    )
