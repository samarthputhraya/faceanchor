"""The record hash is only meaningful if the bytes behind it are reproducible."""

from __future__ import annotations

import json

import pytest

from faceanchor.canonical import (
    canonical_bytes, hamming64, phash_uint64, record_hash, sha256_bytes, sha256_text,
)


def test_key_order_does_not_change_the_bytes():
    a = {"b": 1, "a": {"y": 2, "x": 3}, "c": [1, 2]}
    b = {"a": {"x": 3, "y": 2}, "c": [1, 2], "b": 1}
    assert canonical_bytes(a) == canonical_bytes(b)


def test_floats_are_rounded_so_tiny_drift_cannot_change_the_hash():
    a = {"similarity": 0.61234999}
    b = {"similarity": 0.6123}
    assert record_hash(a) == record_hash(b)


def test_nulls_are_dropped_not_serialised():
    assert canonical_bytes({"a": 1, "b": None}) == canonical_bytes({"a": 1})


def test_non_ascii_survives_round_trip():
    data = canonical_bytes({"caption": "café ünïcode 日本語"})
    assert json.loads(data.decode("utf-8"))["caption"] == "café ünïcode 日本語"


def test_a_single_character_change_changes_the_hash():
    base = {"post": {"caption": "hello world"}}
    tampered = {"post": {"caption": "hello worle"}}
    assert record_hash(base) != record_hash(tampered)


def test_record_hash_is_plain_sha256_of_the_file_bytes(tmp_path):
    """A third party must be able to reproduce it with sha256sum."""
    record = {"schema": "faceanchor.record/v1", "run_id": "x", "n": 1}
    data = canonical_bytes(record)
    p = tmp_path / "record.json"
    p.write_bytes(data)
    assert sha256_bytes(p.read_bytes()) == record_hash(record)


def test_hamming_distance_of_perceptual_hashes():
    a = phash_uint64("c3a5f0e1d2b48796")
    assert hamming64(a, a) == 0
    assert hamming64(a, a ^ 0b1011) == 3


def test_sha256_text_matches_hashlib():
    import hashlib

    assert sha256_text("abc") == hashlib.sha256(b"abc").hexdigest()
