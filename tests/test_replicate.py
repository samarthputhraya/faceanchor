"""Third-party replication of the post-side biometric.

The point of these tests is the *absence* of secrets: everything here works from
what a published bundle actually contains, so a reviewer who clones the repo and
runs them is doing what a verifier would do.

Anything needing the face model or the zk toolchain skips rather than fails, so
CI stays honest without a 182 MB model pack or a 9 MB proving key.
"""

from __future__ import annotations

import json
import shutil

import pytest

from faceanchor import config

SECRET_FILES = ("face_secret.json", "embedding.npy", "post_embedding.npy",
                "zk_secret.json", "zk_input.json", "zk_witness.wtns")


def _bundles():
    root = config.DEMO_EVIDENCE_ROOT
    if not root.exists():
        return []
    return [d for d in sorted(root.iterdir()) if (d / "record.json").exists()]


def _bundle_with_salt():
    for d in _bundles():
        rec = json.loads((d / "record.json").read_text(encoding="utf-8"))
        if rec.get("post", {}).get("zk_salt"):
            return d
    return None


def _models_present():
    try:
        from faceanchor.face.engine import get_engine

        get_engine("insightface").load()
        return True
    except Exception:
        return False


# --- the privacy property ---------------------------------------------------

@pytest.mark.parametrize("name", SECRET_FILES)
def test_published_bundles_carry_no_biometric_material(name):
    """The whole privacy argument collapses if any of these ships."""
    for d in _bundles():
        assert not (d / name).exists(), f"{d.name} publishes {name}"


def test_published_bundles_carry_no_raw_embedding_anywhere():
    """A 512-element numeric array in a published JSON would be an embedding."""
    for d in _bundles():
        for path in d.rglob("*.json"):
            blob = json.loads(path.read_text(encoding="utf-8"))

            def walk(node):
                if isinstance(node, list):
                    numeric = [v for v in node if isinstance(v, (int, float))]
                    assert len(numeric) < 128, f"{path} holds a {len(numeric)}-element vector"
                    for v in node:
                        walk(v)
                elif isinstance(node, dict):
                    for v in node.values():
                        walk(v)

            walk(blob)


# --- what a published bundle must contain to be checkable -------------------

def test_a_bundle_publishes_the_post_side_salt_but_not_the_scan_side():
    """salt_b is public on purpose; salt_a must never be."""
    d = _bundle_with_salt()
    if d is None:
        pytest.skip("no bundle with a published post-side salt")
    rec = json.loads((d / "record.json").read_text(encoding="utf-8"))

    assert rec["post"]["zk_salt"]
    assert rec["post"]["zk_commitment"]
    # 31 bytes: 32 would exceed the BN254 scalar field.
    assert len(bytes.fromhex(rec["post"]["zk_salt"])) == 31

    dumped = json.dumps(rec)
    for leak in ("salt_a", "zk_salt_a", "quantised_int8"):
        assert leak not in dumped, f"record leaks {leak}"


def test_the_proof_and_the_record_agree_on_the_post_commitment():
    d = _bundle_with_salt()
    if d is None:
        pytest.skip("no bundle with a published post-side salt")
    rec = json.loads((d / "record.json").read_text(encoding="utf-8"))
    if not rec.get("zk"):
        pytest.skip("bundle has no proof")
    assert rec["post"]["zk_commitment"] == rec["zk"]["commitment_b"]


# --- the replication itself -------------------------------------------------

@pytest.mark.skipif(not _models_present(), reason="face models not installed")
def test_post_commitment_is_reproducible_from_published_data_alone(tmp_path):
    """The claim this feature exists to support.

    Copy a bundle, strip every secret, and reproduce commitment_b from nothing
    but the published image, the named model and the published salt.
    """
    src = _bundle_with_salt()
    if src is None:
        pytest.skip("no bundle with a published post-side salt")
    if not (src / "post_image.jpg").exists():
        pytest.skip("bundle carries no post image")

    from faceanchor.face import fingerprint
    from faceanchor.face.engine import get_engine, load_image
    from faceanchor.zk import prover

    ok, why = prover.available()
    if not ok:
        pytest.skip(f"zk toolchain unavailable: {why}")

    d = tmp_path / src.name
    shutil.copytree(src, d)
    for name in SECRET_FILES:
        (d / name).unlink(missing_ok=True)
    assert not any((d / n).exists() for n in SECRET_FILES)

    rec = json.loads((d / "record.json").read_text(encoding="utf-8"))
    engine = get_engine(rec["face"]["engine"].split("/")[0])
    engine.load()
    assert engine.model_hashes() == rec["face"]["model_files"]

    faces = [f for f in engine.detect_and_embed(load_image(d / "post_image.jpg"))
             if f.embedding is not None]
    assert faces, "no face in the published post image"

    got = ""
    for f in faces:
        _, _, quantised = fingerprint.commit(f.embedding)
        c = prover.commitment(quantised, rec["post"]["zk_salt"])
        if c == rec["post"]["zk_commitment"]:
            got = c
            break
    assert got == rec["post"]["zk_commitment"], "commitment did not reproduce"


@pytest.mark.skipif(not _models_present(), reason="face models not installed")
def test_replicate_reports_a_mismatch_when_the_image_is_altered(tmp_path, monkeypatch):
    """A silent pass on a swapped image would make the check worthless."""
    src = _bundle_with_salt()
    if src is None or not (src / "post_image.jpg").exists():
        pytest.skip("no usable bundle")

    from PIL import Image

    from faceanchor import replicate as replicate_mod

    # Point the run roots at tmp_path rather than writing a tampered bundle
    # into evidence/: pytest owns the cleanup, and on Windows a model still
    # holding the file makes rmtree under OneDrive unreliable.
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr(config, "EVIDENCE_ROOT", root)
    monkeypatch.setattr(config, "DEMO_EVIDENCE_ROOT", tmp_path / "demo")

    d = root / src.name
    shutil.copytree(src, d)

    # Re-save at low quality: same picture, different bytes, and a different
    # quantised embedding.
    with Image.open(d / "post_image.jpg") as im:
        im.convert("RGB").save(d / "post_image.jpg", quality=25)

    out = replicate_mod.replicate(d.name, live=False)
    states = {c["check"]: c["state"] for c in out["checks"]}
    assert states["published image sha256"] == "FAIL"
    assert out["verdict"] == "MISMATCH"
    assert out["exit_code"] != 0
