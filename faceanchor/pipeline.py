"""Stage orchestration.

Each stage reads and writes only ``evidence/runs/<run_id>/``, so any stage can
be re-run on its own and the whole run is auditable from the folder alone.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

import numpy as np

from . import config
from .canonical import (
    SCHEMA, canonical_bytes, iso, new_run_id, phash_hex, phash_uint64, read_json,
    sha256_bytes, sha256_file, sha256_text, write_canonical, write_json,
)
from .events import StageEvent
from .extract import post as post_mod
from .face import fingerprint
from .face.engine import cosine, crop, get_engine, largest, load_image, save_jpeg
from .search import candidates as cand_mod
from .search import fallbacks, serpapi
from .search.base import canonical_url

Emit = Callable[[StageEvent], None]


def _noop(_: StageEvent) -> None:
    return None


# --- stage 1: scan -----------------------------------------------------------------

def scan(image_path: str | Path, engine_name: str = "", run_id: str = "",
         emit: Emit = _noop) -> dict:
    engine_name = engine_name or config.FACE_ENGINE
    run_id = run_id or new_run_id()
    d = config.run_dir(run_id)
    emit(StageEvent("stage_start", "scan", f"run {run_id}", {"run_id": run_id}))

    src = Path(image_path)
    if not src.exists():
        raise SystemExit(f"input image not found: {src}")
    dest = d / "input.jpg"
    img = load_image(src)
    save_jpeg(img, dest, quality=95)

    engine = get_engine(engine_name)
    engine.load()
    emit(StageEvent("log", "scan", f"engine {engine.name} ({engine.model_id})"))

    faces = engine.detect_and_embed(img)
    if not faces:
        emit(StageEvent("error", "scan", "no face detected in the input image"))
        raise SystemExit(config.EXIT_NO_FACE)
    face = largest(faces)
    if len(faces) > 1:
        emit(StageEvent("log", "scan",
                        f"{len(faces)} faces found; using the largest"))

    save_jpeg(crop(img, face, margin=0.4), d / "face_crop.jpg", quality=92)
    # The reverse-image query image: tight enough to be about the person,
    # small enough for the SerpApi 500 KB upload limit.
    save_jpeg(crop(img, face, margin=0.4), d / "query.jpg",
              quality=92, max_side=900, max_bytes=480_000)

    commitment, salt_hex, quantised = fingerprint.commit(face.embedding)
    input_sha = sha256_file(dest)
    ph = phash_hex(dest)
    h, w = img.shape[:2]

    face_json = {
        "run_id": run_id,
        "created_at": iso(),
        "engine": engine.name,
        "model_id": engine.model_id,
        "model_files": engine.model_hashes(),
        "embedding_dim": engine.embedding_dim,
        "faces_detected": len(faces),
        "bbox": [round(float(v), 2) for v in face.bbox],
        "det_score": round(float(face.det_score), 4),
        "commitment": commitment,
        "commitment_scheme": "sha256(faceanchor-v1 || salt32 || int8(embedding*127))",
        "threshold": {"match": engine.match_threshold, "weak": engine.weak_threshold,
                      "metric": "cosine"},
        "input": {"file": "input.jpg", "sha256": input_sha, "phash": ph,
                  "width": int(w), "height": int(h),
                  "source_path": str(src)},
    }
    write_json(d / "face.json", face_json)
    # Secret material stays out of git: it is what makes the commitment binding.
    write_json(d / "face_secret.json",
               {"salt": salt_hex, "quantised_int8": quantised, "engine": engine.name})
    np.save(d / "embedding.npy", face.embedding)

    emit(StageEvent("stage_end", "scan",
                    f"face {face.det_score:.3f} | {engine.embedding_dim}-d | sha256 {input_sha[:16]}",
                    face_json))
    return face_json


# --- stage 2: search ---------------------------------------------------------------

def search(run_id: str = "", engines: str = "lens", image_url: str = "",
           use_cache: bool = True, max_candidates: int = cand_mod.DEFAULT_MAX_SCORED,
           emit: Emit = _noop) -> dict:
    d = config.resolve_run(run_id)
    face_json = read_json(d / "face.json")
    engine = get_engine(face_json.get("engine", "").split("/")[0] or config.FACE_ENGINE)
    engine.load()
    query_emb = np.load(d / "embedding.npy")
    wanted = {e.strip() for e in engines.split(",") if e.strip()}

    emit(StageEvent("stage_start", "search", f"engines: {', '.join(sorted(wanted))}"))
    search_dir = d / "search"
    search_dir.mkdir(exist_ok=True)
    cache_key = face_json["input"]["sha256"]

    quota_before = serpapi.quota()
    if quota_before:
        emit(StageEvent("log", "search",
                        f"serpapi searches left: {quota_before.get('searches_left')}"))

    raws, hit_lists, errors = [], [], []
    image_id = ""

    if "lens" in wanted and serpapi.available():
        if not image_url:
            try:
                image_id = serpapi.upload_image(d / "query.jpg")
                emit(StageEvent("log", "search", f"uploaded query image, id {image_id[:24]}"))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"serpapi upload: {exc}")
        for kind in ("visual_matches", "exact_matches"):
            try:
                rs = serpapi.google_lens(image_url=image_url, image_id=image_id, kind=kind,
                                        cache_key=cache_key, use_cache=use_cache)
                if rs.error:
                    errors.append(f"{rs.provider}/{kind}: {rs.error}")
                    continue
                raws.append(rs)
                hit_lists.append(serpapi.hits_from(rs))
                emit(StageEvent("log", "search",
                                f"{rs.provider} {kind}: search_id {rs.search_id} "
                                f"{'(disk cache)' if rs.cached else '(live)'} "
                                f"-> {len(hit_lists[-1])} hits"))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"serpapi lens {kind}: {exc}")

    if "lens" in wanted and not raws and fallbacks.searchapi_available() and image_url:
        try:
            rs = fallbacks.searchapi_lens(image_url, cache_key=cache_key, use_cache=use_cache)
            raws.append(rs)
            hit_lists.append(fallbacks.searchapi_hits(rs))
            emit(StageEvent("log", "search", f"fallback {rs.provider}: {len(hit_lists[-1])} hits"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"searchapi: {exc}")

    for name, fn in (("bing", serpapi.bing_reverse), ("yandex", serpapi.yandex_reverse)):
        if name in wanted and serpapi.available() and image_url:
            try:
                rs = fn(image_url, cache_key=cache_key, use_cache=use_cache)
                if rs.error:
                    errors.append(f"{rs.provider}: {rs.error}")
                    continue
                raws.append(rs)
                hit_lists.append(serpapi.hits_from(rs))
                emit(StageEvent("log", "search",
                                f"{rs.provider}: {len(hit_lists[-1])} hits"))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")

    if not raws:
        emit(StageEvent("error", "search",
                        "no search provider produced a response: " + "; ".join(errors)))
        raise SystemExit(config.EXIT_PROVIDER)

    all_hits = [h for hits in hit_lists for h in hits]
    cands = cand_mod.merge_hits(hit_lists)
    emit(StageEvent("log", "search",
                    f"{len(all_hits)} results, {len(cands)} of them social posts"))

    if len(cands) > max_candidates:
        emit(StageEvent("log", "search",
                        f"scoring the first {max_candidates} of {len(cands)}; the rest "
                        f"stay in candidates.json marked SKIPPED"))
    cands = cand_mod.score_candidates(
        cands, query_emb, engine, d, limit=max_candidates,
        emit=lambda c: emit(StageEvent("candidate", "search", c.url, c.as_dict())),
    )
    hop = 1
    name_guess = ""

    # Hop 2: no social post cleared the threshold, so identify the person from
    # the titles the engines returned and search those platforms by name.
    if not cand_mod.best_match(cands):
        name_guess = cand_mod.guess_name([h.title for h in all_hits])
        if name_guess:
            emit(StageEvent("log", "search",
                            f"no match yet; hop 2 by identified name: {name_guess}"))
            hop2_hits = []
            for q in cand_mod.hop2_queries(name_guess):
                try:
                    if serpapi.available():
                        rs = serpapi.google_images(q, cache_key=sha256_text(q), use_cache=use_cache)
                        hits = serpapi.hits_from(rs)
                    elif fallbacks.serper_available():
                        rs = fallbacks.serper_images(q, cache_key=sha256_text(q), use_cache=use_cache)
                        hits = fallbacks.serper_hits(rs)
                    else:
                        break
                    raws.append(rs)
                    hop2_hits.append(hits)
                    emit(StageEvent("log", "search", f"hop2 {q}: {len(hits)} hits"))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"hop2 {q}: {exc}")
            if hop2_hits:
                existing = {c.url for c in cands}
                fresh = [c for c in cand_mod.merge_hits(hop2_hits) if c.url not in existing]
                fresh = cand_mod.score_candidates(
                    fresh, query_emb, engine, d,
                    emit=lambda c: emit(StageEvent("candidate", "search", c.url, c.as_dict())),
                )
                cands = cand_mod.rerank(cands + fresh)
                hop = 2

    for i, rs in enumerate(raws):
        rs.save(search_dir, i)
    quota_after = serpapi.quota()
    write_json(d / "search" / "quota.json",
               {"before": quota_before, "after": quota_after, "errors": errors})
    write_json(d / "candidates.json", {
        "run_id": d.name,
        "searched_at": iso(),
        "engines_requested": sorted(wanted),
        "hop": hop,
        "name_guess": name_guess,
        "threshold": {"match": engine.match_threshold, "weak": engine.weak_threshold,
                      "metric": "cosine", "engine": engine.name},
        "results_total": len(all_hits),
        "social_candidates": len(cands),
        "errors": errors,
        "candidates": [c.as_dict() for c in cands],
    })

    best = cand_mod.best_match(cands)
    summary = {
        "run_id": d.name, "hop": hop, "name_guess": name_guess,
        "candidates": [c.as_dict() for c in cands],
        "best": best.as_dict() if best else None,
        "providers": [
            {"provider": r.provider, "kind": r.query_kind, "search_id": r.search_id,
             "created_at": r.created_at, "cached": r.cached, "raw_sha256": r.raw_sha256,
             "results": len(hits)}
            for r, hits in zip(raws, hit_lists + [[]] * (len(raws) - len(hit_lists)))
        ],
        "quota_before": quota_before, "quota_after": quota_after, "errors": errors,
    }
    write_json(d / "search" / "summary.json", summary)

    if not best:
        emit(StageEvent("error", "search",
                        f"no social post scored at or above {engine.match_threshold}; "
                        f"{len(cands)} candidates were checked and rejected"))
        raise SystemExit(config.EXIT_NO_MATCH)

    emit(StageEvent("stage_end", "search",
                    f"MATCH {best.similarity:.4f} on {best.platform}: {best.url}",
                    best.as_dict()))
    return summary


# --- control: score one run's candidates against a different face ------------------

def control(run_id: str = "", other_image: str = "", emit: Emit = _noop) -> dict:
    """Re-score an existing run's candidates against a different person's face.

    A search engine that keeps returning the right person is doing its job, so a
    normal run can legitimately contain no rejections. That leaves an obvious
    question: is the comparison doing anything at all? This answers it. The
    posts, the thumbnails and the code are identical; only the reference face
    changes, and everything should now be rejected.

    Uses the thumbnails already on disk, so it costs no search quota.
    """
    d = config.resolve_run(run_id)
    cands = read_json(d / "candidates.json")
    engine = get_engine(read_json(d / "face.json")["engine"].split("/")[0])
    engine.load()

    emit(StageEvent("stage_start", "control",
                    f"re-scoring {len(cands['candidates'])} candidates against "
                    f"{Path(other_image).name}"))
    faces = engine.detect_and_embed(load_image(other_image))
    face = largest(faces)
    if face is None:
        raise SystemExit(config.EXIT_NO_FACE)

    rows, flipped = [], 0
    for c in cands["candidates"]:
        if not c.get("thumbnail_file"):
            continue
        thumb = d / c["thumbnail_file"]
        if not thumb.exists():
            continue
        sims = [cosine(face.embedding, f.embedding)
                for f in engine.detect_and_embed(load_image(thumb))
                if f.embedding is not None]
        sim = max(sims) if sims else -1.0
        verdict = cand_mod.verdict_for(sim, engine) if sims else "NO_FACE"
        if c["verdict"] == "MATCH" and verdict != "MATCH":
            flipped += 1
        rows.append({"url": c["url"], "platform": c["platform"],
                     "similarity_original": c["similarity"],
                     "similarity_control": round(sim, 4),
                     "verdict_original": c["verdict"], "verdict_control": verdict})
        emit(StageEvent("candidate", "control", c["url"], {
            "url": c["url"], "platform": c["platform"], "verdict": verdict,
            "similarity": round(sim, 4), "faces_found": len(sims),
            "engines_agreeing": c.get("engines_agreeing", 1),
        }))

    out = {"run_id": d.name, "control_image": str(other_image),
           "control_face_det_score": round(float(face.det_score), 4),
           "checked": len(rows),
           "originally_matched": sum(1 for r in rows if r["verdict_original"] == "MATCH"),
           "still_matching": sum(1 for r in rows if r["verdict_control"] == "MATCH"),
           "flipped_to_rejected": flipped,
           "threshold": engine.match_threshold, "rows": rows}
    write_json(d / "control.json", out)
    emit(StageEvent("stage_end", "control",
                    f"{out['originally_matched']} matched the scanned face, "
                    f"{out['still_matching']} match the control face", out))
    return out


# --- stage 3: extract --------------------------------------------------------------

def extract(run_id: str = "", use_browser: bool = True, emit: Emit = _noop) -> dict:
    d = config.resolve_run(run_id)
    cands = read_json(d / "candidates.json")["candidates"]
    best = next((c for c in cands if c["verdict"] == "MATCH"), None)
    if best is None:
        raise SystemExit(config.EXIT_NO_MATCH)

    emit(StageEvent("stage_start", "extract", best["url"]))
    thumb = d / best["thumbnail_file"] if best.get("thumbnail_file") else None
    p = post_mod.extract(best["url"], best["platform"], d,
                         fallback_thumb=thumb,
                         fallback_thumb_url=best.get("thumbnail_url", ""),
                         use_browser=use_browser)

    # Score the face again on whatever image we actually retrieved: a full-size
    # post image is far more convincing than a 200px search thumbnail.
    similarity = float(best["similarity"])
    similarity_source = "search_thumbnail"
    if p.image_file:
        try:
            engine = get_engine(read_json(d / "face.json")["engine"].split("/")[0])
            engine.load()
            q = np.load(d / "embedding.npy")
            faces = engine.detect_and_embed(load_image(d / p.image_file))
            sims = [cosine(q, f.embedding) for f in faces if f.embedding is not None]
            if sims and max(sims) > similarity:
                similarity, similarity_source = max(sims), p.image_source
            p.image_phash = phash_hex(d / p.image_file)
        except Exception as exc:  # noqa: BLE001
            p.notes.append(f"post-image rescore failed: {type(exc).__name__}")

    out = p.as_dict()
    out.update({
        "canonical_url": canonical_url(p.url),
        "url_sha256": sha256_text(canonical_url(p.url)),
        "caption_sha256": post_mod.caption_sha256(p),
        "similarity": round(similarity, 4),
        "similarity_source": similarity_source,
        "search_similarity": round(float(best["similarity"]), 4),
        "engines_agreeing": best.get("engines_agreeing", 1),
    })
    write_json(d / "post.json", out)
    emit(StageEvent("stage_end", "extract",
                    f"{p.platform} | {p.author or 'unknown author'} | "
                    f"{p.posted_at or 'no date'} ({p.posted_at_source}) | "
                    f"image via {p.image_source}", out))
    return out


# --- stage 4: anchor ---------------------------------------------------------------

def build_record(d: Path) -> dict:
    face = read_json(d / "face.json")
    cands = read_json(d / "candidates.json")
    post = read_json(d / "post.json")
    summary = read_json(d / "search" / "summary.json")

    return {
        "schema": SCHEMA,
        "run_id": d.name,
        "created_at": face["created_at"],
        "input": face["input"] | {"source_path": None},
        "face": {
            "engine": face["engine"],
            "model_id": face["model_id"],
            "model_files": face["model_files"],
            "embedding_dim": face["embedding_dim"],
            "bbox": face["bbox"],
            "det_score": face["det_score"],
            "commitment": face["commitment"],
            "commitment_scheme": face["commitment_scheme"],
            "threshold": face["threshold"],
        },
        "search": {
            "hop": cands["hop"],
            "name_guess": cands.get("name_guess") or None,
            "engines_requested": cands["engines_requested"],
            "providers": [
                {k: p[k] for k in ("provider", "kind", "search_id", "created_at", "raw_sha256")}
                for p in summary["providers"]
            ],
            "results_total": cands["results_total"],
            "social_candidates": cands["social_candidates"],
            "quota_before": (summary.get("quota_before") or {}).get("searches_left"),
            "quota_after": (summary.get("quota_after") or {}).get("searches_left"),
            "candidates": [
                {"rank": c["rank"], "platform": c["platform"], "url": c["url"],
                 "similarity": c["similarity"], "verdict": c["verdict"],
                 "engines_agreeing": c["engines_agreeing"],
                 "thumbnail_sha256": c["thumbnail_sha256"] or None}
                for c in cands["candidates"]
            ],
        },
        "post": {
            "platform": post["platform"],
            "url": post["canonical_url"],
            "url_sha256": post["url_sha256"],
            "author": post.get("author") or None,
            "caption_excerpt": (post.get("caption") or "")[:140] or None,
            "caption_sha256": post["caption_sha256"],
            "posted_at": post.get("posted_at") or None,
            "posted_at_source": post.get("posted_at_source"),
            "image_url": post.get("image_url") or None,
            "image_sha256": post.get("image_sha256") or None,
            "image_phash": post.get("image_phash") or None,
            "image_source": post.get("image_source"),
            "extraction_method": post.get("extraction_method") or None,
            "similarity": post["similarity"],
            "similarity_source": post["similarity_source"],
        },
    }


def anchor(run_id: str = "", chain: str = "local", pin: bool = False,
           emit: Emit = _noop) -> dict:
    from .chain.client import ChainClient

    d = config.resolve_run(run_id)
    record = build_record(d)
    record["chain_intent"] = {"chain": chain, "chain_id": config.get_chain(chain).chain_id}

    emit(StageEvent("stage_start", "anchor", f"chain {chain}"))
    client = ChainClient(chain)

    deployment = client.load_deployment()
    if deployment is None:
        emit(StageEvent("log", "anchor", "no deployment found; deploying the registry"))
        deployment = client.deploy()
        emit(StageEvent("log", "anchor",
                        f"registry at {deployment['contract']} (block {deployment['deploy_block']})"))
    record["chain_intent"]["contract"] = deployment["contract"]

    data, rec_hash = write_canonical(d / "record.json", record)
    (d / "record.sha256").write_text(f"{rec_hash}  record.json\n", encoding="utf-8")
    emit(StageEvent("record", "anchor", f"record sha256 {rec_hash}",
                    {"record_hash": rec_hash, "bytes": len(data)}))

    evidence_uri = f"sha256:{rec_hash}"
    if pin and config.PINATA_JWT:
        try:
            from .chain.ipfs import pin_json
            cid = pin_json(d / "record.json")
            evidence_uri = f"ipfs://{cid}"
            emit(StageEvent("log", "anchor", f"pinned to IPFS: {cid}"))
        except Exception as exc:  # noqa: BLE001
            emit(StageEvent("log", "anchor", f"IPFS pin skipped: {exc}"))

    post = record["post"]
    if client.exists(deployment["contract"], rec_hash):
        # Anchoring is idempotent: an identical bundle keeps its first record
        # rather than paying for a duplicate. Recover the original transaction
        # from the event log so the explorer link still works.
        emit(StageEvent("log", "anchor", "identical record already anchored; reusing it"))
        onchain = client.get(deployment["contract"], rec_hash)
        event = client.find_event(deployment["contract"], rec_hash,
                                  from_block=deployment.get("deploy_block", 0))
        tx_hash = (event or {}).get("tx_hash", "")
        result = {
            "chain": chain, "chain_id": client.chain.chain_id,
            "contract": deployment["contract"], "record_hash": rec_hash,
            "already_anchored": True, "onchain": onchain,
            "tx_hash": tx_hash,
            "block_number": (event or {}).get("block_number"),
            "block_timestamp": onchain.get("anchoredAt"),
            "submitter": onchain.get("submitter"),
            "evidence_uri": onchain.get("evidenceUri"),
            "event": event or {},
            "explorer_tx": client.chain.tx_url(tx_hash) if tx_hash else "",
            "explorer_address": client.chain.addr_url(deployment["contract"]),
        }
    else:
        result = client.anchor(
            deployment["contract"],
            record_hash=rec_hash,
            input_image_sha256=record["input"]["sha256"],
            face_commitment=record["face"]["commitment"],
            post_url_hash=post["url_sha256"],
            post_image_sha256=post["image_sha256"] or ("0" * 64),
            input_phash=phash_uint64(record["input"]["phash"]),
            similarity_bps=int(round(max(0.0, post["similarity"]) * 10000)),
            evidence_uri=evidence_uri,
        )
        result["record_hash"] = rec_hash
        result["evidence_uri"] = evidence_uri
        emit(StageEvent("tx", "anchor",
                        f"tx {result['tx_hash']} in block {result['block_number']}", result))

    result["deployment"] = deployment
    write_json(d / "anchor.json", result)
    emit(StageEvent("stage_end", "anchor", result.get("explorer_tx") or "anchored on local chain",
                    result))
    return result


# --- stage 5: verify ---------------------------------------------------------------

def verify(run_id: str = "", chain: str = "", tamper_field: str = "",
           biometric: bool = False, emit: Emit = _noop) -> dict:
    from .chain.client import ChainClient

    d = config.resolve_run(run_id)
    anchor_info = read_json(d / "anchor.json")
    chain = chain or anchor_info.get("chain") or anchor_info["deployment"]["chain"]
    contract = anchor_info["deployment"]["contract"]

    if config.get_chain(chain).is_local:
        from .chain.client import _LOCAL_DEPLOYMENT

        if _LOCAL_DEPLOYMENT is None:
            raise SystemExit(
                "the in-process chain only exists while one command runs, so a "
                "record anchored by an earlier command is gone. Use "
                "`python -m faceanchor run --chain local` to do every stage at "
                "once, or anchor on a public chain with --chain base-sepolia."
            )

    emit(StageEvent("stage_start", "verify",
                    f"chain {chain} contract {contract}"
                    + (f" | TAMPERING {tamper_field}" if tamper_field else "")))

    record = read_json(d / "record.json")
    checks: list[dict] = []

    # Recompute every hash from the files on disk rather than trusting record.json.
    local_input_sha = sha256_file(d / "input.jpg")
    checks.append({"field": "input image sha256", "recomputed": local_input_sha,
                   "in_record": record["input"]["sha256"],
                   "ok": local_input_sha == record["input"]["sha256"]})

    local_phash = phash_hex(d / "input.jpg")
    checks.append({"field": "input pHash", "recomputed": local_phash,
                   "in_record": record["input"]["phash"],
                   "ok": local_phash == record["input"]["phash"]})

    if (d / "post_image.jpg").exists() and record["post"].get("image_sha256"):
        local_post_sha = sha256_file(d / "post_image.jpg")
        checks.append({"field": "post image sha256", "recomputed": local_post_sha,
                       "in_record": record["post"]["image_sha256"],
                       "ok": local_post_sha == record["post"]["image_sha256"]})

    secret_path = d / "face_secret.json"
    if secret_path.exists():
        secret = read_json(secret_path)
        recomputed = fingerprint.recommit(secret["quantised_int8"], secret["salt"])
        checks.append({"field": "face commitment", "recomputed": recomputed,
                       "in_record": record["face"]["commitment"],
                       "ok": recomputed == record["face"]["commitment"]})
        if biometric:
            engine = get_engine(record["face"]["engine"].split("/")[0])
            engine.load()
            faces = engine.detect_and_embed(load_image(d / "input.jpg"))
            f = largest(faces)
            sim = fingerprint.cosine_to_quantised(f.embedding, secret["quantised_int8"]) if f else -1
            checks.append({"field": "biometric re-scan (cosine)", "recomputed": f"{sim:.4f}",
                           "in_record": f">= {engine.match_threshold}",
                           "ok": sim >= engine.match_threshold})

    if tamper_field:
        record = _tamper(record, tamper_field)
        emit(StageEvent("log", "verify", f"tampered field: {tamper_field}"))

    local_hash = sha256_bytes(canonical_bytes(record))
    anchored_hash = anchor_info["record_hash"]
    checks.append({"field": "record hash (sha256 of canonical record.json)",
                   "recomputed": local_hash, "in_record": anchored_hash,
                   "ok": local_hash == anchored_hash})

    client = ChainClient(chain)
    onchain = client.verify(
        contract,
        record_hash=local_hash,
        input_image_sha256=record["input"]["sha256"],
        face_commitment=record["face"]["commitment"],
        post_url_hash=record["post"]["url_sha256"],
        post_image_sha256=record["post"]["image_sha256"] or ("0" * 64),
    )
    stored = client.get(contract, local_hash) if onchain["found"] else {}
    event = client.find_event(contract, local_hash,
                              from_block=anchor_info["deployment"].get("deploy_block", 0))

    ok = all(c["ok"] for c in checks) and onchain["ok"]
    report = {
        "run_id": d.name, "verified_at": iso(), "chain": chain, "chain_id": client.chain.chain_id,
        "contract": contract, "record_hash_local": local_hash, "record_hash_anchored": anchored_hash,
        "tampered_field": tamper_field or None,
        "local_checks": checks, "onchain": onchain,
        "onchain_record": stored, "event_found": bool(event), "event": event,
        "explorer_tx": anchor_info.get("explorer_tx", ""),
        "verdict": "VERIFIED" if ok else "MISMATCH",
        "exit_code": config.EXIT_OK if ok else config.EXIT_NO_MATCH,
    }
    name = "verify_log.json" if not tamper_field else f"verify_tampered_{tamper_field}.json"
    write_json(d / name, report)
    emit(StageEvent("verified" if ok else "error", "verify", report["verdict"], report))
    return report


def _tamper(record: dict, field: str) -> dict:
    """Mutate exactly one field of a copy of the record, as an attacker would."""
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
    elif field == "candidate":
        if r["search"]["candidates"]:
            r["search"]["candidates"][0]["similarity"] = 0.9999
    else:
        raise SystemExit(
            "unknown --field. choose: caption | post_url | similarity | input_image | candidate"
        )
    return r


def copy_to_demo(run_id: str = "") -> Path:
    """Copy one run into evidence/demo/, leaving the biometric secret behind."""
    d = config.resolve_run(run_id)
    dest = config.DEMO_EVIDENCE_ROOT / d.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(d, dest, ignore=shutil.ignore_patterns("face_secret.json", "embedding.npy"))
    write_json(dest / "README.json", {
        "note": "Sanitised copy of a real run. face_secret.json and embedding.npy are "
                "withheld on purpose: they are the biometric material behind the "
                "on-chain commitment and never leave the operator's machine.",
        "verify": "python verify.py --record evidence/demo/%s/record.json" % d.name,
    })
    return dest
