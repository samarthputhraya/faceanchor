"""The face commitment must be reproducible, binding, and not reversible."""

from __future__ import annotations

import numpy as np
import pytest

from faceanchor.face.fingerprint import (
    commit, cosine_to_quantised, quantise, recommit,
)


@pytest.fixture
def embedding():
    rng = np.random.default_rng(7)
    v = rng.normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


def test_quantisation_is_deterministic(embedding):
    assert np.array_equal(quantise(embedding), quantise(embedding))


def test_quantisation_fits_in_int8(embedding):
    q = quantise(embedding)
    assert q.dtype == np.int8 and len(q) == 512


def test_the_commitment_can_be_recomputed_from_the_stored_secret(embedding):
    digest, salt, q = commit(embedding)
    assert recommit(q, salt) == digest


def test_the_same_face_with_a_different_salt_gives_a_different_commitment(embedding):
    """Without this, the chain would leak that two records are the same person."""
    a, _, _ = commit(embedding)
    b, _, _ = commit(embedding)
    assert a != b


def test_a_fixed_salt_gives_a_stable_commitment(embedding):
    salt = bytes(range(32))
    assert commit(embedding, salt)[0] == commit(embedding, salt)[0]


def test_a_different_face_gives_a_different_commitment(embedding):
    salt = bytes(range(32))
    other = np.roll(embedding, 3)
    assert commit(embedding, salt)[0] != commit(other, salt)[0]


def test_quantisation_preserves_identity_for_biometric_recheck(embedding):
    _, _, q = commit(embedding)
    assert cosine_to_quantised(embedding, q) > 0.99


def test_an_unrelated_face_does_not_match_the_stored_vector(embedding):
    _, _, q = commit(embedding)
    rng = np.random.default_rng(99)
    other = rng.normal(size=512).astype(np.float32)
    assert cosine_to_quantised(other, q) < 0.30


def test_a_zero_embedding_is_rejected_rather_than_hashed():
    with pytest.raises(ValueError):
        quantise(np.zeros(512, dtype=np.float32))
