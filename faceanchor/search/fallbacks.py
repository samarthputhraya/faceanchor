"""Drop-in fallbacks: SearchAPI.io Google Lens and Serper.dev image search."""

from __future__ import annotations

import requests

from .. import config
from .base import Hit, RawSearch, load_cache, redact, save_cache

TIMEOUT = 90


def searchapi_available() -> bool:
    return bool(config.SEARCHAPI_KEY)


def searchapi_lens(image_url: str, kind: str = "visual_matches",
                   cache_key: str = "", use_cache: bool = True) -> RawSearch:
    """Same Google Lens data as SerpApi; email-only signup, 100 free searches."""
    if not searchapi_available():
        raise RuntimeError("SEARCHAPI_KEY not set")
    provider = "searchapi.google_lens"
    params = {"engine": "google_lens", "url": image_url,
              "search_type": kind, "link": "resolved", "hl": "en"}
    if use_cache and cache_key:
        cached = load_cache(provider, kind, cache_key)
        if cached:
            return RawSearch(provider=provider, query_kind=kind, request=redact(params),
                             raw=cached, cached=True,
                             search_id=str((cached.get("search_metadata") or {}).get("id", "")))
    r = requests.get("https://www.searchapi.io/api/v1/search",
                     params={**params, "api_key": config.SEARCHAPI_KEY}, timeout=TIMEOUT)
    raw = r.json()
    meta = raw.get("search_metadata") or {}
    if cache_key and not raw.get("error"):
        save_cache(provider, kind, cache_key, raw)
    return RawSearch(provider=provider, query_kind=kind, request=redact(params), raw=raw,
                     search_id=str(meta.get("id", "")), created_at=str(meta.get("created_at", "")),
                     error=raw.get("error", ""))


def searchapi_hits(rs: RawSearch) -> list[Hit]:
    out, seen = [], set()
    for bucket in ("visual_matches", "exact_matches", "image_results", "images_results"):
        for item in rs.raw.get(bucket) or []:
            link = item.get("link") or ""
            if not link or link in seen:
                continue
            seen.add(link)
            image = item.get("image")
            if isinstance(image, dict):
                image = image.get("link", "")
            out.append(Hit(provider=rs.provider, rank=len(out) + 1,
                           title=item.get("title", ""), link=link,
                           source=item.get("source", ""),
                           thumbnail=item.get("thumbnail", ""), image=image or ""))
    return out


def serper_available() -> bool:
    return bool(config.SERPER_KEY)


def serper_images(query: str, cache_key: str = "", use_cache: bool = True) -> RawSearch:
    """Keyword image search, used for the name -> post hop. 2500 free queries."""
    if not serper_available():
        raise RuntimeError("SERPER_KEY not set")
    provider, kind = "serper.images", "images"
    body = {"q": query, "num": 20}
    if use_cache and cache_key:
        cached = load_cache(provider, kind, cache_key)
        if cached:
            return RawSearch(provider=provider, query_kind=kind, request=body,
                             raw=cached, cached=True)
    r = requests.post("https://google.serper.dev/images", json=body,
                      headers={"X-API-KEY": config.SERPER_KEY,
                               "Content-Type": "application/json"}, timeout=TIMEOUT)
    raw = r.json()
    if cache_key:
        save_cache(provider, kind, cache_key, raw)
    return RawSearch(provider=provider, query_kind=kind, request=body, raw=raw)


def serper_hits(rs: RawSearch) -> list[Hit]:
    out = []
    for i, item in enumerate(rs.raw.get("images") or []):
        link = item.get("link") or ""
        if not link:
            continue
        out.append(Hit(provider=rs.provider, rank=i + 1, title=item.get("title", ""),
                       link=link, source=item.get("source", "") or item.get("domain", ""),
                       thumbnail=item.get("thumbnailUrl", ""), image=item.get("imageUrl", "")))
    return out
