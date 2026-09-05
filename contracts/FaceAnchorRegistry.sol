// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title FaceAnchorRegistry
/// @notice Tamper-evident registry of face-match evidence records.
/// @dev Only hashes are stored. No image, no face embedding and no personal
///      data is ever written on-chain: `faceCommitment` is a salted SHA-256 of
///      an int8-quantised embedding whose salt never leaves the operator's
///      machine, so the record cannot be reversed into a biometric template.
///      The key `recordHash` is sha256(canonical record.json), which anyone can
///      recompute from the published evidence bundle with `sha256sum`.
contract FaceAnchorRegistry {
    struct Record {
        bytes32 inputImageSha256;  // sha256 of the scanned input image bytes
        bytes32 faceCommitment;    // sha256("faceanchor-v1" || salt || int8 embedding)
        bytes32 postUrlHash;       // sha256 of the canonical social post URL
        bytes32 postImageSha256;   // sha256 of the image fetched from that post
        uint64  inputPHash;        // 64-bit perceptual hash of the input image
        uint16  similarityBps;     // cosine similarity in basis points (0.6123 -> 6123)
        uint64  anchoredAt;        // block timestamp; 0 means "no such record"
        address submitter;
        string  evidenceUri;       // "sha256:<hex>" or "ipfs://<cid>"
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
        string  evidenceUri
    );

    error ZeroRecordHash();
    error RecordExists(bytes32 recordHash, uint64 anchoredAt);

    /// @notice Anchor one evidence record. Immutable once written.
    function anchor(
        bytes32 recordHash,
        bytes32 inputImageSha256,
        bytes32 faceCommitment,
        bytes32 postUrlHash,
        bytes32 postImageSha256,
        uint64 inputPHash,
        uint16 similarityBps,
        string calldata evidenceUri
    ) external returns (uint256 index) {
        if (recordHash == bytes32(0)) revert ZeroRecordHash();
        Record storage existing = _records[recordHash];
        if (existing.anchoredAt != 0) revert RecordExists(recordHash, existing.anchoredAt);

        _records[recordHash] = Record({
            inputImageSha256: inputImageSha256,
            faceCommitment: faceCommitment,
            postUrlHash: postUrlHash,
            postImageSha256: postImageSha256,
            inputPHash: inputPHash,
            similarityBps: similarityBps,
            anchoredAt: uint64(block.timestamp),
            submitter: msg.sender,
            evidenceUri: evidenceUri
        });
        recordIds.push(recordHash);

        emit Anchored(
            recordHash, postUrlHash, msg.sender,
            inputImageSha256, faceCommitment, postImageSha256,
            inputPHash, similarityBps, uint64(block.timestamp), evidenceUri
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
    /// @return ok true only when the record exists and every field matches.
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
    /// @dev Lets a verifier show that a re-encoded copy of the image is still
    ///      the same picture (small distance) without storing the image.
    function hamming(uint64 a, uint64 b) external pure returns (uint8 d) {
        uint64 x = a ^ b;
        while (x != 0) {
            d++;
            x &= x - 1;
        }
    }
}
