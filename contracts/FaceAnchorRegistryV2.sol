// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IGroth16Verifier {
    function verifyProof(
        uint[2] calldata pA,
        uint[2][2] calldata pB,
        uint[2] calldata pC,
        uint[5] calldata pubSignals
    ) external view returns (bool);
}

/// @title FaceAnchorRegistryV2
/// @notice A registry that refuses to store a face match it has not been shown
///         proof of.
/// @dev V1 stored `similarityBps` as a number the submitter simply asserted.
///      Nothing stopped an operator writing 9999 for an unrelated face, and no
///      third party could tell. V2 will not write a record at all unless a
///      Groth16 proof shows that the claimed similarity really is the cosine of
///      two committed embeddings.
///
///      The circuit's public signals are
///          [commitmentA, commitmentB, dotOffset, normA, normB]
///      where commitmentA is the Poseidon commitment to the scanned face,
///      commitmentB the same for the face found in the post, and dotOffset is
///      the dot product shifted by DOT_OFFSET so it is never negative.
///
///      Still only hashes and integers are stored. No image, no embedding and
///      no personal data goes on-chain; the commitments are Poseidon hashes of
///      salted, quantised vectors whose salts never leave the operator's
///      machine.
///
///      HONEST LIMIT: the proof binds the similarity to the commitments. It
///      does NOT prove those embeddings came from running a face model over
///      the two images -- that would need CNN inference in zero knowledge.
///      An operator who fabricates BOTH vectors can still satisfy the circuit.
///      What they cannot do is inflate the similarity of a pair they committed
///      to, or retrofit commitmentA, which is published before the search runs.
contract FaceAnchorRegistryV2 {
    /// @dev D * 128 * 128 for D = 512. Must equal the circuit's OFFSET.
    uint256 public constant DOT_OFFSET = 8388608;

    /// @dev Cosine below this is not a match. 4000 bps = 0.40, the same
    ///      threshold the pipeline uses for insightface.
    uint16 public constant MIN_SIMILARITY_BPS = 4000;

    IGroth16Verifier public immutable verifier;

    struct Record {
        bytes32 inputImageSha256;
        bytes32 faceCommitment;    // sha256 commitment, as in V1
        bytes32 postUrlHash;
        bytes32 postImageSha256;
        uint64  inputPHash;
        uint16  similarityBps;     // proven, not asserted
        uint64  anchoredAt;
        address submitter;
        uint256 zkCommitmentA;     // Poseidon commitment, scanned face
        uint256 zkCommitmentB;     // Poseidon commitment, face in the post
        uint256 dot;               // <A,B>, proven
        uint256 normA;             // <A,A>, proven
        uint256 normB;             // <B,B>, proven
        string  evidenceUri;
    }

    mapping(bytes32 => Record) private _records;
    bytes32[] public recordIds;

    event Anchored(
        bytes32 indexed recordHash,
        bytes32 indexed postUrlHash,
        address indexed submitter,
        bytes32 inputImageSha256,
        bytes32 faceCommitment,
        bytes32 postImageSha256,
        uint64  inputPHash,
        uint16  similarityBps,
        uint64  anchoredAt,
        uint256 zkCommitmentA,
        uint256 zkCommitmentB,
        string  evidenceUri
    );

    error ZeroRecordHash();
    error RecordExists(bytes32 recordHash, uint64 anchoredAt);
    /// @dev The proof is not valid for these public signals.
    error BadProof();
    /// @dev The claimed similarity is not supported by the proven integers.
    error SimilarityNotProven(uint16 claimedBps);
    /// @dev A negative dot product cannot be a match.
    error NegativeDot();
    /// @dev Cosine is below the match threshold.
    error BelowThreshold(uint16 similarityBps);

    constructor(address verifier_) {
        verifier = IGroth16Verifier(verifier_);
    }

    struct Proof {
        uint[2] a;
        uint[2][2] b;
        uint[2] c;
        uint[5] pubSignals;
    }

    /// @dev Grouped into a calldata struct rather than passed as loose
    ///      arguments: fourteen locals in one function overflows the EVM stack
    ///      ("stack too deep"), and a struct is cheaper than forcing viaIR on
    ///      the whole build.
    struct Claim {
        bytes32 recordHash;
        bytes32 inputImageSha256;
        bytes32 faceCommitment;
        bytes32 postUrlHash;
        bytes32 postImageSha256;
        uint64  inputPHash;
        uint16  similarityBps;
        string  evidenceUri;
    }

    /// @dev Reverts unless the proof is valid AND supports the claimed
    ///      similarity. Split out of anchor() to keep both stacks shallow.
    ///      Returns the proven integers (dot, normA, normB) for storage.
    function _check(Claim calldata claim, Proof calldata proof)
        internal view returns (uint256 dot, uint256 normA, uint256 normB)
    {
        if (!verifier.verifyProof(proof.a, proof.b, proof.c, proof.pubSignals)) {
            revert BadProof();
        }
        if (proof.pubSignals[2] < DOT_OFFSET) revert NegativeDot();
        dot = proof.pubSignals[2] - DOT_OFFSET;
        normA = proof.pubSignals[3];
        normB = proof.pubSignals[4];

        // The claimed similarity must be backed by the proven integers:
        //   cos = dot / sqrt(normA * normB) >= bps / 1e4
        //     <=> dot^2 * 1e8 >= bps^2 * normA * normB
        // Squaring removes the square root entirely. Magnitudes are tiny next
        // to 2^256: dot <= 2^23, so neither side passes ~7e21.
        uint256 bps = uint256(claim.similarityBps);
        if (bps * bps * normA * normB > dot * dot * 1e8) {
            revert SimilarityNotProven(claim.similarityBps);
        }
        if (claim.similarityBps < MIN_SIMILARITY_BPS) {
            revert BelowThreshold(claim.similarityBps);
        }
    }

    /// @notice Anchor one evidence record, but only against a valid proof.
    function anchor(Claim calldata claim, Proof calldata proof)
        external returns (uint256 index)
    {
        if (claim.recordHash == bytes32(0)) revert ZeroRecordHash();
        Record storage existing = _records[claim.recordHash];
        if (existing.anchoredAt != 0) {
            revert RecordExists(claim.recordHash, existing.anchoredAt);
        }

        (uint256 dot, uint256 normA, uint256 normB) = _check(claim, proof);

        _records[claim.recordHash] = Record({
            inputImageSha256: claim.inputImageSha256,
            faceCommitment: claim.faceCommitment,
            postUrlHash: claim.postUrlHash,
            postImageSha256: claim.postImageSha256,
            inputPHash: claim.inputPHash,
            similarityBps: claim.similarityBps,
            anchoredAt: uint64(block.timestamp),
            submitter: msg.sender,
            zkCommitmentA: proof.pubSignals[0],
            zkCommitmentB: proof.pubSignals[1],
            dot: dot,
            normA: normA,
            normB: normB,
            evidenceUri: claim.evidenceUri
        });
        recordIds.push(claim.recordHash);

        emit Anchored(
            claim.recordHash, claim.postUrlHash, msg.sender,
            claim.inputImageSha256, claim.faceCommitment, claim.postImageSha256,
            claim.inputPHash, claim.similarityBps, uint64(block.timestamp),
            proof.pubSignals[0], proof.pubSignals[1], claim.evidenceUri
        );
        return recordIds.length - 1;
    }

    function exists(bytes32 recordHash) external view returns (bool) {
        return _records[recordHash].anchoredAt != 0;
    }

    function get(bytes32 recordHash) external view returns (Record memory) {
        return _records[recordHash];
    }

    function count() external view returns (uint256) {
        return recordIds.length;
    }

    /// @notice Re-verify a locally recomputed record against the chain.
    function verify(
        bytes32 recordHash,
        bytes32 inputImageSha256,
        bytes32 faceCommitment,
        bytes32 postUrlHash,
        bytes32 postImageSha256
    ) external view returns (bool ok, bool found, bool imageOk, bool faceOk, bool postOk, bool postImageOk) {
        Record memory r = _records[recordHash];
        found = r.anchoredAt != 0;
        imageOk = r.inputImageSha256 == inputImageSha256;
        faceOk = r.faceCommitment == faceCommitment;
        postOk = r.postUrlHash == postUrlHash;
        postImageOk = r.postImageSha256 == postImageSha256;
        ok = found && imageOk && faceOk && postOk && postImageOk;
    }

    /// @notice Hamming distance between two perceptual hashes.
    function hamming(uint64 a, uint64 b) external pure returns (uint8 d) {
        uint64 x = a ^ b;
        while (x != 0) {
            d++;
            x &= x - 1;
        }
    }
}
