# Your job: test the date decoders

Hi — this is the whole of your job, start to finish. You do not need to read any
other file, and you do not need to coordinate with the other person: you two are
working on different files, so you cannot break each other's work. (Their brief
is `docs/TEAMMATE_2.md`, and it is not your job — ignore it.)

**Time:** about 1 hour, most of it the one-off setup.
**Deadline: tomorrow, Sunday 7 September — please push by early afternoon.**
The submission closes that night and everything has to be checked before it goes
in, so an evening push is too late to be useful.

**Do this before anything else:** send Samartha your **GitHub username** and wait
for him to confirm you have been added to the repo. Without it your work is done
but `git push` will fail at the last step, and that is the one problem in this
guide you cannot fix yourself.

**You do NOT need:** API keys, a crypto wallet, the face models, or any paid
service. Everything in your job is pure Python that runs offline.

---

## What you are actually doing, in one paragraph

When FaceAnchor finds a social media post, it needs to know **when** the post was
made. Most platforms don't tell you. So the code works it out from the numbers in
the URL — a Twitter/X post ID literally has its own timestamp buried inside it,
and so do TikTok, LinkedIn and Instagram IDs. That code lives in
`faceanchor/extract/post.py`, it is clever bit-shifting, and **it has no tests at
all**. If someone changed one of those shifts by accident, every date in the
project would silently be wrong and nothing would notice. Your job is to make
something notice.

---

# Part 0 — Setup (do this once, ~10 minutes)

Open a terminal:

- **Windows:** press Start, type `PowerShell`, press Enter.
- **Mac:** press Cmd+Space, type `Terminal`, press Enter.

Run these **one at a time**, and read the note under each one.

### 1. Check you have Python

```
python --version
```

You want **3.10 or newer** (e.g. `Python 3.12.10`).

- If it says "command not found" or opens the Microsoft Store, try `python3 --version`
  instead, and use `python3` everywhere below.
- If you have no Python at all, install it from https://python.org/downloads —
  on Windows, **tick "Add Python to PATH"** on the first screen of the installer.

### 2. Get the code

```
git clone https://github.com/samarthputhraya/faceanchor
cd faceanchor
```

Everything from here on must be run **inside that `faceanchor` folder**. If you
close the terminal, you have to `cd` back into it before doing anything else.

### 3. Make a private Python environment for this project

```
python -m venv .venv
```

Then switch it on.

**Windows:**
```
.venv\Scripts\activate
```

**Mac or Linux:**
```
source .venv/bin/activate
```

You know it worked because your prompt now starts with `(.venv)`.

> **Windows error "running scripts is disabled on this system"?**
> Run this once, then try `.venv\Scripts\activate` again:
> ```
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```
> Answer `Y` if it asks.

### 4. Install what the tests need

```
pip install -r requirements-ci.txt
```

This takes a couple of minutes. Some yellow warnings are normal. A red
`ERROR` is not — if you get one, copy the whole message and send it to Samartha
rather than trying to fix it.

### 5. Check everything works before you change anything

```
pytest -q
```

You should see a long row of dots and no failures, ending in `66 passed` — or
`72 passed` if teammate 2 has already pushed theirs. The exact number does
not matter; **no failures** does. **If this already fails, stop and tell
Samartha** — that is not your fault and not your job to fix.

### 6. Tell git who you are

This matters: without it your commit will be credited to the wrong person, or
rejected outright.

```
git config user.name "Your Name"
git config user.email "your@email.com"
```

Use the **same email as your GitHub account**, or GitHub will not show the commit
as yours.

---

# Part 1 — Write the tests

### Create the file

Create a new file at exactly this path:

```
tests/test_extract_timestamps.py
```

Easiest way: open the `faceanchor` folder in VS Code (or any editor), right-click
the `tests` folder, choose New File, and name it `test_extract_timestamps.py`.

Now paste in **everything** between the lines below — including the comment at
the top:

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

Save the file.

### Run it

```
pytest tests/test_extract_timestamps.py -q
```

**What you should see:**

```
......                                                                   [100%]
6 passed
```

Six dots, six passed.

> ### If a test FAILS
> **Do not change the expected date to make it pass.** A failure here means a
> real bug in the project, which is exactly what these tests exist to catch.
> Copy the failure output and send it to Samartha. That is a genuinely useful
> result, not a problem you caused.

> ### If you get `ModuleNotFoundError: No module named 'faceanchor'`
> You are in the wrong folder, or the `(.venv)` is off. `cd` into the
> `faceanchor` folder and re-run the activate command from step 3.

### Run the whole suite once more

```
pytest -q
```

Still all passing, now with 6 more. Good — you have added tests without breaking
anything.

---

# Part 2 — Commit and push

### 1. Check you are only committing your one file

```
git status
```

The only thing listed should be `tests/test_extract_timestamps.py`. If anything
else shows up, **do not commit** — tell Samartha first.

### 2. Commit

Copy this whole block as one command. The repeated `-m` flags become separate
paragraphs of the commit message, and it works the same in PowerShell and on Mac.

```
git add tests/test_extract_timestamps.py
git commit -m "test: cover the post-time decoders" -m "time_from_id recovers a post's timestamp from the numbers inside its URL for x, tiktok, linkedin and instagram, and had no tests. A change to any of the bit-shifts would have silently produced wrong dates on every record." -m "Also covers the two cases that must not guess: a URL with no id, and a platform we do not decode. Both report unknown."
```

### 3. Push

```
git pull --rebase
git push
```

**What you should see:** a few lines ending in something like
`main -> main`.

> **"Permission denied" or "403" on push?** You have not been added to the repo
> yet. Message Samartha with your GitHub username and he will add you — nothing
> is wrong with your work.

> **`git pull --rebase` reports a conflict?** It shouldn't — nobody else touches
> your file. Run `git rebase --abort` and message Samartha rather than resolving
> it yourself.

### 4. Confirm

Go to https://github.com/samarthputhraya/faceanchor/commits/main and check your
commit is there **with your name on it**. Then tell Samartha you are done.

---

# Part 3 — Only if you have time left over

### Write `zk/README.md`

The `zk/` folder holds a zero-knowledge circuit, a build script, and a pile of
generated files, with nothing explaining which is which. Someone reading it
cannot tell that `verification_key.json` is safe to publish while
`facematch_final.zkey` is **not** — and that distinction is the whole point.

Create `zk/README.md`, keep it **under one page**, and cover:

- what each file is, and which are committed vs ignored, **and why**
- how to rebuild everything from scratch (`zk/build.ps1`) and roughly how long it
  takes
- how someone checks a published proof **without** having the proving key
- one line saying the trusted setup is a local development ceremony, not a
  production one, linking to `docs/zero-knowledge-proof.md`

Everything you need is already written down in `zk/build.ps1` (read the comments
at the top) and `docs/zero-knowledge-proof.md`. Commit and push it the same way
as Part 2.

---

# The rules

- **Run `pytest -q` before every push.** Everything passes right now, so anything
  broken came from your change.
- **Never change a number in the README or in `docs/`** unless you also changed
  the thing it describes. Every figure in this project points at something real,
  and a reviewer will check.
- **Never commit anything from `evidence/runs/`.** It contains face data.
- **If something doesn't work, say so instead of forcing it.** A failing test is
  information. A test edited until it passes is a lie, and this project is being
  judged partly on honesty.

Any question at all, however small, message Samartha. Asking costs a minute;
guessing can cost the submission.
