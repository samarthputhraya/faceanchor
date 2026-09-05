"""Face engine interface + image preparation shared by both implementations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence

import cv2
import numpy as np

MIN_SHORT_SIDE = 320     # upscale small search thumbnails before detection
MAX_LONG_SIDE = 2000     # downscale huge photos for speed


@dataclass
class Face:
    bbox: tuple[float, float, float, float]   # x1, y1, x2, y2 in image coords
    det_score: float
    kps: list[list[float]] = field(default_factory=list)   # 5 landmarks
    embedding: np.ndarray | None = None                    # L2-normalised

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)

    def as_dict(self) -> dict:
        return {
            "bbox": [round(float(v), 2) for v in self.bbox],
            "det_score": round(float(self.det_score), 4),
        }


class FaceEngine(Protocol):
    name: str
    match_threshold: float
    weak_threshold: float
    embedding_dim: int

    def model_hashes(self) -> dict[str, str]: ...
    def detect_and_embed(self, image_bgr: np.ndarray) -> list[Face]: ...


def load_image(path: str | Path) -> np.ndarray:
    """Read an image as BGR, tolerating unicode paths (cv2.imread cannot)."""
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"could not decode image: {path}")
    return img


def decode_image(data: bytes) -> np.ndarray | None:
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def prepare(image_bgr: np.ndarray) -> tuple[np.ndarray, float]:
    """Rescale so faces land in the detector's sweet spot.

    Search-engine thumbnails are often 100-300 px, where faces are too small for
    SCRFD/YuNet; very large photos waste CPU.  Returns (image, scale) where
    scale maps prepared coords back to the original.
    """
    h, w = image_bgr.shape[:2]
    short, long_ = min(h, w), max(h, w)
    scale = 1.0
    if short < MIN_SHORT_SIDE:
        scale = min(4.0, MIN_SHORT_SIDE / short)
    elif long_ > MAX_LONG_SIDE:
        scale = MAX_LONG_SIDE / long_
    if abs(scale - 1.0) < 1e-6:
        return image_bgr, 1.0
    interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    return cv2.resize(image_bgr, (int(w * scale), int(h * scale)), interpolation=interp), scale


def rescale_faces(faces: Sequence[Face], scale: float) -> list[Face]:
    if abs(scale - 1.0) < 1e-6:
        return list(faces)
    out = []
    for f in faces:
        out.append(
            Face(
                bbox=tuple(v / scale for v in f.bbox),          # type: ignore[arg-type]
                det_score=f.det_score,
                kps=[[x / scale, y / scale] for x, y in f.kps],
                embedding=f.embedding,
            )
        )
    return out


def largest(faces: Sequence[Face]) -> Face | None:
    return max(faces, key=lambda f: f.area) if faces else None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return -1.0
    return float(np.dot(a / na, b / nb))


def crop(image_bgr: np.ndarray, face: Face, margin: float = 0.4) -> np.ndarray:
    """Face crop with margin — used as the reverse-image-search query image."""
    h, w = image_bgr.shape[:2]
    x1, y1, x2, y2 = face.bbox
    mw, mh = (x2 - x1) * margin, (y2 - y1) * margin
    x1, y1 = max(0, int(x1 - mw)), max(0, int(y1 - mh))
    x2, y2 = min(w, int(x2 + mw)), min(h, int(y2 + mh))
    return image_bgr[y1:y2, x1:x2]


def save_jpeg(image_bgr: np.ndarray, path: str | Path, quality: int = 92,
              max_side: int | None = None, max_bytes: int | None = None) -> Path:
    """Write JPEG, optionally shrinking to fit a pixel and/or byte budget."""
    img = image_bgr
    if max_side:
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            s = max_side / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    path = Path(path)
    q = quality
    while True:
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), q])
        if not ok:
            raise ValueError("jpeg encode failed")
        if not max_bytes or buf.nbytes <= max_bytes or q <= 40:
            path.write_bytes(buf.tobytes())
            return path
        q -= 10


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get_engine(name: str = "insightface"):
    """Factory. Falls back to SFace only when explicitly asked — never silently."""
    name = (name or "insightface").lower()
    if name in ("insightface", "buffalo_l", "arcface"):
        from .insight import InsightFaceEngine
        return InsightFaceEngine()
    if name in ("sface", "opencv", "yunet"):
        from .sface import SFaceEngine
        return SFaceEngine()
    raise SystemExit(f"unknown face engine '{name}' (use insightface | sface)")
