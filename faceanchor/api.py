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
from .canonical import new_run_id, read_json
from .events import StageEvent

app = FastAPI(title="FaceAnchor", version="0.1.0")

# run_id -> {"queue": Queue, "events": [...], "done": bool}
RUNS: dict[str, dict] = {}
UPLOADS = config.ROOT / ".cache" / "uploads"


def _emitter(run_id: str):
    def emit(ev: StageEvent) -> None:
        state = RUNS.setdefault(run_id, {"queue": queue.Queue(), "events": [], "done": False})
        state["events"].append(ev)
        state["queue"].put(ev)
    return emit


def _run_pipeline(run_id: str, image_path: Path, chain: str, engines: str,
                  image_url: str, use_browser: bool) -> None:
    emit = _emitter(run_id)
    state = RUNS[run_id]
    try:
        pipeline.scan(image_path, "", run_id, emit=emit)
        pipeline.search(run_id, engines, image_url, emit=emit)
        pipeline.extract(run_id, use_browser=use_browser, emit=emit)
        pipeline.anchor(run_id, chain, emit=emit)
        pipeline.verify(run_id, chain, emit=emit)
    except SystemExit as exc:
        emit(StageEvent("error", "pipeline", f"stopped with exit code {exc.code}",
                        {"exit_code": exc.code}))
    except Exception as exc:  # noqa: BLE001 - surfaced to the dashboard
        emit(StageEvent("error", "pipeline", f"{type(exc).__name__}: {exc}"))
    finally:
        state["done"] = True
        state["queue"].put(None)


@app.post("/api/runs")
async def start_run(image: UploadFile = File(...), chain: str = Form("local"),
                    engines: str = Form("lens"), image_url: str = Form(""),
                    use_browser: bool = Form(True)):
    run_id = new_run_id()
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS / f"{run_id}.jpg"
    dest.write_bytes(await image.read())

    RUNS[run_id] = {"queue": queue.Queue(), "events": [], "done": False}
    threading.Thread(
        target=_run_pipeline,
        args=(run_id, dest, chain, engines, image_url, use_browser),
        daemon=True,
    ).start()
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/events")
async def stream(run_id: str):
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "unknown run")

    async def gen():
        # Replay what already happened so a late viewer sees the whole run.
        for ev in list(state["events"]):
            yield f"event: {ev.kind}\ndata: {ev.to_json()}\n\n"
        if state["done"]:
            yield "event: done\ndata: {}\n\n"
            return
        loop = asyncio.get_running_loop()
        seen = len(state["events"])
        while True:
            ev = await loop.run_in_executor(None, state["queue"].get)
            if ev is None:
                yield "event: done\ndata: {}\n\n"
                return
            # The replay above may already have covered this event.
            if state["events"].index(ev) < seen:
                continue
            yield f"event: {ev.kind}\ndata: {ev.to_json()}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}")
async def run_state(run_id: str):
    d = config.EVIDENCE_ROOT / run_id
    out: dict = {"run_id": run_id, "done": RUNS.get(run_id, {}).get("done", not d.exists())}
    for name, key in (("face.json", "face"), ("candidates.json", "candidates"),
                      ("post.json", "post"), ("anchor.json", "anchor"),
                      ("verify_log.json", "verify")):
        if (d / name).exists():
            out[key] = read_json(d / name)
    return out


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
