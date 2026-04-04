"""Product Hunt API fetcher."""

from __future__ import annotations

from typing import Any

import requests
from bs4 import BeautifulSoup

from .config import PH_ENDPOINT, USER_AGENT
from .models import ToolPost, to_int
from .utils import log, normalize_url


def request_json(
    session: requests.Session, method: str, url: str, **kwargs: Any
) -> dict[str, Any]:
    """Make JSON request."""
    headers = kwargs.pop("headers", {})
    merged = {"User-Agent": USER_AGENT, **headers}
    resp = session.request(method, url, headers=merged, timeout=40, **kwargs)
    resp.raise_for_status()
    return resp.json()


def ph_graphql(
    session: requests.Session, token: str, query: str, variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Execute Product Hunt GraphQL query."""
    payload = {"query": query, "variables": variables or {}}
    return request_json(
        session,
        "POST",
        PH_ENDPOINT,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )


def parse_topics(node: dict[str, Any]) -> list[str]:
    """Parse topics from node."""
    topics: list[str] = []
    topic_obj = node.get("topic")
    if isinstance(topic_obj, dict):
        name = (topic_obj.get("name") or "").strip()
        if name:
            topics.append(name)
    topics_obj = node.get("topics")
    if isinstance(topics_obj, dict):
        for edge in topics_obj.get("edges", []) or []:
            n = (edge.get("node", {}) or {}).get("name")
            if isinstance(n, str) and n.strip():
                topics.append(n.strip())
    return list(dict.fromkeys(topics))


def parse_node_images(node: dict[str, Any]) -> list[str]:
    """Parse images from node including thumbnail and media."""
    urls: list[str] = []
    
    # 1. Standard Thumbnail
    thumbnail = node.get("thumbnail")
    if isinstance(thumbnail, dict):
        u = normalize_url(thumbnail.get("url"))
        if u:
            urls.append(u)

    # 2. Media items (screenshots/images)
    media = node.get("media")
    if isinstance(media, list):
        for m in media:
            if not isinstance(m, dict):
                continue
            m_type = str(m.get("type", "")).lower()
            if "image" in m_type or "screenshot" in m_type:
                u = normalize_url(m.get("url") or m.get("imageUrl"))
                if u:
                    urls.append(u)
    
    # 3. Fallback for different API versions
    for key in ("thumbnailUrl", "screenshotUrl"):
        u = normalize_url(node.get(key))
        if u:
            urls.append(u)

    return list(dict.fromkeys(urls))


def parse_posts_from_response(data: dict[str, Any]) -> list[ToolPost]:
    """Parse posts from GraphQL response."""
    posts_obj = (data.get("data", {}) or {}).get("posts")
    if not isinstance(posts_obj, dict):
        return []
    posts: list[ToolPost] = []
    for edge in posts_obj.get("edges", []) or []:
        node = (edge or {}).get("node", {}) or {}
        pid = str(node.get("id") or "").strip()
        name = str(node.get("name") or "").strip()
        ph_url = normalize_url(node.get("url"))
        if not pid or not name or not ph_url:
            continue

        website = normalize_url(node.get("website"))
        post = ToolPost(
            id=pid,
            name=name,
            tagline=str(node.get("tagline") or "").strip(),
            description=str(node.get("description") or "").strip(),
            ph_url=ph_url,
            website=website,
            votes=to_int(node.get("votesCount")),
            comments=to_int(node.get("commentsCount")),
            posted_at=str(node.get("postedAt") or node.get("createdAt") or "").strip(),
            topics=parse_topics(node),
            image_urls=parse_node_images(node),
        )
        posts.append(post)
    return posts


def fetch_posts(session: requests.Session, token: str, first: int = 30) -> list[ToolPost]:
    """Fetch posts from Product Hunt with standardized GraphQL query."""
    query = """
    query FetchPosts($first: Int!) {
      posts(first: $first, order: NEWEST) {
        edges {
          node {
            id
            name
            tagline
            description
            url
            website
            votesCount
            commentsCount
            createdAt
            postedAt
            thumbnail { url }
            media {
              type
              url
            }
            topics(first: 5) { edges { node { name } } }
          }
        }
      }
    }
    """
    
    try:
        data = ph_graphql(session, token=token, query=query, variables={"first": first})
        gql_errors = data.get("errors") or []
        if gql_errors:
            raise RuntimeError("; ".join(str(e.get("message", e)) for e in gql_errors))
        
        posts = parse_posts_from_response(data)
        if posts:
            log(f"Successfully fetched {len(posts)} posts from PH.")
            # Log first post images as sample
            sample = posts[0]
            log(f"Sample data - Name: {sample.name}, Images found: {len(sample.image_urls)}")
            for img in sample.image_urls[:3]:
                log(f"  - Image URL: {img}")
            return posts
    except Exception as exc:
        log(f"Error fetching posts: {exc}")
        raise

    raise RuntimeError("Failed to fetch Product Hunt posts.")


def scrape_meta_images(session: requests.Session, url: str) -> list[str]:
    """Scrape images from meta tags and main content."""
    try:
        resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if resp.status_code != 200:
            return []
        html = resp.text
    except Exception:
        return []
    
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    
    # 1. Try OpenGraph and Twitter images first (highest quality usually)
    for meta in soup.select("meta[property='og:image'], meta[property='og:image:secure_url'], meta[name='twitter:image']"):
        u = normalize_url(meta.get("content"))
        if u:
            urls.append(u)
    
    # 2. Look for product screenshots specifically if on Product Hunt
    if "producthunt.com" in url:
        for img in soup.select("img[src*='ph-files.imgix.net'], img[src*='producthunt.com/posts/']"):
            u = normalize_url(img.get("src") or img.get("data-src"))
            if u:
                urls.append(u)

    # 3. Look for main project images/screenshots in the body
    for img in soup.select("main img, article img, #content img, .content img"):
        src = img.get("src") or img.get("data-src")
        u = normalize_url(src)
        if not u:
            continue
        
        # Filter out common icons, avatars, and logos
        lu = u.lower()
        if any(k in lu for k in ("logo", "avatar", "icon", "sprite", "badge", "button", "loader")):
            continue
        
        # Try to avoid relative paths if they are too short or look like tiny assets
        if len(u) < 20 and "/" not in u[10:]:
            continue
            
        urls.append(u)
        if len(urls) >= 15:
            break
            
    return list(dict.fromkeys(urls))


def enrich_post(session: requests.Session, post: ToolPost) -> ToolPost:
    """Enrich post with additional images."""
    image_urls = list(post.image_urls)
    if not image_urls:
        image_urls.extend(scrape_meta_images(session, post.ph_url))
    if post.website:
        image_urls.extend(scrape_meta_images(session, post.website))
    image_urls = list(dict.fromkeys(image_urls))
    return ToolPost(
        id=post.id,
        name=post.name,
        tagline=post.tagline,
        description=post.description,
        ph_url=post.ph_url,
        website=post.website,
        votes=post.votes,
        comments=post.comments,
        posted_at=post.posted_at,
        topics=post.topics,
        image_urls=image_urls,
    )
