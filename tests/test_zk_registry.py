"""The v2 registry against a real EVM, with a real proof.

These tests need the zk toolchain (node + a built proving key), so they skip
rather than fail on a machine that has not run zk/build.ps1 -- CI installs no
Node and downloads no 9 MB zkey. The contract logic that does not need a proof
is tested unconditionally.
"""

from __future__ import annotations

import json

import pytest
from web3 import Web3

from faceanchor import config
from faceanchor.chain import contract as contract_mod

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

DOT_OFFSET = 8388608


def _w3():
    from web3 import EthereumTesterProvider

    return Web3(EthereumTesterProvider())


def _deploy(w3, contract_def, *args):
    art = contract_mod.load_artifact(contract_def)
    factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    tx = factory.constructor(*args).transact({"from": w3.eth.accounts[0], "gas": 8_000_000})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    return w3.eth.contract(address=receipt.contractAddress, abi=art["abi"])


@pytest.fixture(scope="module")
def registry():
    w3 = _w3()
    verifier = _deploy(w3, contract_mod.VERIFIER)
    reg = _deploy(w3, contract_mod.REGISTRY_V2, verifier.address)
    return w3, verifier, reg


def _proved_run():
    """A committed or local run that has both a proof and a record."""
    for root in (config.EVIDENCE_ROOT, config.DEMO_EVIDENCE_ROOT):
        if not root.exists():
            continue
        for d in sorted(root.iterdir(), reverse=True):
            if (d / "zk.json").exists() and (d / "zk_proof.json").exists():
                return d
    return None


def _can_prove():
    """A proved run is not enough: reading one back needs snarkjs too.

    The demo bundle carries a proof, so gating only on `_proved_run()` made
    these run in CI, where Node is installed but zk/node_modules is not -- they
    failed on a missing snarkjs rather than skipping.
    """
    if _proved_run() is None:
        return False
    from faceanchor.zk import prover

    return prover.available()[0]


_NEEDS_TOOLCHAIN = pytest.mark.skipif(
    not _can_prove(), reason="needs a proved run and the zk toolchain (zk/build.ps1)")


def test_verifier_and_registry_compile_and_deploy(registry):
    _, verifier, reg = registry
    assert Web3.is_address(verifier.address)
    assert reg.functions.verifier().call() == verifier.address
    assert reg.functions.count().call() == 0


def test_constants_match_the_circuit(registry):
    """DOT_OFFSET must equal D*128*128 from facematch.circom, or every
    similarity the contract derives is silently wrong."""
    _, _, reg = registry
    from faceanchor.zk import prover

    assert reg.functions.DOT_OFFSET().call() == DOT_OFFSET
    assert prover.OFFSET == DOT_OFFSET
    assert prover.DIM * 128 * 128 == DOT_OFFSET


def test_similarity_bps_is_derived_not_asserted():
    from faceanchor.zk import prover

    # A pair at exactly cosine 1.0 and one at 0.
    assert prover.similarity_bps(100, 100, 100) == 10000
    assert prover.similarity_bps(0, 100, 100) == 0
    # Half of the geometric mean of the norms is 0.5.
    assert prover.similarity_bps(50, 100, 100) == 5000
    # Negative dot products can never look like a match.
    assert prover.similarity_bps(-50, 100, 100) == 0


def test_salt_must_fit_the_scalar_field():
    """A 32-byte salt overflows BN254 and would wrap silently."""
    from faceanchor.zk import prover

    assert len(bytes.fromhex(prover.new_salt())) == prover.SALT_BYTES == 31


def test_anchor_without_a_valid_proof_is_refused(registry):
    """Random proof bytes must not produce a stored record."""
    w3, _, reg = registry
    claim = (
        bytes.fromhex("aa" * 32), bytes.fromhex("bb" * 32), bytes.fromhex("cc" * 32),
        bytes.fromhex("dd" * 32), bytes.fromhex("ee" * 32),
        1234, 9000, "sha256:nope",
    )
    junk = ([1, 2], [[1, 2], [3, 4]], [5, 6], [1, 2, DOT_OFFSET + 100, 100, 100])
    with pytest.raises(Exception):
        reg.functions.anchor(claim, junk).call({"from": w3.eth.accounts[0]})
    assert reg.functions.count().call() == 0


@_NEEDS_TOOLCHAIN
def test_real_proof_is_accepted_and_a_forged_similarity_is_not(registry):
    """The claim that carries the whole project: the chain accepts the proven
    similarity and refuses anything above it, down to one basis point."""
    w3, verifier, reg = registry
    from faceanchor.zk import prover

    d = _proved_run()
    zk = json.loads((d / "zk.json").read_text(encoding="utf-8"))
    cd = prover.solidity_calldata(d)
    proof = (cd["a"], cd["b"], cd["c"], cd["public_signals"])

    assert verifier.functions.verifyProof(*proof).call() is True

    honest = zk["similarity_bps"]

    def claim(tag, bps):
        return (
            bytes.fromhex(tag * 32), bytes.fromhex("bb" * 32), bytes.fromhex("cc" * 32),
            bytes.fromhex("dd" * 32), bytes.fromhex("ee" * 32), 1234, bps, "sha256:t",
        )

    tx = reg.functions.anchor(claim("11", honest), proof).transact(
        {"from": w3.eth.accounts[0], "gas": 3_000_000})
    assert w3.eth.wait_for_transaction_receipt(tx).status == 1

    stored = reg.functions.get(bytes.fromhex("11" * 32)).call()
    assert stored[5] == honest                      # similarityBps
    assert stored[10] == zk["dot"]                  # dot
    assert stored[11] == zk["norm_a"]
    assert stored[12] == zk["norm_b"]

    for bad in (honest + 1, 9999, 10000):
        with pytest.raises(Exception):
            reg.functions.anchor(claim("22", bad), proof).call({"from": w3.eth.accounts[0]})


@_NEEDS_TOOLCHAIN
def test_tampering_a_public_signal_invalidates_the_proof(registry):
    _, verifier, _ = registry
    from faceanchor.zk import prover

    cd = prover.solidity_calldata(_proved_run())
    for i in range(5):
        bad = list(cd["public_signals"])
        bad[i] += 1
        assert verifier.functions.verifyProof(cd["a"], cd["b"], cd["c"], bad).call() is False


@pytest.mark.skipif(_proved_run() is None, reason="no proved run available")
def test_the_proof_matches_the_commitments_the_pipeline_published(registry):
    """A proof about two vectors nobody committed to would prove nothing."""
    d = _proved_run()
    zk = json.loads((d / "zk.json").read_text(encoding="utf-8"))
    face = json.loads((d / "face.json").read_text(encoding="utf-8"))
    post = json.loads((d / "post.json").read_text(encoding="utf-8"))

    assert face["zk_commitment"] == zk["commitment_a"]
    assert post["zk_commitment"] == zk["commitment_b"]
