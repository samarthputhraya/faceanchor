"""HTTP + server-sent events behind the dashboard.

The dashboard is a viewer of the same StageEvent stream the CLI prints, so it
cannot show anything the pipeline did not actually do.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, pipeline
from .search.candidates import DEFAULT_MAX_SCORED
from .canonical import new_run_id, read_json
from .events import StageEvent

app = FastAPI(title="FaceAnchor", version="0.1.0")

# run_id -> {"subscribers": [Queue, ...], "events": [...], "done": bool}
# One queue per connected viewer: a single shared queue meant a reconnect split
# the stream between two consumers and stranded the abandoned reader forever.
RUNS: dict[str, dict] = {}


def _new_state() -> dict:
    return {"subscribers": [], "events": [], "done": False}


UPLOADS = config.ROOT / ".cache" / "uploads"


def _emitter(run_id: str):
    def emit(ev: StageEvent) -> None:
        state = RUNS.setdefault(run_id, _new_state())
        state["events"].append(ev)
        for q in list(state["subscribers"]):
            q.put(ev)
    return emit


def _run_pipeline(run_id: str, image_path: Path, chain: str, engines: str,
                  image_url: str, use_browser: bool, max_candidates: int) -> None:
    emit = _emitter(run_id)
    state = RUNS[run_id]
    try:
        pipeline.scan(image_path, "", run_id, emit=emit)
        pipeline.search(run_id, engines, image_url,
                        max_candidates=max_candidates, emit=emit)
        pipeline.extract(run_id, use_browser=use_browser, emit=emit)
        pipeline.prove(run_id, emit=emit)
        pipeline.anchor(run_id, chain, emit=emit)
        pipeline.verify(run_id, chain, emit=emit)
    except SystemExit as exc:
        emit(StageEvent("error", "pipeline", f"stopped with exit code {exc.code}",
                        {"exit_code": exc.code}))
    except Exception as exc:  # noqa: BLE001 - surfaced to the dashboard
        emit(StageEvent("error", "pipeline", f"{type(exc).__name__}: {exc}"))
    finally:
        state["done"] = True
        for q in list(state["subscribers"]):
            q.put(None)


@app.post("/api/runs")
async def start_run(image: UploadFile = File(...),
                    chain: str = Form(config.DEFAULT_CHAIN),
                    engines: str = Form("lens"), image_url: str = Form(""),
                    use_browser: bool = Form(True),
                    max_candidates: int = Form(DEFAULT_MAX_SCORED)):
    run_id = new_run_id()
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS / f"{run_id}.jpg"
    dest.write_bytes(await image.read())

    RUNS[run_id] = _new_state()
    threading.Thread(
        target=_run_pipeline,
        args=(run_id, dest, chain, engines, image_url, use_browser, max_candidates),
        daemon=True,
    ).start()
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/events")
async def stream(run_id: str):
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "unknown run")

    async def gen():
        # Subscribe first, then replay, so nothing emitted during the replay is
        # lost. Each viewer gets its own queue, so a reconnect is harmless.
        q: queue.Queue = queue.Queue()
        state["subscribers"].append(q)
        try:
            replay = list(state["events"])
            for ev in replay:
                yield f"event: {ev.kind}\ndata: {ev.to_json()}\n\n"
            if state["done"]:
                yield "event: done\ndata: {}\n\n"
                return
            replayed = set(map(id, replay))
            loop = asyncio.get_running_loop()
            while True:
                try:
                    ev = await asyncio.wait_for(
                        loop.run_in_executor(None, q.get), timeout=20)
                except asyncio.TimeoutError:
                    # Scoring can run for minutes; a comment frame stops the
                    # browser and any proxy from giving up on a quiet stream.
                    yield ": keep-alive\n\n"
                    continue
                if ev is None:
                    yield "event: done\ndata: {}\n\n"
                    return
                if id(ev) in replayed:
                    continue
                yield f"event: {ev.kind}\ndata: {ev.to_json()}\n\n"
        finally:
            if q in state["subscribers"]:
                state["subscribers"].remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@app.get("/api/runs/{run_id}")
async def run_state(run_id: str):
    d = config.EVIDENCE_ROOT / run_id
    out: dict = {"run_id": run_id, "done": RUNS.get(run_id, {}).get("done", not d.exists())}
    for name, key in (("face.json", "face"), ("candidates.json", "candidates"),
                      ("post.json", "post"), ("zk.json", "zk"), ("replicate.json", "replicate"),
                      ("anchor.json", "anchor"), ("forge_demo.json", "forge"),
                      ("verify_log.json", "verify")):
        if (d / name).exists():
            out[key] = read_json(d / name)
    return out


@app.post("/api/runs/{run_id}/replicate")
async def replicate_run(run_id: str, live: bool = Form(False)):
    """Re-derive the post-side face from published data only.

    Defaults to offline: the live re-fetch can take tens of seconds behind a
    filtering network, and the three offline checks are the ones that carry the
    argument.
    """
    from . import replicate as replicate_mod

    try:
        return replicate_mod.replicate(run_id, live=live)
    except SystemExit as exc:
        raise HTTPException(status_code=400, detail=str(exc.code)) from exc


@app.post("/api/runs/{run_id}/forge")
def forge_run(run_id: str, forged_bps: int = Form(9999)):
    """Ask the chain to accept a similarity the proof does not support.

    Sync, not async: the three attempts are blocking eth_calls, and a coroutine
    would hold the event loop -- and every open SSE stream -- for their duration.

    forge_demo exits non-zero when the outcome is not the expected
    accept/reject/reject, but it has already written forge_demo.json by then, so
    the dashboard is shown what actually happened rather than a bare error.
    """
    try:
        return pipeline.forge_demo(run_id, "", forged_bps)
    except SystemExit as exc:
        for root in (config.EVIDENCE_ROOT, config.DEMO_EVIDENCE_ROOT):
            written = root / run_id / "forge_demo.json"
            if written.exists():
                return read_json(written)
        raise HTTPException(400, str(exc.code)) from exc


@app.get("/api/runs/{run_id}/files/{name:path}")
async def run_file(run_id: str, name: str):
    d = (config.EVIDENCE_ROOT / run_id).resolve()
    p = (d / name).resolve()
    if not str(p).startswith(str(d)) or not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(p)


@app.post("/api/runs/{run_id}/verify")
async def verify_run(run_id: str, field: str = Form("")):
    try:
        return pipeline.verify(run_id, "", field, False)
    except SystemExit as exc:
        raise HTTPException(400, f"verify failed with exit code {exc.code}")


@app.get("/api/status")
async def status():
    from .search import serpapi

    return {
        "serpapi": bool(config.SERPAPI_KEY),
        "searchapi": bool(config.SEARCHAPI_KEY),
        "serper": bool(config.SERPER_KEY),
        "wallet": bool(config.PRIVATE_KEY),
        "quota": serpapi.quota() if config.SERPAPI_KEY else {},
        "chains": [
            {"name": c.name, "chain_id": c.chain_id,
             "deployed": (config.DEPLOYMENTS_DIR / f"{c.name}.json").exists() or c.is_local}
            for c in config.CHAINS.values()
        ],
    }


DIST = config.ROOT / "ui" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="ui")
else:
    @app.get("/")
    async def no_ui():
        return JSONResponse({
            "message": "dashboard not built yet",
            "build_it": "cd ui && npm install && npm run build",
            "api": ["/api/status", "POST /api/runs", "/api/runs/{id}/events"],
        })
