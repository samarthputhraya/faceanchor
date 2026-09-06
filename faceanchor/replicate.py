"""Independent re-derivation of the post-side biometric.

The zero-knowledge proof shows the published similarity is the true cosine of
two *committed* embeddings.  It cannot show those embeddings came from the two
images -- that needs the face model inside the circuit, which is far beyond what
a laptop can prove.

For the scanned face that gap is unavoidable: the image is private on purpose.
For the face found in the post it is not, because the post is public.  Anyone
can fetch that image, run the same model and recompute the commitment.

That is why ``salt_b`` is published while ``salt_a`` is not.  Salting the
scanned face keeps a private biometric unlinkable across runs.  Salting the face
in a public post image hides nothing, because the image itself is public -- so
publishing that salt costs no privacy and buys complete verifiability.

Four links, checked in order:

  1. model identity   the weights here are the weights the record names
  2. published image  the bytes in the bundle hash to what the record claims
  3. commitment       those bytes, through that model, under the published
                      salt, reproduce commitment_b exactly -- and that is the
                      value the on-chain record and the zk proof both use
  4. live post        the image at the post URL is still that same picture

Steps 1-3 are fully offline and need no secret, no proving key and no trust in
whoever produced the record.  Step 4 needs the network and is reported as SKIP,
never FAIL, when a platform blocks the fetch: a CDN being down says nothing
about whether the record was honest.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

import numpy as np

from . import config
from .canonical import hamming64, phash_hex, phash_uint64, read_json, sha256_file, write_json
from .events import StageEvent
from .face import fingerprint
from .face.engine import cosine, get_engine, load_image
from .zk import prover as zk_prover

Emit = Callable[[StageEvent], None]

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# Two copies of one picture, one re-encoded by a CDN, stay within a few bits of
# each other. 10 of 64 is the usual "same image" bound, and the registry exposes
# hamming() so a verifier can have the chain make this comparison.
PHASH_SAME_IMAGE = 10


def _noop(_: StageEvent) -> None:
    return None


def _row(check: str, state: str, found: str = "", expected: str = "", note: str = "") -> dict:
    return {"check": check, "state": state, "found": found, "expected": expected, "note": note}


def replicate(run_id: str = "", live: bool = True, emit: Emit = _noop) -> dict:
    d = config.resolve_run(run_id, needs="record.json")
    record = read_json(d / "record.json")
    post = record["post"]

    emit(StageEvent("stage_start", "replicate",
                    "re-deriving the post-side face from published data only"))

    rows: list[dict] = []

    # --- 1. model identity --------------------------------------------------
    engine = get_engine(record["face"]["engine"].split("/")[0])
    engine.load()
    want_models = record["face"]["model_files"]
    have_models = engine.model_hashes()
    same = all(have_models.get(k) == v for k, v in want_models.items())
    rows.append(_row(
        "model identity", PASS if same else FAIL,
        ", ".join(f"{k}={v[:10]}" for k, v in sorted(have_models.items())),
        ", ".join(f"{k}={v[:10]}" for k, v in sorted(want_models.items())),
        "the embedding is only reproducible under these exact weights",
    ))

    img_path = d / "post_image.jpg"
    if not img_path.exists():
        rows.append(_row("published image", SKIP, "post_image.jpg absent", "",
                         "this bundle carries no post image"))
        return _finish(d, record, rows, emit)

    # --- 2. the published bytes are the ones the record describes -----------
    have_sha = sha256_file(img_path)
    want_sha = post.get("image_sha256") or ""
    rows.append(_row("published image sha256", PASS if have_sha == want_sha else FAIL,
                     have_sha, want_sha,
                     "the bytes in the bundle are the bytes that were scored"))

    # --- 3. re-derive the commitment ----------------------------------------
    faces = [f for f in engine.detect_and_embed(load_image(img_path)) if f.embedding is not None]
    if not faces:
        rows.append(_row("re-derived commitment", FAIL, "no face detected", "",
                         "the model found no face in the published image"))
        return _finish(d, record, rows, emit)

    want_commitment = post.get("zk_commitment") or (record.get("zk") or {}).get("commitment_b") or ""
    salt_b = post.get("zk_salt") or ""

    if want_commitment and salt_b:
        # Try every detected face: the record committed to one of them, and
        # which one is exactly what the commitment settles.
        got = ""
        for f in faces:
            _, _, quantised = fingerprint.commit(f.embedding)
            try:
                c = zk_prover.commitment(quantised, salt_b)
            except zk_prover.ZkError as exc:
                rows.append(_row("re-derived commitment", SKIP, str(exc)[:60], "",
                                 "the zk toolchain is not built on this machine"))
                c = ""
                break
            got = got or c
            if c == want_commitment:
                got = c
                break
        if got:
            rows.append(_row(
                "re-derived commitment", PASS if got == want_commitment else FAIL,
                got[:44] + "...", want_commitment[:44] + "...",
                "this image, under this model and the published salt, produces "
                "the committed vector",
            ))
    elif want_commitment:
        rows.append(_row("re-derived commitment", SKIP, "no salt in the record",
                         want_commitment[:44] + "...",
                         "this run predates the published post-side salt"))

    # --- 3b. and it is the same vector the pipeline scored ------------------
    emb_path = d / "post_embedding.npy"
    if emb_path.exists():
        stored = np.load(emb_path)
        best = max(cosine(stored, f.embedding) for f in faces)
        rows.append(_row("re-derived embedding", PASS if best > 0.99 else FAIL,
                         f"{best:.4f}", "> 0.9900",
                         "fresh embedding against the one the pipeline stored"))

    # --- 4. the live post still shows that picture --------------------------
    rows.append(_live_check(record, emit) if live
                else _row("live post image", SKIP, "--offline", "", "network check not run"))

    return _finish(d, record, rows, emit)


def _live_check(record: dict, emit: Emit) -> dict:
    """Fetch the post again and compare perceptual hashes.

    A CDN re-encode changes every byte, so sha256 is the wrong tool. A
    perceptual hash survives re-encoding, and the registry exposes hamming() so
    the comparison can be made by the chain rather than asserted here.
    """
    from .extract import post as post_mod

    post = record["post"]
    want = post.get("image_phash") or ""
    if not want:
        return _row("live post image", SKIP, "", "", "no pHash in the record")

    emit(StageEvent("log", "replicate", f"re-fetching {post['url']}"))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = post_mod.extract(post["url"], post["platform"], Path(tmp),
                                     use_browser=False)
            if not fresh.image_file:
                return _row("live post image", SKIP, "no image returned", want,
                            "the platform served no image to an unauthenticated client")
            live_phash = phash_hex(Path(tmp) / fresh.image_file)
    except Exception as exc:  # noqa: BLE001 - a blocked fetch is not a failure
        return _row("live post image", SKIP, type(exc).__name__, want,
                    "could not reach the post; this says nothing about the record")

    dist = hamming64(phash_uint64(live_phash), phash_uint64(want))
    return _row("live post image", PASS if dist <= PHASH_SAME_IMAGE else FAIL,
                f"{live_phash}  hamming {dist}", f"{want}  <= {PHASH_SAME_IMAGE}",
                "the image at the post URL is still the picture in the bundle")


def _finish(d: Path, record: dict, rows: list[dict], emit: Emit) -> dict:
    failed = [r for r in rows if r["state"] == FAIL]
    skipped = [r for r in rows if r["state"] == SKIP]
    passed = len(rows) - len(failed) - len(skipped)
    out = {
        "run_id": d.name,
        "post_url": record["post"]["url"],
        "checks": rows,
        "verdict": "REPLICATED" if not failed else "MISMATCH",
        "passed": passed, "failed": len(failed), "skipped": len(skipped),
        "exit_code": config.EXIT_OK if not failed else config.EXIT_NO_MATCH,
        "means": ("the face in the published post image is the one the record "
                  "committed to, re-derived here from published data alone"),
        "does_not_mean": ("that the scanned face matches its own input image. "
                          "That image is private by design, so binding it needs "
                          "the disclosed secret: verify --biometric"),
    }
    write_json(d / "replicate.json", out)
    emit(StageEvent("stage_end", "replicate",
                    f"{out['verdict']}  ({passed} passed, {len(failed)} failed, "
                    f"{len(skipped)} skipped)", out))
    return out
