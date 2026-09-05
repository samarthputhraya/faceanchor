pragma circom 2.1.6;

include "node_modules/circomlib/circuits/poseidon.circom";
include "node_modules/circomlib/circuits/bitify.circom";

/*
 * FaceAnchor -- Proof of Honest Match
 * ==================================
 *
 * Attests, in zero knowledge, the arithmetic behind a face-match claim:
 *
 *   "I know two int8 embedding vectors A and B, and two salts, such that
 *    Poseidon-commit(A, saltA) and Poseidon-commit(B, saltB) are the published
 *    commitments, and the dot product and squared norms of A and B are the
 *    published integers."
 *
 * The verifier contract then derives cosine similarity from (dot, normA, normB)
 * and refuses to store a record whose claimed similarity is not backed by these
 * numbers.  An operator can no longer write an arbitrary similarity on-chain.
 *
 * WHAT THIS DOES NOT PROVE
 * ------------------------
 * It does not prove that A and B were actually produced by running ArcFace over
 * the scanned image and the post image -- that would require proving CNN
 * inference in zero knowledge, which is orders of magnitude larger.  A
 * dishonest operator who fabricates BOTH vectors can still satisfy this circuit
 * (trivially, by choosing A == B).
 *
 * What the proof buys is binding: the similarity written on-chain is provably
 * the true cosine of two vectors whose commitments are also on-chain, and
 * commitmentA is published at scan time -- before the search runs -- so it
 * cannot be retrofitted to whatever the search happened to find.  Full binding
 * of vector to image requires the disclosed secret, which the existing
 * `verify --biometric` path already checks.  This limitation is stated in the
 * README rather than papered over.
 */

// Pack `n` byte-valued signals into ceil(n/per) field elements, little-endian.
// Free in constraints: every output is a linear combination of the inputs.
template PackBytes(n, per) {
    signal input in[n];
    var nOut = (n + per - 1) \ per;
    signal output out[nOut];

    for (var k = 0; k < nOut; k++) {
        var acc = 0;
        var mul = 1;
        for (var j = 0; j < per; j++) {
            var idx = k * per + j;
            if (idx < n) {
                acc += in[idx] * mul;
                mul = mul * 256;
            }
        }
        out[k] <== acc;
    }
}

// Poseidon commitment over a 512-byte vector plus a 32-byte salt.
// circomlib's Poseidon caps out around arity 15, so 17 packed elements are
// hashed as a small tree: Poseidon(9) | Poseidon(9 incl. salt) -> Poseidon(2).
template CommitVector(D) {
    signal input v[D];
    signal input salt;
    signal output out;

    component pack = PackBytes(D, 31);
    for (var i = 0; i < D; i++) {
        pack.in[i] <== v[i];
    }

    var nP = (D + 30) \ 31;      // 17 for D = 512
    var half = 9;

    component h1 = Poseidon(half);
    for (var k = 0; k < half; k++) {
        h1.inputs[k] <== pack.out[k];
    }

    component h2 = Poseidon(nP - half + 1);
    for (var k = half; k < nP; k++) {
        h2.inputs[k - half] <== pack.out[k];
    }
    h2.inputs[nP - half] <== salt;

    component top = Poseidon(2);
    top.inputs[0] <== h1.out;
    top.inputs[1] <== h2.out;
    out <== top.out;
}

template FaceMatch(D) {
    // Private: the biometrics. These never leave the prover's machine.
    // Elements are int8 offset into [0, 255], i.e. a[i] = embedding[i] + 128.
    signal input a[D];
    signal input b[D];
    signal input saltA;
    signal input saltB;

    // Public: everything the chain sees.
    signal output commitmentA;
    signal output commitmentB;
    signal output dotOffset;   // <A,B> + D*128*128, so it is never negative
    signal output normA;       // <A,A>
    signal output normB;       // <B,B>

    // 1. Range checks. Without these a prover could supply out-of-range field
    //    elements and inflate the dot product arbitrarily. Not optional.
    component ra[D];
    component rb[D];
    for (var i = 0; i < D; i++) {
        ra[i] = Num2Bits(8);
        ra[i].in <== a[i];
        rb[i] = Num2Bits(8);
        rb[i].in <== b[i];
    }

    // 2. Recover signed values and accumulate the three integers.
    signal sa[D];
    signal sb[D];
    signal prod[D];
    signal sqa[D];
    signal sqb[D];

    var accDot = 0;
    var accNa = 0;
    var accNb = 0;

    for (var i = 0; i < D; i++) {
        sa[i] <== a[i] - 128;
        sb[i] <== b[i] - 128;
        prod[i] <== sa[i] * sb[i];
        sqa[i] <== sa[i] * sa[i];
        sqb[i] <== sb[i] * sb[i];
        accDot += prod[i];
        accNa += sqa[i];
        accNb += sqb[i];
    }

    var OFFSET = D * 128 * 128;   // 8388608 for D = 512

    dotOffset <== accDot + OFFSET;
    normA <== accNa;
    normB <== accNb;

    // 3. Pin dotOffset to a genuine small integer so it cannot wrap the field.
    //    max dotOffset = 2 * D * 128 * 128 = 2^24 for D = 512.
    component dchk = Num2Bits(25);
    dchk.in <== dotOffset;

    // 4. The commitments the chain already stores.
    component cA = CommitVector(D);
    for (var i = 0; i < D; i++) { cA.v[i] <== a[i]; }
    cA.salt <== saltA;
    commitmentA <== cA.out;

    component cB = CommitVector(D);
    for (var i = 0; i < D; i++) { cB.v[i] <== b[i]; }
    cB.salt <== saltB;
    commitmentB <== cB.out;
}

component main = FaceMatch(512);
