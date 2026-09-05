"""Search-side logic that must hold regardless of what the providers return."""

from __future__ import annotations

import pytest

from faceanchor.search.base import Hit, canonical_url, is_social, platform_of
from faceanchor.search.candidates import (
    Candidate, MATCH, REJECT, WEAK, guess_name, hop2_queries, merge_hits, rerank,
)


class FakeEngine:
    match_threshold = 0.40
    weak_threshold = 0.30


@pytest.mark.parametrize("url,expected", [
    ("https://www.instagram.com/p/ABC/?utm_source=ig_web", "https://instagram.com/p/ABC"),
    ("https://twitter.com/a/status/1", "https://x.com/a/status/1"),
    ("https://x.com/a/status/1", "https://x.com/a/status/1"),
    ("https://m.facebook.com/photo?fbid=1", "https://facebook.com/photo?fbid=1"),
    ("https://youtu.be/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
])
def test_urls_are_canonicalised(url, expected):
    assert canonical_url(url) == expected


def test_the_same_post_from_two_engines_becomes_one_candidate():
    hits_a = [Hit("serpapi.google_lens", 1, "t", "https://twitter.com/a/status/1", thumbnail="x")]
    hits_b = [Hit("serpapi.bing_reverse_image", 1, "t", "https://x.com/a/status/1?s=20")]
    merged = merge_hits([hits_a, hits_b])
    assert len(merged) == 1
    assert merged[0].engines_agreeing == 2


def test_non_social_results_are_not_candidates():
    hits = [
        Hit("p", 1, "wiki", "https://en.wikipedia.org/wiki/Sundar_Pichai"),
        Hit("p", 2, "stock", "https://www.gettyimages.com/photo/123"),
        Hit("p", 3, "insta", "https://www.instagram.com/p/ABC/"),
    ]
    merged = merge_hits([hits])
    assert [c.platform for c in merged] == ["instagram"]


@pytest.mark.parametrize("url,platform", [
    ("https://www.linkedin.com/posts/x-activity-123-ab", "linkedin"),
    ("https://www.reddit.com/r/pics/comments/abc/title/", "reddit"),
    ("https://www.tiktok.com/@u/video/7392090301310143786", "tiktok"),
    ("https://example.com/page", ""),
])
def test_platform_detection(url, platform):
    assert platform_of(url) == platform
    assert is_social(url) is bool(platform)


def test_candidates_are_ranked_match_first_then_by_similarity():
    cands = [
        Candidate(0, "x", "u1", "u1", similarity=0.20, verdict=REJECT),
        Candidate(0, "instagram", "u2", "u2", similarity=0.55, verdict=MATCH),
        Candidate(0, "reddit", "u3", "u3", similarity=0.35, verdict=WEAK),
        Candidate(0, "instagram", "u4", "u4", similarity=0.61, verdict=MATCH),
    ]
    ranked = rerank(cands)
    assert [c.url for c in ranked] == ["u4", "u2", "u3", "u1"]
    assert [c.rank for c in ranked] == [1, 2, 3, 4]


def test_rejected_candidates_are_kept_not_discarded():
    """Losing rows are the evidence that a real comparison happened."""
    cands = rerank([
        Candidate(0, "x", "u1", "u1", similarity=0.05, verdict=REJECT),
        Candidate(0, "instagram", "u2", "u2", similarity=0.7, verdict=MATCH),
    ])
    assert len(cands) == 2
    assert any(c.verdict == REJECT for c in cands)


def test_the_person_is_identified_from_repeated_title_bigrams():
    titles = [
        "Sundar Pichai - Wikipedia",
        "Sundar Pichai on Instagram: a photo",
        "Google chief Sundar Pichai speaks at I/O",
        "Getty Images stock photo",
    ]
    assert guess_name(titles) == "Sundar Pichai"


def test_platform_words_are_not_mistaken_for_a_name():
    assert guess_name(["Instagram Photos", "Getty Images", "Stock Photo"]) == ""


def test_a_name_seen_once_is_not_trusted():
    assert guess_name(["Sundar Pichai - Wikipedia"]) == ""


def test_hop2_targets_the_platforms_we_can_actually_read():
    qs = hop2_queries("Sundar Pichai")
    assert all(q.startswith('"Sundar Pichai" site:') for q in qs)
    assert any("instagram.com" in q for q in qs)
    assert any("linkedin.com/posts" in q for q in qs)
