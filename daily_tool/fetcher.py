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
    """Parse images from node."""
    urls: list[str] = []
    for key in ("thumbnailUrl", "screenshotUrl"):
        u = normalize_url(node.get(key))
        if u:
            urls.append(u)

    thumbnail = node.get("thumbnail")
    if isinstance(thumbnail, dict):
        for key in ("url", "imageUrl"):
            u = normalize_url(thumbnail.get(key))
            if u:
                urls.append(u)

    def append_media_item(media_item: dict[str, Any]) -> None:
        if not isinstance(media_item, dict):
            return
        m_type = str(media_item.get("type", "")).lower()
        if m_type and "image" not in m_type and "screenshot" not in m_type:
            return
        for k in ("url", "imageUrl"):
            u = normalize_url(media_item.get(k))
            if u:
                urls.append(u)

    media = node.get("media")
    if isinstance(media, list):
        for m in media:
            append_media_item(m)
    elif isinstance(media, dict):
        for edge in media.get("edges", []) or []:
            append_media_item((edge or {}).get("node", {}) or {})
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
    """Fetch posts from Product Hunt with fallback queries."""
    queries = [
        """
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
                postedAt
                thumbnailUrl
                thumbnail { url }
                topic { name }
                topics(first: 5) { edges { node { name } } }
                media(first: 8) {
                  edges {
                    node {
                      type
                      url
                      imageUrl
                      videoUrl
                    }
                  }
                }
              }
            }
          }
        }
        """,
        """
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
                postedAt
              }
            }
          }
        }
        """,
        """
        query FetchPosts($first: Int!) {
          posts(first: $first, order: NEWEST) {
            edges {
              node {
                id
                name
                tagline
                url
                website
              }
            }
          }
        }
        """,
    ]

    errors: list[str] = []
    for q in queries:
        try:
            data = ph_graphql(session, token=token, query=q, variables={"first": first})
            gql_errors = data.get("errors") or []
            if gql_errors:
                errors.append("; ".join(str(e.get("message", e)) for e in gql_errors))
                continue
            posts = parse_posts_from_response(data)
            if posts:
                return posts
        except Exception as exc:
            errors.append(str(exc))
            continue
    raise RuntimeError("Failed to fetch Product Hunt posts. " + " | ".join(errors[:3]))


def scrape_meta_images(session: requests.Session, url: str) -> list[str]:
    """Scrape images from meta tags."""
    try:
        html = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30).text
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for meta in soup.select("meta[property='og:image'],meta[name='twitter:image']"):
        u = normalize_url(meta.get("content"))
        if u:
            urls.append(u)
    for img in soup.select("img"):
        src = img.get("src") or img.get("data-src")
        u = normalize_url(src)
        if not u:
            continue
        lu = u.lower()
        if any(k in lu for k in ("logo", "avatar", "icon", "sprite")):
            continue
        urls.append(u)
        if len(urls) >= 6:
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
