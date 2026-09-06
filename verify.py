#!/usr/bin/env python3
"""Standalone verifier for a FaceAnchor evidence bundle.

Deliberately dependency-light so a third party can check a published run
without installing the pipeline:

    pip install web3==7.16.0
    python verify.py --record evidence/demo/<run_id>/record.json

It recomputes every hash from the files next to the record, then reads the
record back from the chain twice: through the contract's verify() view and
through the Anchored event log. Exit code 0 means verified, 2 means the bundle
no longer matches the chain, 4 means the chain could not be reached.

No API key, no face model and no private key are needed: verification only ever
reads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEPLOYMENTS = ROOT / "deployments"
ARTIFACT = ROOT / "contracts" / "build" / "FaceAnchorRegistry.json"
ARTIFACT_V2 = ROOT / "contracts" / "build" / "FaceAnchorRegistryV2.json"

EXIT_OK, EXIT_MISMATCH, EXIT_CHAIN = 0, 2, 4

RPCS = {
    "base-sepolia": ["https://sepolia.base.org", "https://base-sepolia-rpc.publicnode.com"],
    "sepolia": ["https://ethereum-sepolia-rpc.publicnode.com", "https://rpc.sepolia.org"],
}
EXPLORERS = {
    "base-sepolia": "https://sepolia.basescan.org/tx/",
    "sepolia": "https://sepolia.etherscan.io/tx/",
}

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


# --- the canonical form. This must match faceanchor/canonical.py exactly. ----------

def normalise(obj):
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, dict):
        return {k: normalise(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, (list, tuple)):
        return [normalise(v) for v in obj]
    return obj


def canonical_bytes(obj) -> bytes:
    return json.dumps(normalise(obj), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tamper(record: dict, field: str) -> dict:
    r = json.loads(json.dumps(record))
    if field == "caption":
        cur = r["post"].get("caption_excerpt") or ""
        r["post"]["caption_excerpt"] = (cur[:-1] + "!") if cur else "tampered"
    elif field == "post_url":
        r["post"]["url"] = r["post"]["url"].rstrip("/") + "x"
    elif field == "similarity":
        r["post"]["similarity"] = round(float(r["post"]["similarity"]) + 0.05, 4)
    elif field == "input_image":
        h = r["input"]["sha256"]
        r["input"]["sha256"] = h[:-1] + ("0" if h[-1] != "0" else "1")
    else:
        sys.exit("--tamper takes: caption | post_url | similarity | input_image")
    return r


def row(label: str, ok: bool, detail: str = "") -> bool:
    mark = f"{GREEN}PASS{OFF}" if ok else f"{RED}FAIL{OFF}"
    print(f"  {mark}  {label:<42} {DIM}{detail}{OFF}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify a FaceAnchor record against the chain.")
    ap.add_argument("--record", required=True, help="path to record.json")
    ap.add_argument("--chain", default="", help="override the chain name")
    ap.add_argument("--rpc", default="", help="override the RPC endpoint")
    ap.add_argument("--contract", default="", help="override the contract address")
    ap.add_argument("--tamper", default="", metavar="FIELD",
                    help="alter one field before verifying, to prove detection works")
    args = ap.parse_args()

    record_path = Path(args.record).resolve()
    if not record_path.exists():
        sys.exit(f"no such file: {record_path}")
    bundle = record_path.parent
    record = json.loads(record_path.read_text(encoding="utf-8"))

    chain = args.chain or (record.get("chain_intent") or {}).get("chain") or "base-sepolia"
    contract = args.contract or (record.get("chain_intent") or {}).get("contract", "")
    deploy_block = 0
    # A v2 record was anchored to a contract deployed much later than v1, and
    # scanning for its event from v1's deploy block is a ~26k block range that
    # public RPCs refuse outright.
    reg = (record.get("chain_intent") or {}).get("registry") or (
        "v2" if record.get("zk") else "v1")
    suffix = "" if reg == "v1" else f"-{reg}"
    dep_file = DEPLOYMENTS / f"{chain}{suffix}.json"
    if not dep_file.exists():
        dep_file = DEPLOYMENTS / f"{chain}.json"
    if dep_file.exists():
        dep = json.loads(dep_file.read_text(encoding="utf-8"))
        contract = contract or dep.get("contract", "")
        deploy_block = dep.get("deploy_block", 0)

    print(f"\n{BOLD}FaceAnchor verification{OFF}")
    print(f"  bundle    {bundle}")
    print(f"  chain     {chain}")
    print(f"  contract  {contract or '(unknown)'}")
    if args.tamper:
        print(f"  {RED}tampering with '{args.tamper}' before verifying{OFF}")
    print()

    checks: list[bool] = []

    print(f"{BOLD}files on disk{OFF}")
    if (bundle / "input.jpg").exists():
        actual = sha256_file(bundle / "input.jpg")
        checks.append(row("input image sha256", actual == record["input"]["sha256"],
                          actual[:32] + "..."))
    else:
        print(f"  {DIM}SKIP  input.jpg not in the bundle{OFF}")

    post_sha = (record.get("post") or {}).get("image_sha256")
    if post_sha and (bundle / "post_image.jpg").exists():
        actual = sha256_file(bundle / "post_image.jpg")
        checks.append(row("post image sha256", actual == post_sha, actual[:32] + "..."))

    secret = bundle / "face_secret.json"
    if secret.exists():
        s = json.loads(secret.read_text(encoding="utf-8"))
        q = bytes((v & 0xFF) for v in s["quantised_int8"])
        digest = hashlib.sha256(b"faceanchor-v1" + bytes.fromhex(s["salt"]) + q).hexdigest()
        checks.append(row("face commitment", digest == record["face"]["commitment"],
                          digest[:32] + "..."))
    else:
        print(f"  {DIM}SKIP  face_secret.json withheld, as it should be in a published bundle{OFF}")

    if args.tamper:
        record = tamper(record, args.tamper)

    local_hash = sha256_bytes(canonical_bytes(record))
    on_file = sha256_bytes(record_path.read_bytes())
    if not args.tamper:
        checks.append(row("record.json is canonical", local_hash == on_file,
                          "re-serialising gives the same bytes"))

    print(f"\n{BOLD}record hash{OFF}")
    print(f"  {local_hash}")

    print(f"\n{BOLD}on-chain{OFF}")
    try:
        from web3 import Web3
    except ImportError:
        sys.exit("web3 is required:  pip install web3==7.16.0")

    if not contract:
        print(f"  {RED}no contract address; pass --contract{OFF}")
        return EXIT_CHAIN

    urls = [args.rpc] if args.rpc else RPCS.get(chain, [])
    w3 = None
    for url in urls:
        try:
            candidate = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 45}))
            if candidate.is_connected():
                w3 = candidate
                print(f"  {DIM}rpc {url}{OFF}")
                break
        except Exception:  # noqa: BLE001
            continue
    if w3 is None:
        print(f"  {RED}could not reach any RPC for {chain}{OFF}")
        return EXIT_CHAIN

    # The two registries share verify() and the Anchored topic but differ in
    # get(): v2's Record carries the proven integers, so decoding a v2 record
    # through the v1 ABI dies with an opaque InvalidPointer. Pick by what the
    # record says it was anchored to, and fall back to v1 for older bundles.
    registry = (record.get("chain_intent") or {}).get("registry") or (
        "v2" if record.get("zk") else "v1")
    artifact = ARTIFACT_V2 if registry == "v2" else ARTIFACT
    if not artifact.exists():
        print(f"  {RED}missing {artifact.name}; run: python -m faceanchor deploy --help{OFF}")
        return EXIT_CHAIN
    abi = json.loads(artifact.read_text(encoding="utf-8"))["abi"]
    print(f"  {DIM}registry {registry}  ({artifact.name}){OFF}")
    c = w3.eth.contract(address=Web3.to_checksum_address(contract), abi=abi)
    b32 = lambda h: bytes.fromhex(h.removeprefix("0x"))  # noqa: E731

    post = record["post"]
    ok, found, image_ok, face_ok, post_ok, post_image_ok = c.functions.verify(
        b32(local_hash), b32(record["input"]["sha256"]), b32(record["face"]["commitment"]),
        b32(post["url_sha256"]), b32(post.get("image_sha256") or "00" * 32),
    ).call()

    checks.append(row("record exists on-chain", found))
    if found:
        for label, value in (("chain: input image", image_ok),
                             ("chain: face commitment", face_ok),
                             ("chain: post url", post_ok),
                             ("chain: post image", post_image_ok)):
            checks.append(row(label, value))
        stored = c.functions.get(b32(local_hash)).call()
        # Index by the ABI's own field order rather than a literal, because v2
        # inserts the proven integers ahead of evidenceUri.
        fields = [o["name"] for o in next(
            e for e in abi if e.get("name") == "get")["outputs"][0]["components"]]
        rec = dict(zip(fields, stored))
        print(f"  {DIM}anchored at block time {rec['anchoredAt']} by {rec['submitter']}{OFF}")
        print(f"  {DIM}evidence uri {rec['evidenceUri']}{OFF}")
        if registry == "v2":
            print(f"  {DIM}proven on-chain: similarity {rec['similarityBps']} bps  "
                  f"dot {rec['dot']}  normA {rec['normA']}  normB {rec['normB']}{OFF}")
        # Public RPCs reject eth_getLogs over roughly 10k blocks, and a record
        # can sit far above its deploy block, so walk backwards in windows from
        # the head rather than asking for the whole range at once.
        SPAN = 9000
        logs, err = [], None
        try:
            head = w3.eth.block_number
            low = max(0, deploy_block)
            hi = head
            while hi >= low and not logs:
                lo = max(low, hi - SPAN)
                try:
                    logs = c.events.Anchored().get_logs(
                        from_block=lo, to_block=hi,
                        argument_filters={"recordHash": b32(local_hash)})
                except Exception as exc:  # noqa: BLE001 - one bad window is survivable
                    err = exc
                if lo == low:
                    break
                hi = lo - 1
        except Exception as exc:  # noqa: BLE001
            err = exc
        if logs:
            checks.append(row("event log carries the same hash", True))
            tx = Web3.to_hex(logs[-1]["transactionHash"])
            print(f"  {DIM}tx {tx}{OFF}")
            if chain in EXPLORERS:
                print(f"  {EXPLORERS[chain]}{tx}")
        elif err is not None:
            print(f"  {DIM}SKIP  event lookup unavailable on this RPC "
                  f"({type(err).__name__}){OFF}")
        else:
            checks.append(row("event log carries the same hash", False))

    passed = all(checks) and ok
    print()
    if passed:
        print(f"{GREEN}{BOLD}  VERIFIED{OFF}  every hash recomputed from the files matches "
              f"the record anchored on-chain.\n")
        return EXIT_OK
    print(f"{RED}{BOLD}  MISMATCH{OFF}  this bundle does not match the on-chain record"
          + (f" after tampering with '{args.tamper}'." if args.tamper else ".") + "\n")
    return EXIT_MISMATCH


if __name__ == "__main__":
    sys.exit(main())
