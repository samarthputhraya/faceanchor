# Proof of Honest Match

## The problem this solves

FaceAnchor v1 writes a record to a registry contract. One field is
`similarityBps` — the cosine similarity between the scanned face and the face
found in the social post, in basis points.

Nothing bound that number to anything. The operator's Python computed it, and
the operator's wallet wrote it. An operator who wanted to could have anchored
`9999` against a photograph of a stranger, and no third party could have told
the difference. Every other guarantee in the system — the canonical record, the
hash on-chain, the tamper demo — protects the record *after* it is written. None
of them says the record was true when written.

That is the gap this closes.

## What is proved

A Groth16 proof over BN254, generated from `zk/facematch.circom`.

**Private inputs** (never leave the machine): two 512-element int8 embedding
vectors `A` and `B`, and two salts.

**Public outputs** (all five go on-chain):

| signal | meaning |
|---|---|
| `commitmentA` | Poseidon commitment to the scanned face |
| `commitmentB` | Poseidon commitment to the face in the post |
| `dotOffset` | `⟨A,B⟩ + 8388608`, shifted so it is never negative |
| `normA` | `⟨A,A⟩` |
| `normB` | `⟨B,B⟩` |

The circuit proves all of this at once:

1. `A` and `B` really are the vectors behind `commitmentA` and `commitmentB`.
2. Every element of both is a genuine 8-bit value.
3. `dotOffset`, `normA` and `normB` are the true dot product and squared norms.

The contract then derives cosine similarity from those three integers and
refuses to store a record claiming anything higher.

## What is *not* proved

**The proof does not show the embeddings came from the images.** Proving that
would mean running ArcFace inference inside the circuit — millions of
constraints, not seventeen thousand.

Concretely: someone who fabricates *both* vectors can satisfy this circuit
trivially, by choosing `A == B`. That is a real limitation and it is stated
here, in the contract, in the circuit header and in the CLI output, rather than
buried.

What the proof does buy:

- **The number cannot be inflated.** The similarity on-chain is provably the
  cosine of two vectors whose commitments are also on-chain. Claiming one basis
  point more than the truth is rejected.
- **`commitmentA` cannot be retrofitted.** It is published during `scan`,
  before any search runs. The operator cannot see what the search returns and
  then work backwards to a flattering pair.
- **Full binding is still available**, just not in zero knowledge: a verifier
  given `face_secret.json` can recompute the commitment *and* re-run the face
  model against `input.jpg`. That is the existing `verify --biometric` path.

## Why Poseidon and not SHA-256

The obvious design reuses the existing sha256 commitment. It does not fit.

circomlib's `Sha256(nBits)` costs roughly 29,000 non-linear constraints per
512-bit block. The preimage here is the domain tag (13 bytes) plus a salt
(32 bytes) plus 512 int8 values — 4,456 bits, so nine blocks. That is about
250,000 constraints before any of the actual work, which needs a `pot19` file
of roughly 630 MB and pushes snarkjs into out-of-memory territory.

Poseidon is built for this. `Sigma()` (the `x^5` S-box) is 3 constraints, and
the Ark/Mix layers are linear, so a Poseidon instance costs
`3 * (t*8 + nRoundsP)` — 240 constraints at arity 2, 612 at arity 16. Two
commitments cost under 2,000 constraints instead of 250,000.

The sha256 commitment is still published and still in the record. Poseidon is an
additional commitment to the same vector, not a replacement.

### Constraint budget

| part | constraints |
|---|---|
| `Num2Bits(8)` range checks, 1024 elements | ~9,200 |
| dot product and both squared norms | 1,536 |
| two Poseidon commitments (chunked) | ~1,900 |
| `Num2Bits(25)` on `dotOffset` | ~26 |
| **total (measured)** | **17,797** |

`powersOfTau28_hez_final_15` covers 32,768. Proving takes about 10 seconds on
CPU; verifying on-chain costs roughly 200–250k gas.

## Three details that are load-bearing

**The range checks are the soundness.** Without `Num2Bits(8)` on every element,
a prover can supply field elements far outside `[-128, 127]` and manufacture any
dot product they like. circomlib's comparators deliberately do not range-check
their own inputs, and omitting these is a known, disclosed vulnerability class in
production circuits. They are ~9,200 of the 17,797 constraints — over half the
circuit exists to make the other half mean something.

**The salt is 31 bytes, not 32.** A 32-byte salt is up to 256 bits and the BN254
scalar field is ~254 bits, so a 32-byte value can silently wrap and produce a
commitment that cannot be reproduced. `zk/js/commit.mjs` rejects any salt at or
above the field order rather than wrapping it.

**circomlib's Poseidon caps out near arity 15.** A 512-byte vector packs into 17
field elements at 31 bytes each, which is over the limit, so the commitment is a
small tree: `Poseidon(9)` over the first nine, `Poseidon(9)` over the remaining
eight plus the salt, then `Poseidon(2)` over those two results.

## The trusted setup is a development ceremony

Every public Powers of Tau mirror listed in the snarkjs README is unreachable as
of September 2026 — `storage.googleapis.com/zkevm/ptau`,
`hermez.s3-eu-west-1.amazonaws.com` and the PSE S3 bucket all return
`AccessDenied` (a genuine upstream permissions response, verified with a ranged
GET, not a local network filter).

So `zk/build.ps1` runs its own ceremony, exactly as iden3's own CI does.

**This means the setup is not trusted.** Whoever ran `build.ps1` could, in
principle, hold the toxic waste and forge proofs. For a demo that is fine and it
is stated plainly; for a real deployment the phase-1 file must come from the
Perpetual Powers of Tau and phase 2 needs contributions from parties who do not
all collude.

## The contract check

`FaceAnchorRegistryV2.anchor()` rejects unless all of these hold:

1. `verifier.verifyProof(...)` returns true for exactly these public signals.
2. `dotOffset >= DOT_OFFSET`, so the dot product is not negative.
3. The claimed similarity is supported by the proven integers.
4. The similarity is at or above the match threshold (4000 bps).

Check 3 avoids a square root by comparing squares:

```
cos = dot / sqrt(normA * normB) >= bps / 1e4
  <=>  dot^2 * 1e8 >= bps^2 * normA * normB
```

`dot` is at most `2^23`, so neither side approaches `2^256` and no intermediate
can overflow.

Two Solidity notes. `anchor()` takes a `Claim` calldata struct because fourteen
loose arguments overflow the EVM stack, and the build needs `viaIR` because the
twelve-argument `Anchored` event goes stack-too-deep without it. Both are
codegen limits rather than design choices.

## Reproducing it

```bash
powershell -ExecutionPolicy Bypass -File zk/build.ps1     # ~3 minutes
python -m faceanchor prove --run <run_id>
python -m faceanchor anchor --run <run_id> --chain base-sepolia
python -m faceanchor forge-demo --run <run_id>
```

Verifying a published proof needs neither the proving key nor the toolchain —
only `verification_key.json` and the two files in the evidence bundle:

```bash
node zk/node_modules/snarkjs/build/cli.cjs groth16 verify \
  zk/verification_key.json \
  evidence/demo/<run_id>/zk_public.json \
  evidence/demo/<run_id>/zk_proof.json
```

## Deployed

| what | where |
|---|---|
| Groth16Verifier | [`0xf1175acf6A63c23f967431F1c7feB46eC1957c22`](https://sepolia.basescan.org/address/0xf1175acf6A63c23f967431F1c7feB46eC1957c22) |
| FaceAnchorRegistryV2 | [`0x3827D54282047caDA437D7AeBB33e05D617Ca1b9`](https://sepolia.basescan.org/address/0x3827D54282047caDA437D7AeBB33e05D617Ca1b9) |
| FaceAnchorRegistry (v1, still live) | [`0xAFeB0eDaC32b4fD7710418211619ddE36C735D43`](https://sepolia.basescan.org/address/0xAFeB0eDaC32b4fD7710418211619ddE36C735D43) |

The generated verifier is GPL-3.0 under its own header, as snarkjs emits it. The
rest of the repository is MIT.
