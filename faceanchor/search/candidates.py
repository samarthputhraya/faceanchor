"""Turn raw search hits into face-verified, ranked candidates.

Every social hit is scored, including the ones that lose.  The rejected rows
are the evidence that a real comparison happened: they cannot be cherry-picked.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import config
from ..canonical import sha256_bytes
from .base import Hit, canonical_url, platform_of

MATCH, WEAK, REJECT = "MATCH", "WEAK", "REJECT"
NO_FACE, FETCH_FAIL, SKIPPED = "NO_FACE", "FETCH_FAIL", "SKIPPED"


@dataclass
class Candidate:
    rank: int
    platform: str
    url: str                     # canonical
    raw_url: str                 # as returned by the provider
    title: str = ""
    source: str = ""
    providers: list[str] = field(default_factory=list)
    thumbnail_url: str = ""
    thumbnail_sha256: str = ""
    thumbnail_file: str = ""
    faces_found: int = 0
    similarity: float = -1.0
    verdict: str = FETCH_FAIL
    note: str = ""
    progress: str = ""

    @property
    def engines_agreeing(self) -> int:
        return len(set(self.providers))

    def as_dict(self) -> dict:
        d = asdict(self)
        d["engines_agreeing"] = self.engines_agreeing
        d["similarity"] = round(self.similarity, 4)
        return d

    def record_row(self) -> dict:
        """Compact row embedded in the hashed record."""
        return {
            "rank": self.rank,
            "platform": self.platform,
            "url": self.url,
            "similarity": round(self.similarity, 4),
            "verdict": self.verdict,
            "engines_agreeing": self.engines_agreeing,
            "thumbnail_sha256": self.thumbnail_sha256,
        }


def merge_hits(hit_lists: list[list[Hit]]) -> list[Candidate]:
    """Deduplicate hits by canonical URL, keeping only social posts."""
    by_url: dict[str, Candidate] = {}
    for hits in hit_lists:
        for h in hits:
            plat = platform_of(h.link)
            if not plat:
                continue
            url = canonical_url(h.link)
            c = by_url.get(url)
            if c is None:
                c = Candidate(rank=0, platform=plat, url=url, raw_url=h.link,
                              title=h.title, source=h.source,
                              thumbnail_url=h.thumbnail or h.image)
                by_url[url] = c
            if h.provider not in c.providers:
                c.providers.append(h.provider)
            if not c.thumbnail_url:
                c.thumbnail_url = h.thumbnail or h.image
            if not c.title and h.title:
                c.title = h.title
    return list(by_url.values())


def verdict_for(similarity: float, engine) -> str:
    if similarity >= engine.match_threshold:
        return MATCH
    if similarity >= engine.weak_threshold:
        return WEAK
    return REJECT


DEFAULT_MAX_SCORED = 40
# Thumbnails come from many different CDNs, so the limit is those hosts,
# not us. Eight keeps every fetch well inside its own timeout.
FETCH_WORKERS = 8


def score_candidates(candidates: list[Candidate], query_embedding: np.ndarray, engine,
                     run_dir: Path, emit=None, timeout: int = 25,
                     limit: int = DEFAULT_MAX_SCORED) -> list[Candidate]:
    """Download each candidate thumbnail, embed every face, keep the best cosine.

    A popular subject can return well over a hundred social results, and each
    one costs a download plus a detection pass. Scoring is capped so a run stays
    watchable; anything skipped keeps the SKIPPED verdict and stays in the
    output, because a silently truncated list would read as full coverage.
    """
    # Imported here rather than at module scope: merging, ranking and name
    # identification are pure logic, so they stay importable (and testable)
    # without OpenCV or a face model installed.
    from ..face.engine import cosine, decode_image

    thumbs = run_dir / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": config.BROWSER_UA}

    skipped = candidates[limit:] if limit else []
    for c in skipped:
        c.verdict, c.note = SKIPPED, f"beyond the --max-candidates limit of {limit}"
    scored = candidates[:limit] if limit else candidates
    total = len(scored)

    # Thumbnails are fetched concurrently and scored in order afterwards.
    # Downloading forty images one at a time was roughly a third of the stage
    # for no reason: the fetches are independent and network-bound. Detection
    # stays sequential and single-threaded, so results and their order are
    # identical to the serial version -- only the waiting overlaps.
    def _fetch(url: str) -> tuple[bytes | None, str]:
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.content, ""
        except Exception as exc:  # noqa: BLE001 - a dead thumbnail must not stop the run
            return None, f"{type(exc).__name__}: {exc}"[:160]

    fetched: dict[int, tuple[bytes | None, str]] = {}
    wanted = [(i, c) for i, c in enumerate(scored) if c.thumbnail_url]
    if wanted:
        with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(wanted))) as pool:
            futures = {pool.submit(_fetch, c.thumbnail_url): i for i, c in wanted}
            for fut in as_completed(futures):
                fetched[futures[fut]] = fut.result()

    for i, c in enumerate(scored, 1):
        c.progress = f"{i}/{total}"
        if not c.thumbnail_url:
            c.verdict, c.note = FETCH_FAIL, "no thumbnail url in search response"
        else:
            data, err = fetched.get(i - 1, (None, "not fetched"))
            if data is None:
                c.verdict, c.note = FETCH_FAIL, err
            else:
                c.thumbnail_sha256 = sha256_bytes(data)
                img = decode_image(data)
                if img is None:
                    c.verdict, c.note = FETCH_FAIL, "thumbnail did not decode as an image"
                else:
                    fname = f"thumb_{i:02d}_{c.platform}.jpg"
                    (thumbs / fname).write_bytes(data)
                    c.thumbnail_file = f"thumbs/{fname}"
                    faces = engine.detect_and_embed(img)
                    c.faces_found = len(faces)
                    if not faces:
                        c.verdict, c.similarity, c.note = NO_FACE, -1.0, "no face detected in thumbnail"
                    else:
                        sims = [cosine(query_embedding, f.embedding) for f in faces
                                if f.embedding is not None]
                        c.similarity = max(sims) if sims else -1.0
                        c.verdict = verdict_for(c.similarity, engine)
        if emit:
            emit(c)

    return rerank(candidates)


ORDER = {MATCH: 0, WEAK: 1, REJECT: 2, NO_FACE: 3, FETCH_FAIL: 4, SKIPPED: 5}


def rerank(candidates: list[Candidate]) -> list[Candidate]:
    """Best verdict first, then similarity, then how many engines agreed."""
    candidates.sort(
        key=lambda c: (ORDER.get(c.verdict, 9), -c.similarity, -c.engines_agreeing)
    )
    for i, c in enumerate(candidates, 1):
        c.rank = i
    return candidates


def best_match(candidates: list[Candidate]) -> Candidate | None:
    for c in candidates:
        if c.verdict == MATCH:
            return c
    return None


# --- hop 2: guess the person's name from the titles the engines returned -----------

_STOP = {
    "Instagram", "Facebook", "Twitter", "LinkedIn", "YouTube", "TikTok", "Reddit",
    "Pinterest", "Threads", "Photos", "Photo", "Images", "Image", "Stock", "Getty",
    "News", "The", "And", "For", "With", "New", "Video", "Videos", "Wikipedia",
    "Wikimedia", "Commons", "File", "Alamy", "Shutterstock", "Reuters", "AP",
}
_NAME = re.compile(r"\b([A-Z][a-z]{1,15})\s+([A-Z][a-z]{1,15})\b")


def guess_name(titles: list[str], min_count: int = 2) -> str:
    """Majority vote over capitalised bigrams appearing across result titles.

    Cheap, explainable and good enough for public figures: the person's name is
    typically the most repeated two-word capitalised phrase across many sites.
    """
    counts: Counter[str] = Counter()
    for t in titles:
        for a, b in _NAME.findall(t or ""):
            if a in _STOP or b in _STOP:
                continue
            counts[f"{a} {b}"] += 1
    if not counts:
        return ""
    name, n = counts.most_common(1)[0]
    return name if n >= min_count else ""


HOP2_SITES = ("instagram.com", "x.com", "linkedin.com/posts", "youtube.com", "reddit.com")


def hop2_queries(name: str) -> list[str]:
    return [f'"{name}" site:{site}' for site in HOP2_SITES]
