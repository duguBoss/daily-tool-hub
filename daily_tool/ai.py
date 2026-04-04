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
    PRIMARY_MODEL_NAME,
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
    """Generate JSON using NASA-style priority: Primary -> OpenRouter Pool -> Gemini Fallback."""
    errors: list[str] = []

    # 1. Try OpenRouter prioritized models
    if openrouter_key:
        # Build candidates: PRIMARY first, then the rest of the pool
        candidates = [PRIMARY_MODEL_NAME] + [m for m in OPENROUTER_MODELS if m != PRIMARY_MODEL_NAME]
        
        for model in candidates:
            try:
                log(f"Trying AI model (OpenRouter): {model}")
                return call_openrouter(
                    session, openrouter_key, prompt, model, temperature=temperature
                )
            except Exception as exc:
                errors.append(f"OpenRouter {model}: {exc}")
                log(f"warn: Model {model} failed, trying next...")
                continue

    # 2. Hard fallback to Gemini if OpenRouter fails or is not configured
    if gemini_key:
        try:
            log(f"Falling back to native Gemini ({GEMINI_MODEL})")
            return call_gemini(session, gemini_key, prompt, temperature=temperature)
        except Exception as exc:
            errors.append(f"Native Gemini: {exc}")

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
You are a top-tier product analyst and tech media editor.
Turn today's Product Hunt launch into a practical, highly readable, premium WeChat article.

Output JSON only: title, summary, wxhtml.

Requirements:
1) title: Simplified Chinese, 18-28 chars, high-click, factual, professional.
2) summary: Simplified Chinese, 60-120 chars, a sharp 1-2 sentence core value prop.
3) wxhtml:
   - Full length around 1000-1500 chars.
   - Do NOT include any title (H1, etc.) inside wxhtml; it is already defined in the "title" field.
   - Do NOT stack rigid sections. Write a highly fluid, engaging narrative.
   - Use this exact format strictly for section headers (no extra divs/sections wrapping everything):
     <h2 style="font-size: 18px; font-weight: 600; color: #0f172a; margin: 28px 0 12px 0; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px;">模块名称</h2>
   - Paragraphs: `<p style="margin: 0 0 16px; font-size: 16px; color: #334155; line-height: 1.7; text-align: justify;">`
   - Highlights: Use `<strong style="color: #0369a1; font-weight: 600;">关键字</strong>` for core concepts, features, or metrics.
   - Quotes/Emphasis: 
     <section style="margin:20px 0;padding:16px;background-color:#f8fafc;border-left:3px solid #0369a1;">
       <p style="margin:0;font-size:15px;color:#475569;line-height:1.7;">引用内容</p>
     </section>
4) DO NOT output ANY `<img>`, `<video>`, or `<a>` tags inside wxhtml. We will programmatically inject premium images into your text.

Tool name: {post.name}
Tagline: {post.tagline}
Description: {post.description}
Votes: {post.votes}
Comments: {post.comments}
Topics: {json.dumps(post.topics, ensure_ascii=False)}
Product Hunt URL: {post.ph_url}
Official Website: {post.website or ''}
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
