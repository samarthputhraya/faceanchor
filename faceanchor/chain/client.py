"""One chain client for the in-process EVM and for public testnets.

`--chain local` runs a real EVM (py-evm via eth-tester) inside the process, so
the pipeline is demonstrable with no network, no faucet and no key.  The same
contract, the same ABI and the same call sites are used on Base Sepolia, so the
fallback is a real chain rather than a simulation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from web3 import Web3

from .. import config
from ..canonical import iso
from .contract import load_artifact

RECEIPT_TIMEOUT = 240

# The in-process EVM lives in memory, so every ChainClient("local") must share
# one instance. Without this, anchor() deploys onto a chain that verify() never
# sees, and the record vanishes between stages.
_LOCAL_W3 = None
_LOCAL_DEPLOYMENT: dict | None = None


def _local_web3():
    global _LOCAL_W3
    if _LOCAL_W3 is None:
        from web3 import EthereumTesterProvider

        _LOCAL_W3 = Web3(EthereumTesterProvider())
    return _LOCAL_W3


class ChainError(RuntimeError):
    pass


class ChainClient:
    def __init__(self, chain_name: str, private_key: str | None = None):
        self.chain = config.get_chain(chain_name)
        self.artifact = load_artifact()
        self.rpc_url = ""
        self.account = None
        self._key = (private_key if private_key is not None else config.PRIVATE_KEY) or ""

        if self.chain.is_local:
            self.w3 = _local_web3()
            self.sender = self.w3.eth.accounts[0]
            self.rpc_url = "in-process (eth-tester / py-evm)"
        else:
            self.w3 = self._connect()
            if not self._key:
                raise ChainError(
                    "PRIVATE_KEY is not set, so nothing can be signed for "
                    f"{self.chain.name}. Generate a burner key with: "
                    "python -m faceanchor newkey"
                )
            self.account = self.w3.eth.account.from_key(self._key)
            self.sender = self.account.address

    # --- connection -------------------------------------------------------------

    def _connect(self) -> Web3:
        errors = []
        for url in self.chain.rpc_urls:
            try:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 45}))
                if w3.is_connected() and w3.eth.chain_id == self.chain.chain_id:
                    self.rpc_url = url
                    return w3
                errors.append(f"{url}: wrong chain id or not connected")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url}: {type(exc).__name__} {exc}")
        raise ChainError(
            f"no working RPC for {self.chain.name}:\n  " + "\n  ".join(errors)
        )

    # --- basics -----------------------------------------------------------------

    @property
    def balance_eth(self) -> float:
        return float(self.w3.from_wei(self.w3.eth.get_balance(self.sender), "ether"))

    def contract(self, address: str):
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(address), abi=self.artifact["abi"]
        )

    def _send(self, fn) -> dict:
        """Send a contract call, local (unlocked) or remote (signed), and wait."""
        if self.chain.is_local:
            tx_hash = fn.transact({"from": self.sender})
        else:
            tx: dict[str, Any] = {
                "from": self.sender,
                "nonce": self.w3.eth.get_transaction_count(self.sender),
                "chainId": self.chain.chain_id,
            }
            try:
                tx["gas"] = int(fn.estimate_gas({"from": self.sender}) * 1.25)
            except Exception:  # noqa: BLE001 - estimation can fail on flaky RPCs
                tx["gas"] = 600_000
            base = self.w3.eth.get_block("latest").get("baseFeePerGas") or 0
            tip = self.w3.to_wei(0.01, "gwei")
            try:
                tip = max(tip, self.w3.eth.max_priority_fee)
            except Exception:  # noqa: BLE001
                pass
            tx["maxPriorityFeePerGas"] = int(tip)
            tx["maxFeePerGas"] = int(base * 2 + tip)
            built = fn.build_transaction(tx)
            signed = self.w3.eth.account.sign_transaction(built, self._key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)

        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_TIMEOUT)
        if receipt.get("status") != 1:
            raise ChainError(f"transaction reverted: {Web3.to_hex(tx_hash)}")
        return dict(receipt)

    # --- deploy -----------------------------------------------------------------

    def deploy(self) -> dict:
        factory = self.w3.eth.contract(
            abi=self.artifact["abi"], bytecode=self.artifact["bytecode"]
        )
        receipt = self._send(factory.constructor())
        address = receipt["contractAddress"]
        tx_hash = Web3.to_hex(receipt["transactionHash"])
        info = {
            "chain": self.chain.name,
            "chain_id": self.chain.chain_id,
            "contract": address,
            "deploy_tx": tx_hash,
            "deploy_block": receipt["blockNumber"],
            "solc_version": self.artifact["solcVersion"],
            "deployed_at": iso(),
            "explorer_address": self.chain.addr_url(address),
            "explorer_tx": self.chain.tx_url(tx_hash),
        }
        if self.chain.is_local:
            global _LOCAL_DEPLOYMENT
            _LOCAL_DEPLOYMENT = info
        else:
            self.save_deployment(info)
        return info

    def save_deployment(self, info: dict) -> Path:
        config.DEPLOYMENTS_DIR.mkdir(parents=True, exist_ok=True)
        p = config.DEPLOYMENTS_DIR / f"{self.chain.name}.json"
        p.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        return p

    def load_deployment(self) -> dict | None:
        if self.chain.is_local:
            # Memory chains cannot outlive the process, so a run against
            # --chain local must happen in one command (`run`, or the
            # dashboard). Separate stage invocations should use a public chain.
            return _LOCAL_DEPLOYMENT
        if config.CONTRACT_ADDRESS:
            return {
                "contract": config.CONTRACT_ADDRESS,
                "chain": self.chain.name,
                "chain_id": self.chain.chain_id,
                "deploy_block": 0,
            }
        p = config.DEPLOYMENTS_DIR / f"{self.chain.name}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    # --- registry operations ----------------------------------------------------

    def anchor(self, address: str, *, record_hash: str, input_image_sha256: str,
               face_commitment: str, post_url_hash: str, post_image_sha256: str,
               input_phash: int, similarity_bps: int, evidence_uri: str) -> dict:
        c = self.contract(address)
        fn = c.functions.anchor(
            _b32(record_hash), _b32(input_image_sha256), _b32(face_commitment),
            _b32(post_url_hash), _b32(post_image_sha256),
            int(input_phash) & 0xFFFFFFFFFFFFFFFF, int(similarity_bps) & 0xFFFF,
            evidence_uri,
        )
        receipt = self._send(fn)
        tx_hash = Web3.to_hex(receipt["transactionHash"])
        logs = c.events.Anchored().process_receipt(receipt)
        ts = self._block_timestamp(receipt["blockNumber"])
        return {
            "chain": self.chain.name,
            "chain_id": self.chain.chain_id,
            "rpc": self.rpc_url,
            "contract": address,
            "tx_hash": tx_hash,
            "block_number": receipt["blockNumber"],
            "block_timestamp": ts or None,
            "block_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None,
            "gas_used": receipt.get("gasUsed"),
            "submitter": self.sender,
            "explorer_tx": self.chain.tx_url(tx_hash),
            "explorer_address": self.chain.addr_url(address),
            "event": _event_to_dict(logs[0]["args"]) if logs else {},
            "anchored_at_local": iso(),
        }

    def _block_timestamp(self, block_number: int, attempts: int = 4) -> int:
        """Read a block's timestamp, tolerating a lagging RPC node.

        Public endpoints sit behind load balancers, so the node that returns a
        receipt is not always the node that has the block yet. The transaction
        is already mined at this point, so a missing timestamp is cosmetic and
        must never fail the anchor.
        """
        for i in range(attempts):
            try:
                return int(self.w3.eth.get_block(block_number)["timestamp"])
            except Exception:  # noqa: BLE001 - includes BlockNotFound
                if i < attempts - 1:
                    time.sleep(1.5 * (i + 1))
        return 0

    def get(self, address: str, record_hash: str) -> dict:
        r = self.contract(address).functions.get(_b32(record_hash)).call()
        keys = ("inputImageSha256", "faceCommitment", "postUrlHash", "postImageSha256",
                "inputPHash", "similarityBps", "anchoredAt", "submitter", "evidenceUri")
        out: dict[str, Any] = {}
        for k, v in zip(keys, r):
            out[k] = v.hex() if isinstance(v, (bytes, bytearray)) else v
        return out

    def exists(self, address: str, record_hash: str) -> bool:
        return bool(self.contract(address).functions.exists(_b32(record_hash)).call())

    def verify(self, address: str, *, record_hash: str, input_image_sha256: str,
               face_commitment: str, post_url_hash: str, post_image_sha256: str) -> dict:
        ok, found, image_ok, face_ok, post_ok, post_image_ok = (
            self.contract(address).functions.verify(
                _b32(record_hash), _b32(input_image_sha256), _b32(face_commitment),
                _b32(post_url_hash), _b32(post_image_sha256),
            ).call()
        )
        return {"ok": ok, "found": found, "image_ok": image_ok, "face_ok": face_ok,
                "post_ok": post_ok, "post_image_ok": post_image_ok}

    def find_event(self, address: str, record_hash: str, from_block: int = 0) -> dict | None:
        """Independent confirmation: read the emitted log, not just storage."""
        c = self.contract(address)
        try:
            logs = c.events.Anchored().get_logs(
                from_block=from_block,
                argument_filters={"recordHash": _b32(record_hash)},
            )
        except Exception:  # noqa: BLE001 - some public RPCs limit log ranges
            return None
        if not logs:
            return None
        log = logs[-1]
        out = _event_to_dict(log["args"])
        out["tx_hash"] = Web3.to_hex(log["transactionHash"])
        out["block_number"] = log["blockNumber"]
        return out


def _b32(hexstr: str) -> bytes:
    h = (hexstr or "").removeprefix("0x")
    if len(h) != 64:
        raise ValueError(f"expected a 32-byte hex string, got {len(h) // 2} bytes")
    return bytes.fromhex(h)


def _event_to_dict(args) -> dict:
    out = {}
    for k, v in dict(args).items():
        out[k] = v.hex() if isinstance(v, (bytes, bytearray)) else v
    return out
