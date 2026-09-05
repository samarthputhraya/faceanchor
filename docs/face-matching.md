# Face matching

| | |
| --- | --- |
| Detector | SCRFD-10GF (`det_10g.onnx`) |
| Encoder | ArcFace `w600k_r50`, 512-d, L2-normalised |
| Metric | cosine similarity |
| Decision | match at 0.40, strong at 0.50, weak band 0.30 to 0.40 |
| Fallback engine | OpenCV YuNet + SFace, 128-d, threshold 0.363 (`--engine sface`) |

Measured on this machine with Wikimedia portraits, first load 28 s then about
1 s per image on CPU:

| pair | cosine | expected |
| --- | --- | --- |
| Pichai, two different photographs | 0.759 | same person |
| Pichai, two crops of one photograph | 0.993 | same person |
| Kohli, two different photographs | 0.625 | same person |
| Pichai vs Nadella | −0.038 | different |
| Pichai vs Musk | −0.059 | different |
| Musk vs Altman | 0.069 | different |
| Pichai vs Kohli | 0.071 | different |

Search-engine thumbnails are small, so a true match from a thumbnail often
lands between 0.40 and 0.55 rather than higher. When the full-size image can be
fetched from the post, the score is recomputed on it and the record states
which image the final number came from.
