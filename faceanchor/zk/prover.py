"""Zero-knowledge proof that a face match is arithmetically honest.

The registry stores a similarity number, but nothing binds that number to
reality: an operator can write 9999 and no third party can tell.  This module
produces a Groth16 proof that the published dot product and squared norms
really belong to the two committed embeddings, so a verifier can derive the
cosine similarity itself and reject anything that disagrees.

WHAT THE PROOF DOES NOT COVER
-----------------------------
It does not prove the embeddings were produced by running ArcFace over the two
images -- that needs CNN inference in zero knowledge, which is orders of
magnitude larger.  Someone who fabricates BOTH vectors can satisfy the circuit
trivially by choosing A == B.  What it buys is binding: the similarity on-chain
is provably the true cosine of two vectors whose commitments are also on-chain,
and commitmentA is fixed at scan time, before the search runs, so it cannot be
retrofitted to whatever the search happened to return.  The README says this in
the same words.

Everything here shells out to Node.  snarkjs is pure JS, and circomlibjs is the
only implementation guaranteed to agree with circomlib's in-circuit Poseidon,
so reimplementing either in Python would risk silent divergence.
"""

from __future__ import annotations

import json
import math
import secrets
import subprocess
from pathlib import Path

from .. import config

ZK_DIR = config.ROOT / "zk"
BUILD = ZK_DIR / "build"
COMMIT_JS = ZK_DIR / "js" / "commit.mjs"
SNARKJS = ZK_DIR / "node_modules" / "snarkjs" / "build" / "cli.cjs"
WITNESS_JS = BUILD / "facematch_js" / "generate_witness.js"
WASM = BUILD / "facematch_js" / "facematch.wasm"
ZKEY = BUILD / "facematch_final.zkey"
VKEY = ZK_DIR / "verification_key.json"

# The circuit is compiled for one fixed width. insightface is 512-d; the OpenCV
# SFace fallback is 128-d and simply cannot be proved by this artifact.
DIM = 512
OFFSET = DIM * 128 * 128          # keeps dotOffset non-negative; matches the circuit
SALT_BYTES = 31                   # 32 would exceed the BN254 scalar field
TIMEOUT = 300

NODE = "node"


class ZkError(RuntimeError):
    """Proving failed for a reason the operator can act on."""


def available() -> tuple[bool, str]:
    """Whether a proof can be produced right now, and why not if it cannot."""
    try:
        subprocess.run([NODE, "--version"], capture_output=True, timeout=20, check=True)
    except Exception:
        return False, "node is not on PATH; install Node 20+ to generate proofs"
    for path, what in ((SNARKJS, "snarkjs"), (WASM, "the compiled circuit"),
                       (ZKEY, "the proving key"), (VKEY, "the verification key")):
        if not path.exists():
            return False, f"{what} is missing ({path.name}); run zk/build.ps1 first"
    return True, ""


def new_salt() -> str:
    """A salt that fits the BN254 scalar field. Not the 32-byte sha256 salt."""
    return secrets.token_bytes(SALT_BYTES).hex()


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=TIMEOUT, cwd=str(ZK_DIR))
    except subprocess.TimeoutExpired as exc:
        raise ZkError(f"{what} timed out after {TIMEOUT}s") from exc
    except FileNotFoundError as exc:
        raise ZkError(f"{what} could not start: {exc}") from exc


def commitment(quantised: list[int], salt_hex: str) -> str:
    """Poseidon commitment, identical to the circuit's CommitVector template."""
    if len(quantised) != DIM:
        raise ZkError(
            f"the circuit is compiled for {DIM}-d embeddings but this run is "
            f"{len(quantised)}-d. Only the insightface engine can be proved."
        )
    payload = json.dumps({"vector": [int(v) for v in quantised], "salt": salt_hex})
    p = _run([NODE, str(COMMIT_JS), payload], "poseidon commitment")
    if p.returncode != 0:
        raise ZkError(f"poseidon commitment failed: {(p.stderr or p.stdout).strip()[:400]}")
    return json.loads(p.stdout)["commitment"]


def _offset_bytes(quantised: list[int]) -> list[int]:
    out = []
    for v in quantised:
        v = int(v)
        if not -128 <= v <= 127:
            raise ZkError(f"embedding element outside int8 range: {v}")
        out.append(v + 128)
    return out


def similarity_bps(dot: int, norm_a: int, norm_b: int) -> int:
    """Cosine in basis points, derived only from the proven integers."""
    denom = math.sqrt(norm_a * norm_b)
    if denom <= 0:
        return 0
    return max(0, min(10000, int(10000 * dot / denom)))


def prove(run_dir: Path, quantised_a: list[int], salt_a: str,
          quantised_b: list[int], salt_b: str) -> dict:
    """Generate and self-verify a proof. Writes zk.json into the run directory."""
    ok, why = available()
    if not ok:
        raise ZkError(why)

    inputs = {
        "a": _offset_bytes(quantised_a),
        "b": _offset_bytes(quantised_b),
        "saltA": str(int(salt_a, 16)),
        "saltB": str(int(salt_b, 16)),
    }
    d = Path(run_dir).resolve()
    input_path = d / "zk_input.json"
    witness = d / "zk_witness.wtns"
    proof_path = d / "zk_proof.json"
    public_path = d / "zk_public.json"
    input_path.write_text(json.dumps(inputs), encoding="utf-8")

    try:
        p = _run([NODE, str(WITNESS_JS), str(WASM), str(input_path), str(witness)],
                 "witness generation")
        if p.returncode != 0:
            raise ZkError(f"witness generation failed: {(p.stderr or p.stdout).strip()[:400]}")

        p = _run([NODE, "--max-old-space-size=8192", str(SNARKJS), "groth16", "prove",
                  str(ZKEY), str(witness), str(proof_path), str(public_path)], "proving")
        if p.returncode != 0:
            raise ZkError(f"proving failed: {(p.stderr or p.stdout).strip()[:400]}")

        # Self-verify before publishing anything. A proof we cannot verify
        # ourselves must never reach the chain or the evidence bundle.
        p = _run([NODE, str(SNARKJS), "groth16", "verify",
                  str(VKEY), str(public_path), str(proof_path)], "verification")
        if p.returncode != 0:
            raise ZkError("the proof did not verify against our own verification "
                          "key; refusing to publish it")
    finally:
        # The witness and the input both contain the embeddings in the clear.
        witness.unlink(missing_ok=True)
        input_path.unlink(missing_ok=True)

    signals = [str(s) for s in json.loads(public_path.read_text(encoding="utf-8"))]
    commitment_a, commitment_b, dot_offset, norm_a, norm_b = signals
    dot = int(dot_offset) - OFFSET
    bps = similarity_bps(dot, int(norm_a), int(norm_b))

    out = {
        "scheme": "groth16/bn128",
        "circuit": "facematch.circom",
        "dimensions": DIM,
        "commitment_a": commitment_a,
        "commitment_b": commitment_b,
        "dot": dot,
        "norm_a": int(norm_a),
        "norm_b": int(norm_b),
        "similarity_bps": bps,
        "similarity": round(bps / 10000, 4),
        "public_signals": signals,
        "proof_file": proof_path.name,
        "public_file": public_path.name,
        "verified_locally": True,
        "proves": ("the published dot product and squared norms belong to the two "
                   "committed embeddings"),
        "does_not_prove": ("that those embeddings were produced by running the face "
                           "model on the two images"),
    }
    (d / "zk.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    return out
