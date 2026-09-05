"""Turn a matched post URL into author / caption / date / image evidence.

Three transports, tried in order, and the one that succeeded is recorded in the
evidence bundle so nothing is claimed that was not actually fetched:

  tier 1  plain HTTP + OpenGraph / JSON-LD / oEmbed
  tier 2  the installed Chrome via Playwright (handles JS shells, login
          overlays, and networks that block script clients but not browsers)
  tier 3  the thumbnail the search engine already returned

Extraction never aborts the pipeline: a blocked platform degrades to tier 3 and
the reason is written into the record.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .. import config
from ..canonical import sha256_bytes, sha256_text

TIMEOUT = 25
HEADERS = {"User-Agent": config.BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}

# Epochs used to recover a post time from its id when the page hides the date.
TWITTER_EPOCH_MS = 1288834974657
INSTAGRAM_EPOCH_MS = 1314220021721


@dataclass
class Post:
    url: str
    platform: str
    author: str = ""
    caption: str = ""
    posted_at: str = ""
    posted_at_source: str = "unknown"   # exact | derived_from_id | approx | unknown
    image_url: str = ""
    image_file: str = ""
    image_sha256: str = ""
    image_phash: str = ""
    image_source: str = "search_thumbnail"  # post_og | embed | oembed | browser | search_thumbnail
    extraction_method: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


# --- small parsing helpers ---------------------------------------------------------

_META = re.compile(
    r"""<meta[^>]+(?:property|name)\s*=\s*["'](og:[^"']+|twitter:[^"']+)["'][^>]*>""",
    re.I,
)
_CONTENT = re.compile(r"""content\s*=\s*["'](.*?)["']""", re.I | re.S)
_JSONLD = re.compile(
    r"""<script[^>]+type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""",
    re.I | re.S,
)
_ITEMPROP_DATE = re.compile(
    r"""itemprop\s*=\s*["'](?:uploadDate|datePublished)["'][^>]*content\s*=\s*["']([^"']+)["']""",
    re.I,
)
_TIME_TAG = re.compile(r"""<time[^>]+datetime\s*=\s*["']([^"']+)["']""", re.I)


def meta_tags(page: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in _META.finditer(page):
        key = tag.group(1).lower()
        m = _CONTENT.search(tag.group(0))
        if m and key not in out:
            out[key] = html.unescape(m.group(1)).strip()
    return out


def json_ld(page: str) -> list[dict]:
    blocks = []
    for m in _JSONLD.finditer(page):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        blocks.extend(data if isinstance(data, list) else [data])
    return blocks


def _iso_from(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return value


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_int(text: str) -> int | None:
    m = re.search(r"(\d{6,25})", text or "")
    return int(m.group(1)) if m else None


# --- per-platform id -> timestamp --------------------------------------------------

def time_from_id(url: str, platform: str) -> tuple[str, str]:
    """Recover a post time from the id in the URL. Returns (iso, source)."""
    try:
        if platform == "x":
            m = re.search(r"/status/(\d+)", url)
            if m:
                return _ms_to_iso((int(m.group(1)) >> 22) + TWITTER_EPOCH_MS), "derived_from_id"
        if platform == "tiktok":
            m = re.search(r"/video/(\d+)", url)
            if m:
                ts = int(m.group(1)) >> 32
                return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ), "derived_from_id"
        if platform == "linkedin":
            m = re.search(r"activity[-:](\d+)", url)
            if m:
                return _ms_to_iso((int(m.group(1)) >> 22)), "derived_from_id"
        if platform in ("instagram", "threads"):
            m = re.search(r"/(?:p|reel|post)/([A-Za-z0-9_-]{5,})", url)
            if m:
                alphabet = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                            "abcdefghijklmnopqrstuvwxyz0123456789-_")
                media_id = 0
                for ch in m.group(1):
                    if ch not in alphabet:
                        return "", "unknown"
                    media_id = media_id * 64 + alphabet.index(ch)
                return _ms_to_iso((media_id >> 23) + INSTAGRAM_EPOCH_MS), "approx"
    except (ValueError, OverflowError):
        pass
    return "", "unknown"


# --- tier 1: plain HTTP ------------------------------------------------------------

def fetch(url: str, timeout: int = TIMEOUT) -> requests.Response | None:
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return None


def oembed(platform: str, url: str) -> dict | None:
    endpoints = {
        "youtube": f"https://www.youtube.com/oembed?url={url}&format=json",
        "reddit": f"https://www.reddit.com/oembed?url={url}",
        "tiktok": f"https://www.tiktok.com/oembed?url={url}",
    }
    ep = endpoints.get(platform)
    if not ep:
        return None
    r = fetch(ep)
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def from_html(page: str, post: Post) -> None:
    """Fill a Post from OpenGraph, JSON-LD and inline date markup."""
    og = meta_tags(page)
    if og.get("og:image") and not post.image_url:
        post.image_url = og["og:image"]
        post.image_source = "post_og"
    title = og.get("og:title", "")
    desc = og.get("og:description", "")
    if desc and not post.caption:
        post.caption = desc
    if title and not post.author:
        # "Name (@handle) on X", "Name on Instagram: ...", "Name | LinkedIn"
        m = re.search(r"\(@([A-Za-z0-9_.]+)\)", title) or re.search(
            r"^(.*?)\s+on\s+(?:Instagram|X|Threads|TikTok)", title
        )
        post.author = (m.group(1) if m else title.split("|")[0]).strip()

    for block in json_ld(page):
        for key in ("datePublished", "uploadDate", "dateCreated"):
            if block.get(key) and post.posted_at_source != "exact":
                post.posted_at = _iso_from(str(block[key]))
                post.posted_at_source = "exact"
        author = block.get("author")
        if isinstance(author, dict) and author.get("name") and not post.author:
            post.author = str(author["name"])

    if post.posted_at_source != "exact":
        m = _ITEMPROP_DATE.search(page) or _TIME_TAG.search(page)
        if m:
            post.posted_at = _iso_from(m.group(1))
            post.posted_at_source = "exact"


def instagram_extras(url: str, post: Post) -> None:
    """Instagram serves handle, date and caption inside og:description."""
    m = re.search(
        r"-\s*([A-Za-z0-9_.]+)\s+on\s+([A-Z][a-z]+ \d{1,2}, \d{4})\s*:\s*[\"“](.*)",
        post.caption or "",
        re.S,
    )
    if m:
        post.author = post.author or m.group(1)
        try:
            dt = datetime.strptime(m.group(2), "%B %d, %Y").replace(tzinfo=timezone.utc)
            post.posted_at, post.posted_at_source = dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "exact"
        except ValueError:
            pass
        post.caption = m.group(3).rstrip("”\" ")
    if not post.image_url:
        r = fetch(url.rstrip("/") + "/embed/captioned/")
        if r is not None and r.status_code == 200:
            img = re.search(r"https://[^\"'\s]*cdninstagram\.com/v/t51\.[^\"'\s]+", r.text)
            if img:
                post.image_url = html.unescape(img.group(0))
                post.image_source = "embed"
                post.notes.append("image via /embed/captioned/")


def x_extras(url: str, post: Post) -> None:
    """X blocks script clients; FxTwitter mirrors the public tweet as JSON."""
    tid = _first_int(urlsplit(url).path)
    if not tid:
        return
    r = fetch(f"https://api.fxtwitter.com/2/status/{tid}")
    if r is None or r.status_code != 200:
        post.notes.append("fxtwitter unreachable (network filter or rate limit)")
        return
    try:
        tweet = (r.json() or {}).get("tweet") or {}
    except ValueError:
        return
    post.caption = tweet.get("text", "") or post.caption
    post.author = (tweet.get("author") or {}).get("screen_name", "") or post.author
    photos = (tweet.get("media") or {}).get("photos") or []
    if photos and not post.image_url:
        post.image_url = photos[0].get("url", "")
        post.image_source = "post_og"
    if tweet.get("created_timestamp"):
        post.posted_at = datetime.fromtimestamp(
            int(tweet["created_timestamp"]), tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        post.posted_at_source = "exact"
    post.extraction_method = "fxtwitter_api"


def youtube_extras(url: str, post: Post) -> None:
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url) or re.search(r"youtu\.be/([A-Za-z0-9_-]{6,})", url)
    if m and not post.image_url:
        post.image_url = f"https://img.youtube.com/vi/{m.group(1)}/maxresdefault.jpg"
        post.image_source = "post_og"


def reddit_extras(url: str, post: Post) -> None:
    """Unauthenticated .json is dead since May 2026; the RSS feed still works."""
    r = fetch(url.rstrip("/") + "/.rss")
    if r is None or r.status_code != 200:
        post.notes.append(f"reddit rss unavailable (status {getattr(r, 'status_code', 'n/a')})")
        return
    body = html.unescape(r.text)
    img = re.search(r"https://(?:i\.redd\.it|preview\.redd\.it)/[^\"'\s<&]+", body)
    if img and not post.image_url:
        post.image_url = img.group(0)
        post.image_source = "post_og"
    pub = re.search(r"<published>([^<]+)</published>", body)
    if pub:
        post.posted_at, post.posted_at_source = _iso_from(pub.group(1)), "exact"
    auth = re.search(r"<name>/u/([^<]+)</name>", body)
    if auth and not post.author:
        post.author = auth.group(1)
    post.extraction_method = post.extraction_method or "reddit_rss"


# --- tier 2: real Chrome -----------------------------------------------------------

def via_chrome(url: str, post: Post, screenshot: Path | None = None) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        post.notes.append("playwright not installed; skipped browser tier")
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(user_agent=config.BROWSER_UA)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            content = page.content()
            if screenshot:
                page.screenshot(path=str(screenshot), full_page=False)
            browser.close()
        before = post.image_url
        from_html(content, post)
        if post.image_url and post.image_url != before:
            post.image_source = "browser"
        post.extraction_method = "playwright_chrome"
        return True
    except Exception as exc:  # noqa: BLE001 - browser tier is best effort
        post.notes.append(f"chrome tier failed: {type(exc).__name__}")
        return False


# --- orchestration -----------------------------------------------------------------

def extract(url: str, platform: str, run_dir: Path, fallback_thumb: Path | None = None,
            fallback_thumb_url: str = "", use_browser: bool = True) -> Post:
    post = Post(url=url, platform=platform)

    r = fetch(url)
    if r is not None and r.status_code == 200 and "<html" in r.text[:4000].lower():
        from_html(r.text, post)
        post.extraction_method = "http_og"
    else:
        post.notes.append(
            f"direct fetch failed (status {getattr(r, 'status_code', 'no response')})"
        )

    data = oembed(platform, url)
    if data:
        post.author = post.author or data.get("author_name", "")
        post.caption = post.caption or data.get("title", "")
        if not post.image_url and data.get("thumbnail_url"):
            post.image_url = data["thumbnail_url"]
            post.image_source = "oembed"
        post.extraction_method = post.extraction_method or "oembed"

    enrichers = {
        "instagram": instagram_extras, "x": x_extras,
        "youtube": youtube_extras, "reddit": reddit_extras,
    }
    if platform in enrichers:
        try:
            enrichers[platform](url, post)
        except Exception as exc:  # noqa: BLE001
            post.notes.append(f"{platform} enricher failed: {type(exc).__name__}")

    if not post.image_url and use_browser:
        via_chrome(url, post, screenshot=run_dir / "post_screenshot.png")

    if not post.posted_at:
        post.posted_at, post.posted_at_source = time_from_id(url, platform)

    # Download whatever image we ended up with; CDN URLs are signed and expire,
    # so the bytes are hashed at fetch time.
    if post.image_url:
        img = fetch(post.image_url, timeout=40)
        if img is not None and img.status_code == 200 and img.content:
            dest = run_dir / "post_image.jpg"
            dest.write_bytes(img.content)
            post.image_file = dest.name
            post.image_sha256 = sha256_bytes(img.content)
        else:
            post.notes.append("post image url did not download; using search thumbnail")
            post.image_url = ""

    if not post.image_file and fallback_thumb and fallback_thumb.exists():
        data_bytes = fallback_thumb.read_bytes()
        dest = run_dir / "post_image.jpg"
        dest.write_bytes(data_bytes)
        post.image_file = dest.name
        post.image_sha256 = sha256_bytes(data_bytes)
        post.image_url = fallback_thumb_url
        post.image_source = "search_thumbnail"
        post.extraction_method = post.extraction_method or "search_thumbnail"

    if post.caption:
        post.caption = re.sub(r"\s+", " ", post.caption).strip()
    return post


def caption_sha256(post: Post) -> str:
    return sha256_text(post.caption or "")
