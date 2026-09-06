# Work for the team — two people, one job each

Read your own section only. The two jobs touch **different files**, so you cannot
break each other's work and you will not get merge conflicts.

Each job is about **1 hour**. There is a Part 2 in each if you have more time.

**Deadline: Sunday 7 September, and please push by the afternoon** so there is
time to check everything before submission.

---

## First: set up (10 minutes, both of you)

Open a terminal (PowerShell on Windows, Terminal on Mac) and run these one at a
time:

```bash
git clone https://github.com/samarthputhraya/faceanchor
cd faceanchor
python -m venv .venv
```

Turn the environment on — **Windows**:
```bash
.venv\Scripts\activate
```
**Mac or Linux**:
```bash
source .venv/bin/activate
```

Then:
```bash
pip install -r requirements-ci.txt
pytest -q
```

You should see a row of dots and no errors. If you do, you are ready. You do
**not** need API keys, a crypto wallet, or the face models for either job.

### Tell git who you are (important — do this once)

Your commits must show **your** name, not Samartha's:

```bash
git config user.name "Your Name"
git config user.email "your@email.com"
```

Use the same email as your GitHub account, or the commit will not show up as
yours on GitHub.

---

# TEAMMATE 1 — test the date decoders

### What this is about

When FaceAnchor finds a social post, it needs to know when the post was made.
Most platforms hide that, so the code works it out **from the numbers in the
URL** — a Twitter/X post ID secretly contains its own timestamp, and so do
TikTok, LinkedIn and Instagram IDs.

That code is in `faceanchor/extract/post.py`, it is clever, and **it has no
tests at all**. If someone changes it by accident, nothing would notice. Your
job is to make sure something notices.

### Step 1 — create the file

Create a new file called `tests/test_extract_timestamps.py` and paste this in:

```python
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
```

### Step 2 — run it

```bash
pytest tests/test_extract_timestamps.py -q
```

All six should pass. **If one fails, do not change the expected value to make it
pass** — tell Samartha instead, because that would mean a real bug.

### Step 3 — commit and push

```bash
git add tests/test_extract_timestamps.py
git commit -m "test: cover the post-time decoders

time_from_id recovers a post's timestamp from the numbers inside its URL for
x, tiktok, linkedin and instagram, and had no tests. A change to any of the
bit-shifts would have silently produced wrong dates on every record.

Also covers the two cases that must not guess: a URL with no id, and a platform
we do not decode. Both report unknown."
git pull --rebase
git push
```

### Part 2, if you have time — write `zk/README.md`

The `zk/` folder has a circuit, a build script and some generated files, and
nothing explaining which is which. Someone reading it cannot tell that
`verification_key.json` is safe to publish while `facematch_final.zkey` is not.

Create `zk/README.md`, keep it under one page, and cover:

- what each file is, and which are committed vs ignored, **and why**
- how to rebuild everything from scratch (`build.ps1`) and how long it takes
- how someone checks a published proof **without** the proving key
- one line that the trusted setup is a local development ceremony, linking to
  `docs/zero-knowledge-proof.md`

Everything you need is in `zk/build.ps1` and `docs/zero-knowledge-proof.md`.
Commit it the same way as above.

---

# TEAMMATE 2 — test the HTML readers

### What this is about

When FaceAnchor finds a social post, it reads the page to get the author,
caption and image. Websites bury this in two places: `<meta>` tags and a block
of JSON called JSON-LD.

There is a real bug that was already fixed once by hand: on LinkedIn, the
`og:title` tag contains the **post text**, while the author's actual name is in
the JSON-LD. So the code must prefer JSON-LD for the author. Nothing currently
stops that fix being undone.

### Step 1 — create the file

Create a new file called `tests/test_extract_parsers.py` and paste this in:

```python
"""The HTML readers in extract/post.py.

These parse pages written by other people, who change them without telling us,
and they had no tests. The author case is a regression guard: on LinkedIn the
og:title holds the post text while the real account name is in the JSON-LD, so
preferring og:title puts a sentence in the author field.
"""

from faceanchor.extract.post import Post, from_html, json_ld, meta_tags


def test_meta_tags_are_read_and_html_escapes_are_decoded():
    tags = meta_tags('<meta property="og:title" content="Hi &amp; bye">')
    assert tags["og:title"] == "Hi & bye"


def test_json_ld_blocks_are_parsed():
    blocks = json_ld(
        '<script type="application/ld+json">{"author":{"name":"Ada"}}</script>')
    assert blocks == [{"author": {"name": "Ada"}}]


def test_broken_json_ld_is_skipped_instead_of_crashing():
    """A malformed block on someone else's page must not kill the run."""
    assert json_ld('<script type="application/ld+json">{not json}</script>') == []


def test_the_author_comes_from_json_ld_not_og_title():
    """The LinkedIn case: og:title is the post text, JSON-LD has the account."""
    page = """
    <meta property="og:title" content="Thrilled to announce our new office!">
    <script type="application/ld+json">
      {"@type": "SocialMediaPosting", "author": {"name": "Ada Lovelace"}}
    </script>
    """
    post = Post(url="https://linkedin.com/posts/x", platform="linkedin")
    from_html(page, post)
    assert post.author == "Ada Lovelace"


def test_the_image_url_is_read_from_og_image():
    page = '<meta property="og:image" content="https://cdn.example.com/a.jpg">'
    post = Post(url="https://example.com/p/1", platform="x")
    from_html(page, post)
    assert post.image_url == "https://cdn.example.com/a.jpg"


def test_a_page_with_nothing_useful_leaves_the_post_empty():
    """No author is better than a wrong author."""
    post = Post(url="https://example.com/p/1", platform="x")
    from_html("<html><body>nothing here</body></html>", post)
    assert post.author == ""
    assert post.image_url == ""
```

### Step 2 — run it

```bash
pytest tests/test_extract_parsers.py -q
```

All six should pass. **If one fails, do not change the test to make it pass** —
tell Samartha, because that means a real bug.

### Step 3 — commit and push

```bash
git add tests/test_extract_parsers.py
git commit -m "test: cover the OpenGraph and JSON-LD readers

extract/post.py parses pages written by other people and had no tests. The
author case is the important one: it is a regression guard for the LinkedIn fix,
where og:title carries the post text and only the JSON-LD has the account name,
so preferring og:title puts a whole sentence in the author field.

Also covers a malformed JSON-LD block, which must be skipped rather than kill
the run, and a page with nothing useful, where an empty author is correct."
git pull --rebase
git push
```

### Part 2, if you have time — write `docs/threat-model.md`

The README lists limitations and `docs/what-is-proven.md` explains what the
proof covers. Nothing says plainly **who might misuse this tool and what stops
them**. This is a face-search tool, so a reviewer will ask.

Write `docs/threat-model.md` as a table, one row per person: what they want,
what they can reach, and what stops them.

Cover at least:

- **A dishonest operator** who wants a fake match on the record
- **Someone who gets hold of a published evidence bundle** — what can they
  actually learn from it?
- **Someone who steals `face_secret.json`** — they get the biometric. What
  should a real deployment do that this one does not?
- **The person whose face was searched**, who never agreed to it. What can they
  do? What *should* they be able to do?
- **A platform** whose pages we fetch — rate limits, terms of service

Read `docs/what-is-proven.md` first; most of the answers are there.

**Where the answer is "nothing stops them", say that.** An honest gap is worth
far more than a made-up mitigation, and pretending otherwise is the fastest way
to lose a reviewer's trust.

---

## Rules for both of you

- **Run `pytest -q` before you push.** Everything passes right now, so anything
  broken came from your change.
- **Never change a number in the README or docs** unless you also changed the
  thing it describes. Every figure in this project points at something real.
- **Never commit anything from `evidence/runs/`.** It contains face data.
- If something does not work, say so rather than forcing it. A test that fails
  is information; a test edited until it passes is a lie.
