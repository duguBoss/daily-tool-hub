"""Main entry point."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import requests

from .ai import classify_productivity, generate_article
from .config import (
    ASSETS_DIR,
    MAX_PRODUCTIVITY_CHECKS,
    OUTPUT_DIR,
    PH_FETCH_FIRST,
    POST_JSON,
)
from .fetcher import enrich_post, fetch_posts
from .images import create_fallback_cover, download_images
from .renderer import choose_theme, ensure_wxhtml
from .utils import (
    load_seen_state,
    log,
    make_click_title,
    clamp_summary,
    save_seen_state,
    tool_fingerprint,
)


def clean_generated_outputs() -> None:
    """Clean previous generated outputs."""
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    if ASSETS_DIR.exists():
        shutil.rmtree(ASSETS_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    """Main function."""
    # Get API keys
    ph_token = os.getenv("PRODUCT_HUNT_TOKEN", "").strip()
    if not ph_token:
        raise RuntimeError("Missing PRODUCT_HUNT_TOKEN environment variable.")

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip() or None
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip() or None

    if not openrouter_key and not gemini_key:
        raise RuntimeError("Missing API key: need OPENROUTER_API_KEY or GEMINI_API_KEY")

    clean_generated_outputs()
    seen_ids, seen_fingerprints = load_seen_state()
    log(f"loaded seen state: ids={len(seen_ids)} fingerprints={len(seen_fingerprints)}")

    session = requests.Session()
    posts = fetch_posts(session, token=ph_token, first=max(20, PH_FETCH_FIRST))
    log(f"fetched posts: {len(posts)}")

    unseen_posts = [
        p for p in posts if p.id not in seen_ids and tool_fingerprint(p) not in seen_fingerprints
    ]
    if not unseen_posts:
        unseen_posts = [p for p in posts if p.id not in seen_ids]
    if not unseen_posts:
        raise RuntimeError("No unseen Product Hunt post in fetched results.")

    # Find productivity-related post
    selected = None
    checked = 0
    max_checks = (
        len(unseen_posts)
        if MAX_PRODUCTIVITY_CHECKS <= 0
        else min(MAX_PRODUCTIVITY_CHECKS, len(unseen_posts))
    )

    for candidate in unseen_posts:
        if checked >= max_checks:
            break
        enriched = enrich_post(session, candidate)
        checked += 1
        related, reason = classify_productivity(session, enriched, openrouter_key, gemini_key)
        log(
            f"productivity-check {checked}/{max_checks}: "
            f"{enriched.name} ({enriched.id}) -> {related} {reason}"
        )
        if not related:
            continue
        selected = enriched
        break

    if selected is None:
        raise RuntimeError("No productivity-related unseen tool found in fetched results.")

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

    # Generate article
    result = generate_article(session, selected, github_images, openrouter_key, gemini_key)
    title = make_click_title(
        str(result.get("title", "")).strip() or f"{selected.name}：今天值得试的效率工具",
        selected,
    )
    summary = clamp_summary(str(result.get("summary", "")).strip())
    wxhtml_raw = str(result.get("wxhtml", "")).strip()

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


def cli() -> None:
    """CLI entry point."""
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"error: {exc}")
        raise
