"""Search provider interface and shared types.

Every provider returns a RawSearch whose ``raw`` field is the provider's JSON
*untouched*.  That file is what a judge inspects to confirm the search really
happened, so it is never filtered or rewritten.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from .. import config
from ..canonical import iso, sha256_bytes


@dataclass
class RawSearch:
    provider: str                  # serpapi.google_lens, searchapi.google_lens, ...
    query_kind: str                # visual_matches | exact_matches | images
    request: dict[str, Any]        # request params with the api key removed
    raw: dict[str, Any]            # provider response, verbatim
    search_id: str = ""
    created_at: str = ""
    fetched_at: str = field(default_factory=iso)
    cached: bool = False
    error: str = ""

    @property
    def raw_sha256(self) -> str:
        return sha256_bytes(json.dumps(self.raw, sort_keys=True, ensure_ascii=False).encode())

    def save(self, directory: Path, index: int = 0) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{index:02d}_{self.provider.replace('.', '_')}_{self.query_kind}"
        path = directory / f"{stem}.raw.json"
        path.write_text(
            json.dumps(
                {
                    "provider": self.provider,
                    "query_kind": self.query_kind,
                    "search_id": self.search_id,
                    "created_at": self.created_at,
                    "fetched_at": self.fetched_at,
                    "cached": self.cached,
                    "request": self.request,
                    "response": self.raw,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path


@dataclass
class Hit:
    """One result row, normalised across providers."""
    provider: str
    rank: int
    title: str
    link: str
    source: str = ""
    thumbnail: str = ""
    image: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class SearchProvider(Protocol):
    name: str

    def available(self) -> bool: ...
    def lens(self, image_url: str = "", image_id: str = "", kind: str = "visual_matches") -> RawSearch: ...


_TRACKING = re.compile(r"^(utm_|fbclid|igshid|gclid|si$|feature$)", re.I)


def canonical_url(url: str) -> str:
    """Normalise a post URL so the same post from different engines dedupes.

    Lower-cases the host, drops www/m/mobile, maps twitter.com -> x.com, strips
    tracking query params and the fragment, and removes a trailing slash.
    """
    if not url:
        return ""
    try:
        s = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (s.hostname or "").lower()
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    if host in ("twitter.com", "vxtwitter.com", "fxtwitter.com"):
        host = "x.com"
    if host == "youtu.be":
        vid = s.path.lstrip("/")
        return f"https://youtube.com/watch?v={vid}" if vid else "https://youtube.com"
    query = "&".join(
        p for p in s.query.split("&")
        if p and not _TRACKING.match(p.split("=", 1)[0])
    )
    path = s.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def platform_of(url: str) -> str:
    """Return a social platform name, or '' when the URL is not a social post."""
    host = (urlsplit(canonical_url(url)).hostname or "").lower()
    table = {
        "instagram.com": "instagram",
        "x.com": "x",
        "facebook.com": "facebook",
        "fb.com": "facebook",
        "reddit.com": "reddit",
        "redd.it": "reddit",
        "tiktok.com": "tiktok",
        "youtube.com": "youtube",
        "linkedin.com": "linkedin",
        "threads.net": "threads",
        "threads.com": "threads",
        "pinterest.com": "pinterest",
    }
    for domain, name in table.items():
        if host == domain or host.endswith("." + domain):
            return name
    return ""


def is_social(url: str) -> bool:
    return bool(platform_of(url))


def cache_path(provider: str, kind: str, key: str) -> Path:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"{provider.replace('.', '_')}__{kind}__{key[:32]}.json"


def load_cache(provider: str, kind: str, key: str) -> dict | None:
    p = cache_path(provider, kind, key)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def save_cache(provider: str, kind: str, key: str, payload: dict) -> None:
    cache_path(provider, kind, key).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def redact(params: dict) -> dict:
    return {k: ("<redacted>" if k in ("api_key", "apikey", "key") else v) for k, v in params.items()}
