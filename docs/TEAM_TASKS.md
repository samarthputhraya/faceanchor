# Open work

Three genuine gaps, scoped so they can be picked up independently and none of
them blocks the demo. Each is real work with a real gap behind it — not filler.

Please commit under your own name and email. One focused commit each is worth
more than a large one, and the message should say *why*, not just *what*.

---

## 1. Tests for the extraction parsers

**The gap:** `faceanchor/extract/post.py` is 435 lines — the OpenGraph parser,
the JSON-LD parser, the oEmbed clients and four per-platform enrichers — and it
has **zero tests**. It is the least covered and most fragile part of the
pipeline, because it parses HTML written by other people who change it without
telling us.

**Why it matters:** the fix in commit `c40c310` ("trust structured data over
og:title for the author") was found by hand. A LinkedIn `og:title` holds the
post text while its JSON-LD `SocialMediaPosting` holds the account name, so the
author came out as a sentence. Nothing would catch that regression today.

**What to do:** save real HTML responses as fixtures under
`tests/fixtures/extract/` and test the parsers against them offline. No network
in the tests.

Worth covering:
- `meta_tags()` and `json_ld()` on a real post page
- `from_html()` preferring JSON-LD over `og:title` for the author (the
  regression above)
- `time_from_id()` for each platform — X snowflake, TikTok, LinkedIn activity
  id, Instagram shortcode. These are pure functions and easy to pin down.
- `canonical_url()` cases not already in `test_search_logic.py`

**Start here:** `faceanchor/extract/post.py:193` (`from_html`) and `:117`
(`time_from_id`). Follow the style in `tests/test_search_logic.py`.

---

## 2. A threat model in `docs/`

**The gap:** the README lists limitations, and `docs/zero-knowledge-proof.md`
explains what the proof does and does not cover. Nothing states plainly *who
might attack this system and what stops them*.

**Why it matters:** this is a surveillance-shaped tool. A reviewer will ask what
happens when it is pointed at someone who did not consent, and "we thought about
it" is a much weaker answer than a written model with the mitigations named.

**What to do:** write `docs/threat-model.md`. Give each actor a row: what they
want, what they can reach, what stops them, and what does not.

At least these:
- **A dishonest operator** — wants a false match on the record. Mitigated by
  the proof for the *number*; explicitly **not** mitigated for fabricated
  embeddings (see the limitations).
- **Someone who obtains the evidence bundle** — what can they learn? The
  commitments are salted; the salts are gitignored. What is actually exposed?
- **Someone who obtains `face_secret.json`** — they get the biometric. What
  should a real deployment do that we do not?
- **The subject of a search** — they never consented. What recourse exists?
  What *should* exist? An honest "nothing yet, and here is what we would build"
  is a good answer.
- **A platform** — we fetch their pages. Rate limits, robots, terms.

Be honest where the answer is "not mitigated". That is the point of the
document.

---

## 3. `zk/README.md`

**The gap:** `zk/build.ps1` works, but there is nothing at the directory level
telling a reader what each artifact is or which are safe to publish. Someone
looking at `zk/` sees a `.circom` file, a `bin/`, a `build/` and a `js/`.

**Why it matters:** the difference between `verification_key.json` (safe to
publish, needed to check a proof) and `facematch_final.zkey` (9 MB, needed to
*make* proofs, and gitignored) is exactly the kind of thing that gets confused,
and confusing them is how a project accidentally publishes something it should
not — or fails to publish something a verifier needs.

**What to do:** a short `zk/README.md` covering:
- what each file is, and which are committed vs gitignored, and *why*
- how to rebuild from nothing (`build.ps1`) and roughly how long it takes
- how a third party verifies a published proof **without** the proving key
- the ceremony caveat, in one line, pointing at
  `docs/zero-knowledge-proof.md` for the detail

Keep it under a page. `zk/build.ps1` and `docs/zero-knowledge-proof.md` have
everything you need.

---

## Ground rules

- Run `pytest -q` before committing. It is green now — 55 tests — and CI runs
  on Ubuntu and Windows.
- Do not change any published number without changing the artifact behind it.
  Every figure in the README links to something real, and that property is
  worth more than any individual number.
- Do not commit anything from `evidence/runs/`. `face_secret.json`,
  `embedding.npy`, `post_embedding.npy` and `zk_secret.json` are biometric
  material and are gitignored for that reason.
