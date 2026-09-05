"""Privacy-preserving face commitment.

Raw face embeddings are biometric templates and are invertible back to a
recognisable face (IdDecoder, CODASPY'23; arXiv 2310.03005), so they must never
go on-chain.  We publish only

    commitment = sha256(b"faceanchor-v1" || salt32 || int8(embedding * 127))

The salt and the quantised vector stay in ``face_secret.json``, which is
gitignored.  A verifier who is given the secret can recompute the commitment
exactly; nobody else can brute-force it from the chain.
"""

from __future__ import annotations

import hashlib
import secrets

import numpy as np

DOMAIN = b"faceanchor-v1"
SCALE = 127.0


def quantise(embedding: np.ndarray) -> np.ndarray:
    """L2-normalise then map to int8. Deterministic for a given vector."""
    e = np.asarray(embedding, dtype=np.float32).ravel()
    n = np.linalg.norm(e)
    if n == 0:
        raise ValueError("zero embedding")
    return np.round((e / n) * SCALE).astype(np.int8)


def commit(embedding: np.ndarray, salt: bytes | None = None) -> tuple[str, str, list[int]]:
    """Return (commitment_hex, salt_hex, quantised_vector)."""
    salt = salt or secrets.token_bytes(32)
    q = quantise(embedding)
    digest = hashlib.sha256(DOMAIN + salt + q.tobytes()).hexdigest()
    return digest, salt.hex(), [int(v) for v in q]


def recommit(quantised: list[int], salt_hex: str) -> str:
    """Recompute the commitment from the stored secret (used by verify)."""
    q = np.asarray(quantised, dtype=np.int8)
    return hashlib.sha256(DOMAIN + bytes.fromhex(salt_hex) + q.tobytes()).hexdigest()


def cosine_to_quantised(embedding: np.ndarray, quantised: list[int]) -> float:
    """Biometric re-verification: fresh embedding vs the stored int8 vector."""
    a = np.asarray(embedding, dtype=np.float32).ravel()
    b = np.asarray(quantised, dtype=np.float32).ravel()
    a /= np.linalg.norm(a) or 1.0
    b /= np.linalg.norm(b) or 1.0
    return float(np.dot(a, b))
