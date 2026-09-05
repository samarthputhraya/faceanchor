"""OpenCV fallback engine: YuNet detector + SFace recogniser (128-d).

Needs no packages beyond opencv-python and two small ONNX files from opencv_zoo.
Official thresholds (opencv_zoo/models/face_recognition_sface/sface.py):
cosine >= 0.363 means "same person".
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .. import config
from .engine import Face, file_sha256, prepare, rescale_faces

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
DETECTOR_URL = f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx"
RECOGNISER_URL = f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx"
DETECTOR = config.MODELS_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNISER = config.MODELS_DIR / "face_recognition_sface_2021dec.onnx"


def download_models(force: bool = False) -> dict[str, str]:
    """Fetch the two ONNX files; return {filename: sha256}."""
    import requests

    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for url, dest in ((DETECTOR_URL, DETECTOR), (RECOGNISER_URL, RECOGNISER)):
        if force or not dest.exists():
            r = requests.get(url, timeout=180)
            r.raise_for_status()
            dest.write_bytes(r.content)
        out[dest.name] = file_sha256(dest)
    return out


@lru_cache(maxsize=1)
def _models():
    if not DETECTOR.exists() or not RECOGNISER.exists():
        download_models()
    det = cv2.FaceDetectorYN.create(
        model=str(DETECTOR), config="", input_size=(320, 320),
        score_threshold=0.7, nms_threshold=0.3, top_k=5000,
    )
    rec = cv2.FaceRecognizerSF.create(model=str(RECOGNISER), config="")
    return det, rec


class SFaceEngine:
    name = "opencv/yunet+sface"
    model_id = "YuNet 2023mar + SFace 2021dec"
    embedding_dim = 128
    match_threshold = config.SFACE_MATCH_THRESHOLD
    weak_threshold = config.SFACE_WEAK_THRESHOLD
    strong_threshold = 0.5

    def load(self) -> None:
        _models()

    def model_hashes(self) -> dict[str, str]:
        return {p.name: file_sha256(p) for p in (DETECTOR, RECOGNISER) if p.exists()}

    def detect_and_embed(self, image_bgr: np.ndarray) -> list[Face]:
        det, rec = _models()
        img, scale = prepare(image_bgr)
        h, w = img.shape[:2]
        det.setInputSize((w, h))          # mandatory: detect() throws otherwise
        _, raw = det.detect(img)
        if raw is None:
            return []
        faces: list[Face] = []
        for row in raw:
            x, y, bw, bh = row[0:4]
            aligned = rec.alignCrop(img, row)
            feat = rec.feature(aligned)
            emb = np.asarray(feat, dtype=np.float32).ravel()
            faces.append(
                Face(
                    bbox=(float(x), float(y), float(x + bw), float(y + bh)),
                    det_score=float(row[14]),
                    kps=[[float(row[i]), float(row[i + 1])] for i in range(4, 14, 2)],
                    embedding=emb,
                )
            )
        return rescale_faces(faces, scale)
