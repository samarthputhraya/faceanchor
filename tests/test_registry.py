"""End-to-end registry behaviour against a real EVM running in-process.

These tests need no network, no key and no faucet, so CI can run them.
"""

from __future__ import annotations

import hashlib

import pytest

from faceanchor.chain.client import ChainClient


def h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture(scope="module")
def registry():
    client = ChainClient("local")
    deployment = client.deploy()
    return client, deployment["contract"]


@pytest.fixture(scope="module")
def anchored(registry):
    client, address = registry
    fields = {
        "record_hash": h("record"),
        "input_image_sha256": h("input-image"),
        "face_commitment": h("commitment"),
        "post_url_hash": h("https://instagram.com/p/ABC"),
        "post_image_sha256": h("post-image"),
    }
    result = client.anchor(address, input_phash=0xC3A5F0E1D2B48796,
                           similarity_bps=6123, evidence_uri="sha256:" + fields["record_hash"],
                           **fields)
    return fields, result


def test_anchoring_emits_an_event_carrying_the_record_hash(anchored):
    fields, result = anchored
    assert result["event"]["recordHash"] == fields["record_hash"]
    assert result["event"]["similarityBps"] == 6123
    assert result["block_number"] > 0


def test_an_honest_record_verifies_on_every_field(registry, anchored):
    client, address = registry
    fields, _ = anchored
    out = client.verify(address, **fields)
    assert out == {"ok": True, "found": True, "image_ok": True,
                   "face_ok": True, "post_ok": True, "post_image_ok": True}


def test_the_event_log_is_an_independent_source_of_truth(registry, anchored):
    client, address = registry
    fields, _ = anchored
    event = client.find_event(address, fields["record_hash"])
    assert event and event["faceCommitment"] == fields["face_commitment"]


def test_a_tampered_record_hash_is_simply_not_on_chain(registry, anchored):
    client, address = registry
    fields, _ = anchored
    out = client.verify(address, **{**fields, "record_hash": h("record-tampered")})
    assert out["found"] is False and out["ok"] is False


def test_a_tampered_image_is_caught_field_by_field(registry, anchored):
    """The record still exists, but the changed field is identified."""
    client, address = registry
    fields, _ = anchored
    out = client.verify(address, **{**fields, "input_image_sha256": h("other-image")})
    assert out["found"] is True
    assert out["image_ok"] is False
    assert out["face_ok"] is True and out["post_ok"] is True
    assert out["ok"] is False


def test_a_tampered_post_url_is_caught(registry, anchored):
    client, address = registry
    fields, _ = anchored
    out = client.verify(address, **{**fields, "post_url_hash": h("https://instagram.com/p/OTHER")})
    assert out["post_ok"] is False and out["ok"] is False


def test_records_are_immutable(registry, anchored):
    """Re-anchoring the same record hash must revert, not overwrite."""
    client, address = registry
    fields, _ = anchored
    with pytest.raises(Exception):
        client.anchor(address, input_phash=1, similarity_bps=1, evidence_uri="x", **fields)


def test_perceptual_hash_distance_is_computed_on_chain(registry):
    client, address = registry
    fn = client.contract(address).functions.hamming
    assert fn(0xC3A5F0E1D2B48796, 0xC3A5F0E1D2B48796).call() == 0
    assert fn(0xC3A5F0E1D2B48796, 0xC3A5F0E1D2B48797).call() == 1
    assert fn(0, 0xFFFFFFFFFFFFFFFF).call() == 64


def test_stored_record_keeps_the_submitter_and_evidence_uri(registry, anchored):
    client, address = registry
    fields, _ = anchored
    stored = client.get(address, fields["record_hash"])
    assert stored["submitter"] == client.sender
    assert stored["evidenceUri"].startswith("sha256:")
    assert stored["anchoredAt"] > 0
