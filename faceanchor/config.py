"""Configuration: .env loading, chain table, thresholds, run directories."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

EVIDENCE_ROOT = ROOT / "evidence" / "runs"
DEMO_EVIDENCE_ROOT = ROOT / "evidence" / "demo"
MODELS_DIR = ROOT / "models"
CONTRACTS_DIR = ROOT / "contracts"
DEPLOYMENTS_DIR = ROOT / "deployments"
CACHE_DIR = ROOT / ".cache" / "search"

# Face matching. Cosine similarity on L2-normalised ArcFace embeddings.
MATCH_THRESHOLD = float(os.getenv("MATCH_THRESHOLD", "0.40"))
WEAK_THRESHOLD = float(os.getenv("WEAK_THRESHOLD", "0.30"))
STRONG_THRESHOLD = 0.50
# OpenCV SFace is a different model with its own calibration.
SFACE_MATCH_THRESHOLD = 0.363
SFACE_WEAK_THRESHOLD = 0.28

FACE_ENGINE = os.getenv("FACE_ENGINE", "insightface")

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "").strip()
SEARCHAPI_KEY = os.getenv("SEARCHAPI_KEY", "").strip()
SERPER_KEY = os.getenv("SERPER_KEY", "").strip()
PINATA_JWT = os.getenv("PINATA_JWT", "").strip()
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "").strip()

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

SOCIAL_HOSTS = (
    "instagram.com", "x.com", "twitter.com", "facebook.com", "fb.com",
    "reddit.com", "redd.it", "tiktok.com", "youtube.com", "youtu.be",
    "linkedin.com", "threads.net", "threads.com", "pinterest.com",
)


@dataclass(frozen=True)
class Chain:
    name: str
    chain_id: int
    rpc_urls: tuple[str, ...] = ()
    explorer_tx: str = ""
    explorer_addr: str = ""
    is_local: bool = False

    def tx_url(self, tx_hash: str) -> str:
        return f"{self.explorer_tx}{tx_hash}" if self.explorer_tx else ""

    def addr_url(self, address: str) -> str:
        return f"{self.explorer_addr}{address}" if self.explorer_addr else ""


def _rpcs(*defaults: str) -> tuple[str, ...]:
    urls = [u for u in (os.getenv("RPC_URL", ""), os.getenv("RPC_URL_FALLBACK", "")) if u]
    return tuple(dict.fromkeys(urls + list(defaults)))


CHAINS: dict[str, Chain] = {
    "local": Chain(
        name="local",
        chain_id=131277322940537,  # eth-tester default
        is_local=True,
    ),
    "base-sepolia": Chain(
        name="base-sepolia",
        chain_id=84532,
        rpc_urls=_rpcs("https://sepolia.base.org", "https://base-sepolia-rpc.publicnode.com"),
        explorer_tx="https://sepolia.basescan.org/tx/",
        explorer_addr="https://sepolia.basescan.org/address/",
    ),
    "sepolia": Chain(
        name="sepolia",
        chain_id=11155111,
        rpc_urls=_rpcs("https://ethereum-sepolia-rpc.publicnode.com", "https://rpc.sepolia.org"),
        explorer_tx="https://sepolia.etherscan.io/tx/",
        explorer_addr="https://sepolia.etherscan.io/address/",
    ),
}


def get_chain(name: str) -> Chain:
    if name not in CHAINS:
        raise SystemExit(f"unknown chain '{name}'. choose from: {', '.join(CHAINS)}")
    return CHAINS[name]


def run_dir(run_id: str, demo: bool = False) -> Path:
    d = (DEMO_EVIDENCE_ROOT if demo else EVIDENCE_ROOT) / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_run() -> str | None:
    if not EVIDENCE_ROOT.exists():
        return None
    runs = sorted((p.name for p in EVIDENCE_ROOT.iterdir() if p.is_dir()), reverse=True)
    return runs[0] if runs else None


def resolve_run(run_id: str | None) -> Path:
    rid = run_id or latest_run()
    if not rid:
        raise SystemExit("no runs found. start with: python -m faceanchor scan --image <file>")
    d = EVIDENCE_ROOT / rid
    if not d.exists():
        d = DEMO_EVIDENCE_ROOT / rid
    if not d.exists():
        raise SystemExit(f"run '{rid}' not found under evidence/")
    return d


# Exit codes (documented in the README; used by the demo and by CI).
EXIT_OK = 0
EXIT_NO_MATCH = 2       # search found nothing above threshold, or verify mismatched
EXIT_NO_FACE = 3
EXIT_CHAIN = 4
EXIT_PROVIDER = 5
