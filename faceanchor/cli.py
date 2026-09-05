"""FaceAnchor command line.

The terminal is the product surface: every stage prints what it did, what it
fetched, and what it decided, so a viewer can audit the run as it happens.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import config, pipeline
from .canonical import read_json
from .events import StageEvent

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Face scan -> genuine reverse image search -> tamper-evident on-chain record.",
)
console = Console()

VERDICT_STYLE = {
    "MATCH": "bold green", "WEAK": "yellow", "REJECT": "red",
    "NO_FACE": "dim", "FETCH_FAIL": "dim red",
}
STAGE_TITLE = {
    "scan": "1 SCAN  face detection and encoding",
    "search": "2 SEARCH  reverse image search across the open web",
    "extract": "3 EXTRACT  fetch the matched post",
    "anchor": "4 ANCHOR  write the evidence hash on-chain",
    "verify": "5 VERIFY  recompute and re-read from the chain",
}


def make_emitter(json_mode: bool = False):
    def emit(ev: StageEvent) -> None:
        if json_mode:
            print(ev.to_json(), flush=True)
            return
        if ev.kind == "stage_start":
            console.rule(f"[bold cyan]{STAGE_TITLE.get(ev.stage, ev.stage)}")
            if ev.message:
                console.print(f"  [dim]{ev.ts}[/dim]  {ev.message}")
        elif ev.kind == "stage_end":
            console.print(f"  [bold green]done[/bold green]  {ev.message}\n")
        elif ev.kind == "candidate":
            d = ev.data
            style = VERDICT_STYLE.get(d["verdict"], "")
            sim = f"{d['similarity']:.4f}" if d["similarity"] >= 0 else "  --  "
            console.print(
                f"    [{style}]{d['verdict']:<10}[/] {sim}  "
                f"[dim]{d['platform']:<10}[/dim] {d['url'][:72]}"
            )
        elif ev.kind == "error":
            console.print(f"  [bold red]{ev.message}[/bold red]")
        elif ev.kind in ("record", "tx"):
            console.print(f"  [bold]{ev.message}[/bold]")
        else:
            console.print(f"  [dim]{ev.ts}[/dim]  {ev.message}")
    return emit


def candidate_table(path: Path) -> Table:
    data = read_json(path)
    th = data["threshold"]
    t = Table(
        title=f"candidates scored with {th['engine']}  "
              f"(cosine, MATCH >= {th['match']}, WEAK >= {th['weak']})",
        title_style="bold", header_style="bold", expand=False,
    )
    t.add_column("#", justify="right")
    t.add_column("verdict")
    t.add_column("cosine", justify="right")
    t.add_column("faces", justify="right")
    t.add_column("eng", justify="right")
    t.add_column("platform")
    t.add_column("post url", overflow="fold", max_width=62)
    for c in data["candidates"]:
        sim = f"{c['similarity']:.4f}" if c["similarity"] >= 0 else "--"
        t.add_row(
            str(c["rank"]),
            Text(c["verdict"], style=VERDICT_STYLE.get(c["verdict"], "")),
            sim, str(c["faces_found"]), str(c["engines_agreeing"]),
            c["platform"], c["url"],
        )
    return t


def check_table(report: dict) -> Table:
    t = Table(header_style="bold", expand=False,
              title="local recomputation vs the record and the chain", title_style="bold")
    t.add_column("check")
    t.add_column("recomputed locally", overflow="fold", max_width=44)
    t.add_column("expected", overflow="fold", max_width=44)
    t.add_column("", justify="center")
    for c in report["local_checks"]:
        t.add_row(c["field"], str(c["recomputed"])[:66], str(c["in_record"])[:66],
                  Text("PASS", style="bold green") if c["ok"] else Text("FAIL", style="bold red"))
    oc = report["onchain"]
    for label, key in (("chain: record found", "found"), ("chain: input image", "image_ok"),
                       ("chain: face commitment", "face_ok"), ("chain: post url", "post_ok"),
                       ("chain: post image", "post_image_ok")):
        t.add_row(label, "eth_call verify()", "true",
                  Text("PASS", style="bold green") if oc[key] else Text("FAIL", style="bold red"))
    t.add_row("chain: event log", "get_logs(Anchored)",
              "present", Text("PASS", style="bold green") if report["event_found"]
              else Text("FAIL", style="bold red"))
    return t


# --- commands ----------------------------------------------------------------------

@app.command()
def scan(image: str = typer.Option(..., "--image", "-i", help="input photograph"),
         engine: str = typer.Option("", "--engine", help="insightface | sface"),
         run: str = typer.Option("", "--run", help="reuse an existing run id"),
         json_out: bool = typer.Option(False, "--json")):
    """Detect a face, encode it, and commit to it privately."""
    out = pipeline.scan(image, engine, run, emit=make_emitter(json_out))
    if not json_out:
        console.print(Panel(
            Group(
                Text(f"run id        {out['run_id']}"),
                Text(f"engine        {out['engine']}  ({out['model_id']})"),
                Text(f"faces found   {out['faces_detected']}  "
                     f"detector confidence {out['det_score']}"),
                Text(f"embedding     {out['embedding_dim']}-d, L2-normalised"),
                Text(f"input sha256  {out['input']['sha256']}"),
                Text(f"input pHash   {out['input']['phash']}"),
                Text(f"commitment    {out['commitment']}"),
                Text("scheme        " + out["commitment_scheme"], style="dim"),
                Text("the embedding itself never leaves this machine", style="dim italic"),
            ), title="face scan", border_style="cyan"))
    print(out["run_id"])


@app.command()
def search(run: str = typer.Option("", "--run"),
           engines: str = typer.Option("lens", "--engines",
                                       help="comma separated: lens,bing,yandex"),
           image_url: str = typer.Option("", "--image-url",
                                         help="public URL of the query image (else upload)"),
           no_cache: bool = typer.Option(False, "--no-cache", help="ignore the on-disk cache"),
           json_out: bool = typer.Option(False, "--json")):
    """Search the open web for this face and score every candidate."""
    out = pipeline.search(run, engines, image_url, use_cache=not no_cache,
                          emit=make_emitter(json_out))
    if not json_out:
        console.print(candidate_table(config.resolve_run(run) / "candidates.json"))
        console.print()


@app.command()
def extract(run: str = typer.Option("", "--run"),
            no_browser: bool = typer.Option(False, "--no-browser"),
            json_out: bool = typer.Option(False, "--json")):
    """Fetch author, caption, date and image from the matched post."""
    out = pipeline.extract(run, use_browser=not no_browser, emit=make_emitter(json_out))
    if not json_out:
        console.print(Panel(
            Group(
                Text(f"platform    {out['platform']}"),
                Text(f"post        {out['canonical_url']}"),
                Text(f"author      {out.get('author') or 'not exposed publicly'}"),
                Text(f"posted at   {out.get('posted_at') or 'unknown'}  "
                     f"({out.get('posted_at_source')})"),
                Text(f"caption     {(out.get('caption') or '')[:110] or 'none'}"),
                Text(f"image via   {out['image_source']}  "
                     f"(method: {out.get('extraction_method') or 'n/a'})"),
                Text(f"image sha   {out.get('image_sha256') or 'none'}"),
                Text(f"similarity  {out['similarity']:.4f} "
                     f"(from {out['similarity_source']}; "
                     f"search thumbnail scored {out['search_similarity']:.4f})"),
            ), title="matched post", border_style="cyan"))


@app.command()
def anchor(run: str = typer.Option("", "--run"),
           chain: str = typer.Option("local", "--chain",
                                     help="local | base-sepolia | sepolia"),
           pin: bool = typer.Option(False, "--pin", help="also pin the record to IPFS"),
           json_out: bool = typer.Option(False, "--json")):
    """Write the evidence hash to the registry contract."""
    out = pipeline.anchor(run, chain, pin, emit=make_emitter(json_out))
    if not json_out and not out.get("already_anchored"):
        console.print(Panel(
            Group(
                Text(f"chain        {out['chain']}  (id {out['chain_id']})"),
                Text(f"contract     {out['contract']}"),
                Text(f"tx           {out['tx_hash']}"),
                Text(f"block        {out['block_number']}  at {out.get('block_time_utc')}"),
                Text(f"gas used     {out.get('gas_used')}"),
                Text(f"record hash  {out['record_hash']}"),
                Text(f"evidence     {out.get('evidence_uri')}"),
                Text(f"explorer     {out.get('explorer_tx') or 'n/a (local chain)'}",
                     style="bold"),
            ), title="anchored", border_style="green"))


@app.command()
def verify(run: str = typer.Option("", "--run"),
           chain: str = typer.Option("", "--chain"),
           field: str = typer.Option("", "--tamper",
                                     help="caption | post_url | similarity | input_image | candidate"),
           biometric: bool = typer.Option(False, "--biometric",
                                          help="also re-embed the input and compare"),
           json_out: bool = typer.Option(False, "--json")):
    """Recompute every hash and re-read the record from the chain."""
    report = pipeline.verify(run, chain, field, biometric, emit=make_emitter(json_out))
    if not json_out:
        console.print(check_table(report))
        style = "bold green" if report["verdict"] == "VERIFIED" else "bold red"
        note = ("every hash recomputed from the files on disk matches the record "
                "anchored on-chain" if report["verdict"] == "VERIFIED" else
                f"the record no longer matches the chain"
                + (f" after tampering with '{field}'" if field else ""))
        console.print(Panel(Text(f"{report['verdict']}\n{note}", justify="center"),
                            border_style=style, style=style))
        if report.get("explorer_tx"):
            console.print(f"  {report['explorer_tx']}\n")
    raise typer.Exit(report["exit_code"])


@app.command("tamper-demo")
def tamper_demo(run: str = typer.Option("", "--run"),
                chain: str = typer.Option("", "--chain"),
                field: str = typer.Option("caption", "--field")):
    """Show that changing one field breaks verification."""
    console.rule("[bold]honest record")
    good = pipeline.verify(run, chain, "", False, emit=make_emitter(False))
    console.print(check_table(good))
    console.print(Panel(Text(good["verdict"], justify="center"),
                        border_style="bold green", style="bold green"))
    console.rule(f"[bold]same record with '{field}' altered by one character")
    bad = pipeline.verify(run, chain, field, False, emit=make_emitter(False))
    console.print(check_table(bad))
    console.print(Panel(
        Text(f"{bad['verdict']}\nlocal  {bad['record_hash_local']}\n"
             f"chain  {bad['record_hash_anchored']}", justify="center"),
        border_style="bold red", style="bold red"))
    raise typer.Exit(config.EXIT_OK if (good["verdict"] == "VERIFIED"
                                        and bad["verdict"] == "MISMATCH")
                     else config.EXIT_NO_MATCH)


@app.command()
def run(image: str = typer.Option(..., "--image", "-i"),
        chain: str = typer.Option("local", "--chain"),
        engines: str = typer.Option("lens", "--engines"),
        engine: str = typer.Option("", "--engine"),
        image_url: str = typer.Option("", "--image-url"),
        pin: bool = typer.Option(False, "--pin"),
        no_browser: bool = typer.Option(False, "--no-browser"),
        no_cache: bool = typer.Option(False, "--no-cache")):
    """Run every stage: scan, search, extract, anchor, verify."""
    emit = make_emitter(False)
    face = pipeline.scan(image, engine, "", emit=emit)
    rid = face["run_id"]
    pipeline.search(rid, engines, image_url, use_cache=not no_cache, emit=emit)
    console.print(candidate_table(config.resolve_run(rid) / "candidates.json"))
    console.print()
    pipeline.extract(rid, use_browser=not no_browser, emit=emit)
    pipeline.anchor(rid, chain, pin, emit=emit)
    report = pipeline.verify(rid, chain, "", False, emit=emit)
    console.print(check_table(report))
    style = "bold green" if report["verdict"] == "VERIFIED" else "bold red"
    console.print(Panel(Text(f"{report['verdict']}   run {rid}", justify="center"),
                        border_style=style, style=style))
    if report.get("explorer_tx"):
        console.print(f"  {report['explorer_tx']}\n")
    raise typer.Exit(report["exit_code"])


@app.command()
def deploy(chain: str = typer.Option("base-sepolia", "--chain")):
    """Deploy the registry contract and record the address."""
    from .chain.client import ChainClient

    c = ChainClient(chain)
    console.print(f"deploying from {c.sender} (balance {c.balance_eth:.6f} ETH) via {c.rpc_url}")
    info = c.deploy()
    console.print(Panel(Group(*[Text(f"{k:<18} {v}") for k, v in info.items()]),
                        title="deployed", border_style="green"))


@app.command()
def newkey():
    """Generate a throwaway wallet for testnet use."""
    from eth_account import Account

    acct = Account.create()
    console.print(Panel(
        Group(
            Text(f"address      {acct.address}", style="bold"),
            Text(f"private key  {acct.key.hex()}"),
            Text("\nPut the private key in .env as PRIVATE_KEY. Testnet only.",
                 style="yellow"),
            Text("Fund it at https://portal.cdp.coinbase.com/products/faucet "
                 "(Base Sepolia)", style="dim"),
        ), title="burner wallet", border_style="yellow"))


@app.command("fetch-models")
def fetch_models():
    """Download the OpenCV fallback models."""
    from .face.sface import download_models

    console.print("downloading YuNet and SFace ...")
    for name, digest in download_models().items():
        console.print(f"  {name}  sha256 {digest}")


@app.command("demo-bundle")
def demo_bundle(run: str = typer.Option("", "--run")):
    """Copy a run into evidence/demo/ without the biometric secret."""
    dest = pipeline.copy_to_demo(run)
    console.print(f"sanitised bundle written to {dest}")


@app.command()
def status():
    """Show configuration and readiness without revealing secrets."""
    from .chain.contract import BUILD

    t = Table(header_style="bold", title="faceanchor status", title_style="bold")
    t.add_column("item")
    t.add_column("value")
    t.add_column("", justify="center")

    def row(name, value, ok):
        t.add_row(name, value, Text("ok", style="green") if ok else Text("missing", style="red"))

    row("SERPAPI_KEY", "set" if config.SERPAPI_KEY else "not set", bool(config.SERPAPI_KEY))
    row("SEARCHAPI_KEY", "set" if config.SEARCHAPI_KEY else "not set", bool(config.SEARCHAPI_KEY))
    row("SERPER_KEY", "set" if config.SERPER_KEY else "not set", bool(config.SERPER_KEY))
    row("PRIVATE_KEY", "set" if config.PRIVATE_KEY else "not set", bool(config.PRIVATE_KEY))
    row("contract artifact", str(BUILD.name), BUILD.exists())
    for name in ("base-sepolia", "sepolia"):
        p = config.DEPLOYMENTS_DIR / f"{name}.json"
        if p.exists():
            row(f"deployment {name}", json.loads(p.read_text())["contract"], True)
    if config.SERPAPI_KEY:
        from .search import serpapi
        q = serpapi.quota()
        row("serpapi quota", f"{q.get('searches_left')} searches left", bool(q.get("searches_left")))
    latest = config.latest_run()
    row("latest run", latest or "none", bool(latest))
    console.print(t)


@app.command()
def serve(host: str = typer.Option("127.0.0.1", "--host"),
          port: int = typer.Option(8000, "--port")):
    """Serve the web dashboard and its event stream."""
    import uvicorn

    uvicorn.run("faceanchor.api:app", host=host, port=port, log_level="info")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        console.print("\ninterrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
