# Your job: test the HTML readers

Hi — this is the whole of your job, start to finish. You do not need to read any
other file, and you do not need to coordinate with the other person: you two are
working on different files, so you cannot break each other's work. (Their brief
is `docs/TEAMMATE_1.md`, and it is not your job — ignore it.)

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

When FaceAnchor finds a social media post, it has to read that page to pull out
the author, the caption and the image. Sites bury this in two places: `<meta>`
tags in the page header, and a block of JSON called JSON-LD. There is a **real
bug that was already fixed by hand once**: on LinkedIn, the `og:title` tag
contains the *post text*, while the author's actual name is only in the JSON-LD.
So the code has to prefer JSON-LD for the author, or the author field ends up
holding a whole sentence. Right now nothing stops someone undoing that fix. Your
job is to lock it down, along with the rest of the parsing.

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
`72 passed` if teammate 1 has already pushed theirs. The exact number does
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
tests/test_extract_parsers.py
```

Easiest way: open the `faceanchor` folder in VS Code (or any editor), right-click
the `tests` folder, choose New File, and name it `test_extract_parsers.py`.

Now paste in **everything** between the lines below — including the comment at
the top:

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

Save the file.

### Run it

```
pytest tests/test_extract_parsers.py -q
```

**What you should see:**

```
......                                                                   [100%]
6 passed
```

Six dots, six passed.

> ### If a test FAILS
> **Do not change the test to make it pass.** A failure here means a real bug in
> the project, which is exactly what these tests exist to catch. Copy the failure
> output and send it to Samartha. That is a genuinely useful result, not a
> problem you caused.

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

The only thing listed should be `tests/test_extract_parsers.py`. If anything else
shows up, **do not commit** — tell Samartha first.

### 2. Commit

Copy this whole block as one command. The repeated `-m` flags become separate
paragraphs of the commit message, and it works the same in PowerShell and on Mac.

```
git add tests/test_extract_parsers.py
git commit -m "test: cover the OpenGraph and JSON-LD readers" -m "extract/post.py parses pages written by other people and had no tests. The author case is the important one: it is a regression guard for the LinkedIn fix, where og:title carries the post text and only the JSON-LD has the account name, so preferring og:title puts a whole sentence in the author field." -m "Also covers a malformed JSON-LD block, which must be skipped rather than kill the run, and a page with nothing useful, where an empty author is correct."
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

### Write `docs/threat-model.md`

The README lists the project's limitations and `docs/what-is-proven.md` explains
what the proof does and does not cover. Nothing anywhere says plainly **who might
misuse this tool and what stops them**. This is a face-search tool, so a reviewer
will absolutely ask.

Write `docs/threat-model.md` as a table — one row per person: what they want,
what they can reach, and what stops them.

Cover at least:

- **A dishonest operator** who wants a fake match on the record
- **Someone who gets hold of a published evidence bundle** — what can they
  actually learn from it?
- **Someone who steals `face_secret.json`** — they get the biometric. What should
  a real deployment do that this one does not?
- **The person whose face was searched**, who never agreed to it. What can they
  do? What *should* they be able to do?
- **A platform** whose pages we fetch — rate limits, terms of service

Read `docs/what-is-proven.md` first; most of the answers are already in there.

**Where the honest answer is "nothing stops them", write that.** A stated gap is
worth far more to a reviewer than an invented mitigation, and getting caught
inventing one is the fastest way to lose their trust. Commit and push it the same
way as Part 2.

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
