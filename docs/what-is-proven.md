# What is actually proven

A record makes one claim: *this scanned face matches the face in this social
post, at this similarity.* That claim rests on four separate bindings, and they
are not equally strong. This page says exactly which is which.

Most systems of this kind state the claim and stop. The interesting question is
which link a determined liar would attack, so each one is listed with the
mechanism that holds it and — where nothing holds it — that is said plainly.

## The four bindings

| # | Binding | Mechanism | Who can check it | Strength |
|---|---|---|---|---|
| 1 | similarity ← the two committed vectors | Groth16 proof, verified **on-chain** | anyone, from the bundle | **Proven** |
| 2 | post-side vector ← the published post image | re-run the model under the published `salt_b` | anyone, from the bundle | **Proven** |
| 3 | published post image ← the live post URL | perceptual hash, `hamming()` on-chain | anyone, with network | **Proven** (bounded) |
| 4 | scan-side vector ← the scanned input image | re-run the model on `input.jpg` | **only** with the disclosed secret | **Conditional** |

Run `python -m faceanchor replicate` for 2 and 3, and
`python -m faceanchor forge-demo` for 1.

## 1. The similarity cannot be inflated

`FaceAnchorRegistryV2` will not store a record unless a Groth16 proof shows the
claimed similarity really is the cosine of the two committed vectors. This is
enforced by the contract, not by us:

```
honest       0.9916   ACCEPTED
forged       0.9999   REJECTED   SimilarityNotProven
off-by-one   0.9917   REJECTED   SimilarityNotProven
```

One basis point over the truth is refused. Detail:
[zero-knowledge-proof.md](zero-knowledge-proof.md).

## 2. The post-side face is re-derivable by anyone

The face in the post is in a **public image**. Anyone can fetch it, run the same
model and recompute the commitment — so `salt_b` is published in the record
while `salt_a` is not.

That asymmetry is deliberate. Salting the scanned face keeps a private biometric
unlinkable across runs, which is the whole reason the commitment scheme exists.
Salting a face in a public image hides nothing: the image is already public, so
the vector is already computable. Keeping that salt secret would cost a verifier
everything and cost an attacker nothing.

With it published, the chain is complete and needs no trust in the operator:

```
post_image.jpg  ──model──►  embedding  ──Poseidon(·, salt_b)──►  commitment_b
                                                                      ║
                                        the value the proof uses  ═════╝
                                        and the chain stores
```

Verified against a copy of the sanitised bundle with `post_embedding.npy` and
`zk_secret.json` removed: the commitment reproduces exactly, using no secret.

## 3. The published image is the one at the post URL

`replicate` re-fetches the post and compares perceptual hashes. `sha256` is the
wrong tool here — a CDN re-encode changes every byte while leaving the picture
identical — so the comparison is a Hamming distance over 64-bit pHashes, with
the registry's `hamming()` available so a verifier can have the chain do it.

Bounded, not absolute: a perceptual hash says *this is the same picture*, not
*these are the same bytes*. On the demo run the distance is **0**.

This check is a **SKIP, never a FAIL**, when a platform blocks the fetch. A CDN
being down is not evidence that a record was dishonest, and scoring it as a
failure would make the tool cry wolf.

## 4. The scan-side binding is conditional, and that is by design

**Nothing in the published bundle proves the scanned vector came from
`input.jpg`.** An operator who fabricates a vector, commits to it and proves
against it produces a record that passes every public check.

This is the one gap that cannot be closed with a proof, and it is worth being
precise about why.

### Why not just prove it too?

Because that means running ArcFace inside the circuit. The comparison circuit is
17,797 constraints. ArcFace `w600k_r50` is a ResNet-50 — roughly 4 billion
multiply-accumulates. Even at one constraint each that is ~15× beyond the
largest trusted setup that exists (2^28 ≈ 268M), and published ResNet-50 proving
takes over 24 hours on general tooling; the sub-10-second results come from
dedicated ASIC hardware. On a CPU laptop this is not a matter of effort.

### What is left instead

- **Ordering.** `commitmentA` is fixed during `scan`, before any search runs. An
  operator cannot see what the search returns and then work backwards to a
  flattering pair — they would have to have guessed the match in advance.
- **The attack is narrower than it looks.** Because binding 2 holds, a forger
  cannot invent the post-side vector. To fake a high similarity they must commit
  to a vector genuinely close to a *publicly verifiable* target — which is
  essentially possessing a photograph that embeds near that person's face. That
  is not far off the legitimate use of the tool.
- **Full binding on request.** `verify --biometric` re-runs the model against
  `input.jpg` and checks the commitment. It needs `face_secret.json`, which is
  never published. A subject, a platform or a court can be given it; the open
  internet is not.

## The honest one-line summary

> The similarity cannot be inflated, and the face found in the post can be
> re-derived by anyone from published data. The scanned face is bound to its own
> image only for someone holding the disclosed secret — proving that publicly
> would require the neural network inside the circuit, which no laptop can do.

## What a reviewer should try

```bash
# 1. the similarity is real - ask the live contract to accept a lie
python -m faceanchor forge-demo --run <run_id>

# 2. and 3. re-derive the post-side face yourself
python -m faceanchor replicate --run <run_id>

# check the proof with nothing but the published bundle
cd zk && npm install && cd ..    # once: zk/node_modules is not committed
node zk/node_modules/snarkjs/build/cli.cjs groth16 verify \
  zk/verification_key.json \
  evidence/demo/<run_id>/zk_public.json \
  evidence/demo/<run_id>/zk_proof.json

# every hash, recomputed from disk and re-read from the chain
python verify.py --record evidence/demo/<run_id>/record.json
```
