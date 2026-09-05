"""Output must never be able to fail a run.

Social posts carry Devanagari, Thai, Arabic and emoji in their titles and URLs.
On Windows a redirected stream defaults to the ANSI code page, which raised
UnicodeEncodeError mid-pipeline and lost work that had already succeeded.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TRICKY = [
    "https://reddit.com/r/ahmedabad/comments/1v8pglo/why_the_hell_",
    "https://threads.com/@official_chunggu/post/DbvRgVBE6dR/한국어",
    "https://facebook.com/hypeness/posts/o-mais-recente-lançamento",
    "Stop to smell the roses 🌹 and then take a picture",
    "بحث عن صورة",
]


def test_rich_renders_non_ascii_onto_a_cp1252_stream():
    from rich.console import Console

    buffer = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="replace")
    console = Console(file=buffer, width=100, force_terminal=False)
    for text in TRICKY:
        console.print(text)          # must not raise
    buffer.flush()


def test_the_cli_survives_a_non_utf8_console():
    """Run the CLI in a subprocess whose stdout encoding is the ANSI code page."""
    import os

    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run(
        [sys.executable, "-m", "faceanchor", "--help"],
        cwd=ROOT, capture_output=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
