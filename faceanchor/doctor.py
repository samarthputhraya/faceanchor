"""Preflight checks.

Run this before a demo. Every check answers one question: will the pipeline
still work in a minute, on this machine, on this network, with this image?
Nothing here spends search quota or sends a transaction.
"""

from __future__ import annotations

import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""
    fix: str = ""


def _probe(host: str, port: int = 443, timeout: float = 6.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_python() -> Check:
    v = sys.version_info
    if v >= (3, 12):
        return Check("python", OK, f"{v.major}.{v.minor}.{v.micro}")
    return Check("python", FAIL, f"{v.major}.{v.minor}", "3.12 or newer is required")


def check_imports() -> list[Check]:
    out = []
    for mod, why, hard in (
        ("cv2", "image decoding and the fallback engine", True),
        ("insightface", "the primary face engine", True),
        ("onnxruntime", "model inference", True),
        ("web3", "the chain client", True),
        ("imagehash", "perceptual hashing", True),
        ("playwright", "the browser extraction tier", False),
    ):
        try:
            __import__(mod)
            out.append(Check(f"import {mod}", OK, why))
        except ImportError as exc:
            out.append(Check(
                f"import {mod}", FAIL if hard else WARN, str(exc)[:60],
                "pip install -r requirements.txt"))
    return out


def check_models() -> list[Check]:
    from .face.insight import MODEL_DIR

    out = []
    missing = [f for f in ("det_10g.onnx", "w600k_r50.onnx")
               if not (MODEL_DIR / f).exists()]
    if missing:
        out.append(Check("face models", WARN, f"missing {', '.join(missing)}",
                         "they download automatically on the first scan (288 MB)"))
    else:
        size = sum((MODEL_DIR / f).stat().st_size
                   for f in ("det_10g.onnx", "w600k_r50.onnx")) // (1024 * 1024)
        out.append(Check("face models", OK, f"buffalo_l present, {size} MB"))

    from .face.sface import DETECTOR, RECOGNISER

    if DETECTOR.exists() and RECOGNISER.exists():
        out.append(Check("fallback models", OK, "YuNet and SFace present"))
    else:
        out.append(Check("fallback models", WARN, "not downloaded",
                         "python -m faceanchor fetch-models"))
    return out


def check_keys() -> list[Check]:
    out = []
    if config.SERPAPI_KEY:
        from .search import serpapi

        q = serpapi.quota()
        left = q.get("searches_left")
        if q.get("error"):
            out.append(Check("serpapi", FAIL, str(q["error"])[:60],
                             "check the key and the network"))
        elif left is None:
            out.append(Check("serpapi", WARN, "quota unreadable"))
        elif int(left) < 10:
            out.append(Check("serpapi", WARN, f"only {left} searches left",
                             "each run costs 2 to 5; top up or use SEARCHAPI_KEY"))
        else:
            out.append(Check("serpapi", OK, f"{left} searches left"))
    else:
        out.append(Check("serpapi", FAIL, "SERPAPI_KEY not set",
                         "put it in .env; the search stage cannot run without it"))

    for name, key in (("searchapi fallback", config.SEARCHAPI_KEY),
                      ("serper fallback", config.SERPER_KEY)):
        out.append(Check(name, OK if key else WARN,
                         "configured" if key else "not set",
                         "" if key else "optional, but it is the safety net"))
    return out


def check_network() -> list[Check]:
    out = []
    for host, label, needed in (
        ("serpapi.com", "serpapi.com", True),
        ("encrypted-tbn0.gstatic.com", "google thumbnails", True),
        ("sepolia.base.org", "base sepolia rpc", True),
        ("www.instagram.com", "instagram", False),
        ("x.com", "x", False),
    ):
        reachable = _probe(host)
        if reachable:
            out.append(Check(label, OK, "reachable"))
        else:
            out.append(Check(
                label, FAIL if needed else WARN, "unreachable",
                "" if needed else
                "blocked by this network; extraction will fall back to the "
                "search thumbnail. Use a phone hotspot for the full result."))
    return out


def check_chain(chain_name: str = "base-sepolia") -> list[Check]:
    out = []
    if not config.PRIVATE_KEY:
        return [Check("wallet", WARN, "PRIVATE_KEY not set",
                      "only --chain local will work; run: python -m faceanchor newkey")]
    try:
        from .chain.client import ChainClient

        c = ChainClient(chain_name)
        bal = c.balance_eth
        # A deploy is roughly 0.000008 ETH and a record roughly 0.000003 at
        # Base Sepolia gas, so this is many runs' worth.
        if bal <= 0:
            out.append(Check("wallet balance", FAIL, f"{bal} ETH on {chain_name}",
                             "fund it at https://portal.cdp.coinbase.com/products/faucet"))
        elif bal < 0.00005:
            out.append(Check("wallet balance", WARN, f"{bal} ETH",
                             "enough for a few records; claim from the faucet again"))
        else:
            out.append(Check("wallet balance", OK, f"{bal} ETH on {chain_name}"))
        out.append(Check("rpc", OK, c.rpc_url))

        dep = c.load_deployment()
        if dep and dep.get("contract"):
            code = c.w3.eth.get_code(c.w3.to_checksum_address(dep["contract"]))
            if len(code) > 2:
                out.append(Check("registry contract", OK, dep["contract"]))
            else:
                out.append(Check("registry contract", FAIL,
                                 f"no code at {dep['contract']}",
                                 f"python -m faceanchor deploy --chain {chain_name}"))
        else:
            out.append(Check("registry contract", WARN, "not deployed yet",
                             f"python -m faceanchor deploy --chain {chain_name}"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("chain", FAIL, f"{type(exc).__name__}: {exc}"[:90],
                         "check RPC_URL and PRIVATE_KEY in .env"))
    return out


def check_image(path: str | Path) -> list[Check]:
    """The single most common cause of a disappointing run."""
    from .face.engine import get_engine, largest, load_image

    p = Path(path)
    if not p.exists():
        return [Check("input image", FAIL, f"{p} not found")]

    img = load_image(p)
    h, w = img.shape[:2]
    out = [Check("input image", OK if min(h, w) >= 400 else WARN, f"{w} x {h} px",
                 "" if min(h, w) >= 400 else
                 "small images give the search engine very little to work with; "
                 "open the original rather than a search thumbnail")]

    engine = get_engine(config.FACE_ENGINE)
    engine.load()
    faces = engine.detect_and_embed(img)
    if not faces:
        out.append(Check("face detection", FAIL, "no face found",
                         "use a photograph where the face is clearly visible"))
        return out

    face = largest(faces)
    fw = face.bbox[2] - face.bbox[0]
    if fw >= 150:
        state, fix = OK, ""
    elif fw >= 80:
        state, fix = WARN, "usable, but a larger face searches much better"
    else:
        state, fix = WARN, ("the crop will be upscaled before searching and the "
                            "whole image retried, but expect weaker results")
    out.append(Check("face size", state,
                     f"{int(fw)} px wide, {100 * fw / w:.0f}% of the image, "
                     f"confidence {face.det_score:.2f}", fix))
    if len(faces) > 1:
        out.append(Check("faces in frame", WARN, f"{len(faces)} found",
                         "the largest is used; a single-subject photo is clearer"))
    return out


def check_disk() -> Check:
    free = shutil.disk_usage(config.ROOT).free // (1024 * 1024)
    return Check("disk space", OK if free > 500 else WARN, f"{free} MB free")


def check_zk(chain: str = "base-sepolia") -> list[Check]:
    """The proving toolchain, and whether a proof-gated registry is deployed.

    Every one of these is a WARN, never a FAIL: without the toolchain the
    pipeline still runs end to end and anchors to v1. Only the proof is lost.
    """
    from .zk import prover

    out: list[Check] = []
    ok, why = prover.available()
    out.append(Check(
        "zk toolchain",
        OK if ok else WARN,
        "ready to prove" if ok else why,
        "" if ok else "powershell -ExecutionPolicy Bypass -File zk/build.ps1",
    ))

    try:
        from .chain.client import ChainClient

        c = ChainClient(chain, registry="v2")
        dep = c.load_deployment()
        if dep:
            out.append(Check("v2 registry", OK,
                             f"{dep['contract']}  verifier {dep.get('verifier', '?')}"))
        else:
            out.append(Check("v2 registry", WARN, f"not deployed on {chain}",
                             f"python -m faceanchor deploy --chain {chain} --registry v2"))
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        out.append(Check("v2 registry", WARN, f"{type(exc).__name__}: {exc}"))
    return out


def run_all(chain: str = "base-sepolia", image: str = "") -> list[Check]:
    checks = [check_python(), *check_imports(), *check_models(), *check_keys(),
              *check_network(), *check_chain(chain), *check_zk(chain), check_disk()]
    if image:
        checks.extend(check_image(image))
    return checks
