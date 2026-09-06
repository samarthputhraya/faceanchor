"""The post-time decoders in extract/post.py.

Platforms mostly hide the timestamp, so it is recovered from the numbers inside
the post ID. These are pure functions -- no network, no keys -- and until now
they had no tests, so a change to the bit-shifts would have gone unnoticed.
"""

from faceanchor.extract.post import time_from_id


def test_x_post_id_decodes_to_its_real_timestamp():
    iso, source = time_from_id(
        "https://x.com/VanEmmerickKris/status/2094782729346859238", "x")
    assert iso == "2026-09-01T13:41:36Z"
    assert source == "derived_from_id"


def test_tiktok_video_id_decodes_to_its_real_timestamp():
    iso, source = time_from_id(
        "https://tiktok.com/@who/video/7300000000000000000", "tiktok")
    assert iso == "2023-11-11T00:48:18Z"
    assert source == "derived_from_id"


def test_linkedin_activity_id_decodes_to_its_real_timestamp():
    iso, source = time_from_id(
        "https://linkedin.com/feed/update/urn:li:activity:7100000000000000000",
        "linkedin")
    assert iso == "2023-08-23T06:25:11Z"
    assert source == "derived_from_id"


def test_instagram_shortcode_is_decoded_but_flagged_approximate():
    """Instagram's shortcode gives a rough time, so it must not claim exactness."""
    iso, source = time_from_id("https://instagram.com/p/DayHWYpCjzS", "instagram")
    assert iso == "2026-07-14T18:17:11Z"
    assert source == "approx"


def test_a_url_with_no_id_reports_unknown_rather_than_guessing():
    assert time_from_id("https://x.com/someone", "x") == ("", "unknown")


def test_an_unsupported_platform_reports_unknown():
    assert time_from_id("https://example.com/thing/123", "myspace") == ("", "unknown")