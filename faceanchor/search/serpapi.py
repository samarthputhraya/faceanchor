"""SerpApi providers: Google Lens (primary), Bing and Yandex reverse image.

Free plan: 250 searches/month, 50/hour.  Identical repeat searches are served
from SerpApi's own cache and are not billed, and we additionally cache raw
responses on disk keyed by the query image hash so development reruns are free.
"""

from __future__ import annotations

from pathlib import Path

import requests

from .. import config
from .base import Hit, RawSearch, load_cache, redact, save_cache

ENDPOINT = "https://serpapi.com/search"
UPLOAD = "https://serpapi.com/image"
ACCOUNT = "https://serpapi.com/account"
TIMEOUT = 45


def available() -> bool:
    return bool(config.SERPAPI_KEY)


def quota() -> dict:
    """Searches left on the plan - printed before and after every live search."""
    if not available():
        return {}
    try:
        r = requests.get(ACCOUNT, params={"api_key": config.SERPAPI_KEY}, timeout=30)
        j = r.json()
        return {
            "plan": j.get("plan_name"),
            "searches_left": j.get("plan_searches_left", j.get("total_searches_left")),
            "this_month": j.get("this_month_usage"),
        }
    except Exception as exc:  # noqa: BLE001 - quota is informational only
        return {"error": str(exc)}


def upload_image(path: str | Path) -> str:
    """Upload a local image, returning a short-lived image_id for Lens.

    Max 500 KB, JPG/PNG/WebP; the id expires after about 10 minutes.
    """
    if not available():
        raise RuntimeError("SERPAPI_KEY not set")
    p = Path(path)
    size = p.stat().st_size
    if size > 500_000:
        raise ValueError(f"{p.name} is {size} bytes; SerpApi upload limit is 500 KB")
    with open(p, "rb") as fh:
        r = requests.post(
            UPLOAD,
            files={"image": (p.name, fh, "image/jpeg")},
            data={"api_key": config.SERPAPI_KEY},
            timeout=TIMEOUT,
        )
    r.raise_for_status()
    j = r.json()
    image_id = j.get("image_id") or (j.get("image") or {}).get("image_id")
    if not image_id:
        raise RuntimeError(f"no image_id in SerpApi upload response: {j}")
    return image_id


def _get(params: dict, provider: str, kind: str, cache_key: str, use_cache: bool) -> RawSearch:
    if use_cache and cache_key:
        cached = load_cache(provider, kind, cache_key)
        if cached:
            meta = cached.get("search_metadata") or {}
            return RawSearch(
                provider=provider, query_kind=kind, request=redact(params),
                raw=cached, cached=True,
                search_id=meta.get("id", ""), created_at=meta.get("created_at", ""),
            )
    r = requests.get(ENDPOINT, params={**params, "api_key": config.SERPAPI_KEY}, timeout=TIMEOUT)
    try:
        raw = r.json()
    except ValueError:
        raise RuntimeError(f"{provider}: non-JSON response {r.status_code}: {r.text[:200]}")
    meta = raw.get("search_metadata") or {}
    err = raw.get("error", "")
    if not err and cache_key:
        save_cache(provider, kind, cache_key, raw)
    return RawSearch(
        provider=provider, query_kind=kind, request=redact(params), raw=raw,
        search_id=meta.get("id", ""), created_at=meta.get("created_at", ""), error=err,
    )


def google_lens(image_url: str = "", image_id: str = "", kind: str = "visual_matches",
                cache_key: str = "", use_cache: bool = True) -> RawSearch:
    if not available():
        raise RuntimeError("SERPAPI_KEY not set")
    params: dict = {"engine": "google_lens", "type": kind, "hl": "en", "country": "us"}
    if image_id:
        params["image_id"] = image_id
    elif image_url:
        params["url"] = image_url
    else:
        raise ValueError("google_lens needs image_url or image_id")
    return _get(params, "serpapi.google_lens", kind, cache_key, use_cache)


def bing_reverse(image_url: str, cache_key: str = "", use_cache: bool = True) -> RawSearch:
    return _get({"engine": "bing_reverse_image", "image_url": image_url, "mkt": "en-US"},
                "serpapi.bing_reverse_image", "pages", cache_key, use_cache)


def yandex_reverse(image_url: str, cache_key: str = "", use_cache: bool = True) -> RawSearch:
    return _get({"engine": "yandex_images", "url": image_url},
                "serpapi.yandex_images", "pages", cache_key, use_cache)


def google_images(query: str, cache_key: str = "", use_cache: bool = True) -> RawSearch:
    return _get({"engine": "google_images", "q": query, "hl": "en", "gl": "us"},
                "serpapi.google_images", "images", cache_key, use_cache)


def _url(value) -> str:
    """Some engines return {"link": ...} where others return a bare string."""
    if isinstance(value, dict):
        return value.get("link") or value.get("url") or ""
    return value or ""


def hits_from(rs: RawSearch) -> list[Hit]:
    """Flatten any SerpApi engine response into ranked Hits."""
    raw, out = rs.raw, []
    buckets = (
        "visual_matches", "exact_matches", "image_results",
        "pages_with_this_image", "images_results", "related_content",
    )
    seen = set()
    for bucket in buckets:
        for item in raw.get(bucket) or []:
            link = item.get("link") or item.get("source_page") or ""
            if not link or link in seen:
                continue
            seen.add(link)
            out.append(
                Hit(
                    provider=rs.provider,
                    rank=len(out) + 1,
                    title=item.get("title") or item.get("snippet") or "",
                    link=link,
                    source=item.get("source") or item.get("domain") or "",
                    thumbnail=_url(item.get("thumbnail") or item.get("thumbnail_url")),
                    image=_url(item.get("original") or item.get("image")
                               or item.get("original_image")),
                )
            )
    return out
