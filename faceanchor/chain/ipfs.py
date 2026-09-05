"""Optional: pin the evidence record to IPFS so the CID can go on-chain."""

from __future__ import annotations

from pathlib import Path

import requests

from .. import config


def pin_json(path: str | Path) -> str:
    if not config.PINATA_JWT:
        raise RuntimeError("PINATA_JWT not set")
    p = Path(path)
    with open(p, "rb") as fh:
        r = requests.post(
            "https://uploads.pinata.cloud/v3/files",
            headers={"Authorization": f"Bearer {config.PINATA_JWT}"},
            files={"file": (p.name, fh, "application/json")},
            data={"network": "public", "name": f"faceanchor-{p.stem}"},
            timeout=90,
        )
    r.raise_for_status()
    cid = ((r.json() or {}).get("data") or {}).get("cid")
    if not cid:
        raise RuntimeError(f"no cid in Pinata response: {r.text[:200]}")
    return cid
