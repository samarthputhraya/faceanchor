"""Canonical JSON, hashing and run-id helpers.

The *bytes* produced by `canonical_bytes` are the single source of truth for the
on-chain record hash.  They are written to disk verbatim as ``record.json`` so a
third party can reproduce the hash with ``sha256sum record.json``.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "faceanchor.record/v1"
FLOAT_PLACES = 4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime | None = None) -> str:
    """ISO-8601 UTC, second precision, trailing Z."""
    return (ts or utc_now()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def _normalise(obj: Any) -> Any:
    """Round floats, drop nulls, recurse.  Keys are sorted at dump time."""
    if isinstance(obj, float):
        return round(obj, FLOAT_PLACES)
    if isinstance(obj, dict):
        return {k: _normalise(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, (list, tuple)):
        return [_normalise(v) for v in obj]
    return obj


def canonical_bytes(obj: Any) -> bytes:
    """Deterministic UTF-8 encoding of ``obj``.

    sort_keys + compact separators + ensure_ascii=False, floats rounded to 4dp,
    null values removed.  Re-encoding the same logical object always yields the
    same bytes, on any platform.
    """
    return json.dumps(
        _normalise(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def record_hash(record: dict) -> str:
    """The value anchored on-chain: sha256 of the canonical record bytes."""
    return sha256_bytes(canonical_bytes(record))


def write_canonical(path: str | Path, obj: Any) -> tuple[bytes, str]:
    """Write canonical bytes to ``path``; return (bytes, sha256 hex)."""
    data = canonical_bytes(obj)
    Path(path).write_bytes(data)
    return data, sha256_bytes(data)


def phash_hex(image_path: str | Path) -> str:
    """64-bit perceptual hash as 16 hex chars."""
    import imagehash
    from PIL import Image

    with Image.open(image_path) as im:
        return str(imagehash.phash(im.convert("RGB")))


def phash_uint64(hex16: str) -> int:
    return int(hex16, 16)


def hamming64(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, obj: Any, indent: int = 2) -> None:
    """Human-readable JSON for non-hashed artifacts (candidates, anchor, ...)."""
    Path(path).write_text(
        json.dumps(obj, indent=indent, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
