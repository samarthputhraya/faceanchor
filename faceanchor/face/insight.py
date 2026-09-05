"""InsightFace buffalo_l engine: SCRFD-10GF detector + ArcFace w600k_r50 (512-d).

Pure-wheel since insightface 1.0 (no compiler needed).  Models auto-download to
%USERPROFILE%/.insightface/models/buffalo_l on first use (288 MB).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from .. import config
from .engine import Face, file_sha256, prepare, rescale_faces

MODEL_PACK = "buffalo_l"
MODEL_DIR = Path(os.path.expanduser("~")) / ".insightface" / "models" / MODEL_PACK


@lru_cache(maxsize=1)
def _app():
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        name=MODEL_PACK,
        providers=["CPUExecutionProvider"],
        allowed_modules=["detection", "recognition"],
    )
    # ctx_id<0 forces CPU; an explicit det_size avoids insightface 1.0's "Auto"
    # mode, which runs the detector twice (128 and 640) for double the cost.
    app.prepare(ctx_id=-1, det_thresh=0.5, det_size=(640, 640))
    return app


class InsightFaceEngine:
    name = "insightface/buffalo_l"
    model_id = "SCRFD-10GF + ArcFace w600k_r50"
    embedding_dim = 512
    match_threshold = config.MATCH_THRESHOLD
    weak_threshold = config.WEAK_THRESHOLD
    strong_threshold = config.STRONG_THRESHOLD

    def __init__(self) -> None:
        self._loaded = False

    def load(self) -> None:
        if not self._loaded:
            _app()
            self._loaded = True

    def model_hashes(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for fname in ("det_10g.onnx", "w600k_r50.onnx"):
            p = MODEL_DIR / fname
            if p.exists():
                out[fname] = file_sha256(p)
        return out

    def detect_and_embed(self, image_bgr: np.ndarray) -> list[Face]:
        app = _app()
        img, scale = prepare(image_bgr)
        faces: list[Face] = []
        for f in app.get(img):
            emb = np.asarray(f.normed_embedding, dtype=np.float32)
            faces.append(
                Face(
                    bbox=tuple(float(v) for v in f.bbox),   # type: ignore[arg-type]
                    det_score=float(f.det_score),
                    kps=[[float(x), float(y)] for x, y in np.asarray(f.kps)],
                    embedding=emb,
                )
            )
        return rescale_faces(faces, scale)
