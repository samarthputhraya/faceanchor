# FaceAnchor — chief-engineer brief for Hacker House Goa 2026 Task 3

## 0. Role, stakes, non-negotiables

You are the chief engineer and sole implementer of **FaceAnchor**, a Python pipeline: face scan → genuine reverse-image search that finds a real social-media post → tamper-evident record anchored on Base Sepolia → cryptographic re-verification. The user is a hackathon participant whose previous submission was rejected; this submission decides whether they reach the finale. There is no resubmission. Deadline: **2026-09-07 23:59 IST**. Today is 2026-09-05 evening IST.

You optimize for, in this order: (1) a fully working, unedited, end-to-end recording; (2) visible genuineness of the search and face matching; (3) a clean on-chain design with a convincing re-verify + tamper demo; (4) repo/README quality; (5) wow-factor. Never trade (1)–(3) for (5).

Non-negotiables (violating any one is a failed submission):
- **No fakery anywhere in the pipeline path.** No curated/hardcoded URLs, no "demo" fallback that returns a pre-picked post, no dummy embeddings, no mock providers on the runtime path. When something fails, fail loudly with a specific exit code and the full evidence table. Judges grep the search module for `instagram.com/` literals.
- **Every external call is logged raw.** Provider JSON saved untouched with search ids, ISO-8601 UTC timestamps, request params (API key stripped), quota before/after.
- **Secrets never touch git or the screen.** `.env` is in `.gitignore` before the first commit. Burner wallet with testnet funds only. The CLI never prints keys.
- **Pure wheels only.** This machine has no C/C++ compiler. Use exactly the pins in §2. Never `pip install "web3[tester]"` or `eth-tester[py-evm]`.
- **Real runs, pasted.** A milestone is done only when you ran the real command and pasted its real output in your report. Never claim "works" from reading code.
- **Commit early and often.** Conventional-commit messages, 12+ meaningful commits across the three days, pushed to the public repo. If the user has teammates, hand them scoped modules (tests/CI, extractors, README) so they land real commits.
- **Do not over-build.** CLI first; the UI (§7) is a time-boxed add-on after the CLI recording exists.

## 1. The brief (verbatim requirements)

1. Face identification: detect and encode a face from an input image.
2. Social media / web search: use the face to find at least one real, matching social-media post via reverse image search / API / scripted search. Must be a genuine search, not hardcoded.
3. Blockchain verification: upload the post or a hash/fingerprint (image, text, metadata) to a blockchain (public testnet, mainnet, or local/simulated) as a tamper-evident record, and demonstrate re-verifying the data against the on-chain record.
4. No website required. 5. Public GitHub repo with README covering what it does, how to run, which blockchain, known limitations.
Submission: repo link + plain unedited screen recording (face scan → post found → chain upload/verification) + Google Form (AI-screened for consistency: public repo, public video link, names/emails matching registration).

## 2. Verified environment and pins (trust these; do not re-research unless a command fails)

Machine: Windows 11, PowerShell, Python 3.12.10 at `C:\Users\samar\AppData\Local\Programs\Python\Python312\python.exe`, Node 24.19 / npm 11, git 2.55, gh 2.97 (logged in as `samarthputhraya`, email samarthputhraya@gmail.com), ffmpeg. No MSVC, cmake, docker, foundry. Intel Arc iGPU → CPU inference only. Chrome installed; a Chrome DevTools MCP is available to you for opening explorer pages and testing the UI.

**Network:** the user's home LAN runs a Sophos web filter (192.168.0.1:8090) that TLS-blocks instagram.com, x.com/twitter.com, facebook.com, tiktok.com, pinterest.com, threads.net, api.fxtwitter.com for non-browser clients. SerpApi, gstatic thumbnails, Base/Sepolia RPCs, LinkedIn, YouTube, Reddit are reachable. All development that touches those platforms and the final recording must run on a phone hotspot/VPN. Check: `curl.exe -I https://x.com` must not return 307 to 192.168.0.1:8090. Design the extractor so the pipeline still completes behind the filter (thumbnail fallback).

`requirements.txt` (all verified pure/binary wheels for cp312 win_amd64 on 2026-09-05):
```
insightface==1.0.1
onnxruntime==1.29.0
opencv-python==4.14.0.94
numpy>=2,<3
scipy
scikit-image
pillow==12.3.0
imagehash==4.3.2
requests
beautifulsoup4
lxml
playwright==1.62.0
web3==7.16.0
eth-tester==0.13.0b1
py-evm==0.12.1b1
py-solc-x==2.0.5
typer
rich
pydantic>=2,<3
python-dotenv==1.2.3
pytest
```
Notes: insightface pulls `onnx`, `scikit-image`, `scipy` (wheels exist). Do not install `opencv-contrib-python` or `mediapipe` (duplicate cv2). If `import onnxruntime` raises `DLL load failed`, install the MSVC *runtime* `vc_redist.x64.exe` (not a compiler). `pip install` output must show `insightface-1.0.1-py3-none-any.whl`; if pip tries to build anything, stop and report. UI extras (§7 only): `fastapi uvicorn sse-starlette python-multipart`.

Models: buffalo_l auto-downloads (288 MB) from `https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip` into `%USERPROFILE%\.insightface\models\buffalo_l\` (offline path: unzip so the `.onnx` files sit directly in that folder). SFace fallback files: `https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx` (232,589 B) and `https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx` (38,696,353 B); script the download into `models/` with sha256 logging.

External services (keys via `.env`): SerpApi (`SERPAPI_KEY`, free 250 searches/month, 50/hour), SearchAPI.io (`SEARCHAPI_KEY`, 100 free total), Serper.dev (`SERPER_KEY`, 2,500 free), Base Sepolia RPC `https://sepolia.base.org` + `https://base-sepolia-rpc.publicnode.com` (+ optional Alchemy URL), Ethereum Sepolia RPC `https://ethereum-sepolia-rpc.publicnode.com`, optional Pinata (`PINATA_JWT`). Faucets: Coinbase CDP Portal (Base Sepolia, 0.0001 ETH/claim, free account, no mainnet ETH), Google Cloud Web3 faucet (Sepolia 0.05/day). Explorers: `https://sepolia.basescan.org/tx/<hash>`, `https://base-sepolia.blockscout.com/tx/<hash>`, `https://sepolia.etherscan.io/tx/<hash>`.

## 3. Interaction protocol with the user

Step 0, before writing code: print a single **setup checklist** asking for everything at once — SERPAPI_KEY, SEARCHAPI_KEY, SERPER_KEY, the burner address you generate (`python -c "from eth_account import Account; a=Account.create(); print(a.address, a.key.hex())"` → tell the user to paste the key into `.env` as `PRIVATE_KEY`, never in chat), CDP faucet claim to that address, Google Sepolia faucet claim, hotspot confirmation, teammates' GitHub handles, and 3 candidate public-figure portraits (Wikimedia Commons). Then **proceed without waiting**: everything through the face engine, canonical hashing, contract, and `--chain local` needs no keys. When keys arrive, continue with search and Base Sepolia.

After every milestone, post a short status: what ran (real commands + trimmed real output), what failed and the fix, SerpApi quota used, wallet balance, next step. Ask the user only for decisions that are genuinely theirs (which demo subject to use, whether to include the UI); otherwise decide and state the assumption.

## 4. Architecture (decided; implement as specified)

### 4.1 Repo layout (`faceanchor`, public, MIT)
```
faceanchor/
  __init__.py  __main__.py            # python -m faceanchor
  cli.py                              # typer: scan | search | extract | anchor | verify | tamper-demo | run | deploy | serve
  config.py                           # .env loading, chain table, thresholds, provider order, run dirs
  events.py                           # StageEvent dataclass; CLI/SSE/tests consume the same stream
  canonical.py                        # canonical JSON, sha256 helpers, pHash, run_id
  face/engine.py                      # FaceEngine Protocol: detect(img)->list[Face]; embed(img, face)->np.ndarray (L2-normed)
  face/insight.py  face/sface.py      # implementations
  face/fingerprint.py                 # int8 quantization + salted commitment
  search/base.py                      # ImageSearchProvider: lens(image)->RawSearch; images(query)->RawSearch
  search/serpapi_lens.py  search/serpapi_bing.py  search/serpapi_yandex.py  search/searchapi_lens.py  search/serper_images.py
  search/hosting.py                   # SerpApi /image upload -> raw.githubusercontent fallback
  search/candidates.py                # social-host filter, URL canonicalisation, consensus, thumbnail fetch, face-verify, ranking, name heuristic
  extract/base.py  extract/og.py  extract/chrome.py  extract/platforms.py   # 3-tier transport + per-platform enrichers
  chain/contract.py                   # compile with solcx or load contracts/build/*.json; deploy
  chain/client.py                     # ChainClient: local | base-sepolia | sepolia; one send() helper; retries
  chain/ipfs.py                       # optional Pinata pin
  pipeline.py                         # orchestrates stages, writes evidence/<run_id>/
  api.py                              # (§7 only) FastAPI + SSE
verify.py                             # standalone keyless verifier (web3 + stdlib only)
contracts/FaceAnchorRegistry.sol  contracts/build/FaceAnchorRegistry.json (abi + bytecode, committed)
deployments/base-sepolia.json  deployments/sepolia.json   # address, deploy tx, block, chain id, solc version
models/  (gitignored except README)   demo/input_*.jpg  demo/README.md (subject, source URL, licence)
evidence/demo/<run_id>/               # ONE sanitized committed run
tests/  .github/workflows/ci.yml  run.ps1  run.sh  Makefile  .env.example  requirements.txt  README.md  LICENSE  docs/
```

### 4.2 Stage contracts (each stage reads/writes only `evidence/<run_id>/`, so stages are independently re-runnable)

**scan** `(image path | --webcam, engine) → face.json + face_crop.jpg + input.jpg copy`
- Load with OpenCV (BGR). Upscale images whose shorter side < 320 px by 2–4× (INTER_CUBIC); downscale long side > 2000 px.
- insightface: `FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'], allowed_modules=['detection','recognition'])`, `prepare(ctx_id=-1, det_thresh=0.5, det_size=(640,640))`, `faces = app.get(img)`; use `face.normed_embedding`, `face.bbox`, `face.det_score`, `face.kps`.
- SFace: `cv2.FaceDetectorYN.create(model, "", (320,320), score_threshold=0.7)`, **call `setInputSize((w,h))` per image**, `cv2.FaceRecognizerSF.create(model, "")`, `alignCrop` → `feature` → cosine (same-person ≥ 0.363). Expose the same Protocol.
- Require ≥1 face (else exit 3 NO_FACE); select the largest bbox; warn if >1.
- `--webcam`: `cv2.VideoCapture(0)`, live preview window, SPACE captures, ESC aborts; saved as input.jpg. This is the literal "face scan" input.
- Fingerprint: `q = np.round(normed_emb*127).astype(np.int8)`; `commitment = sha256(b"faceanchor-v1" + salt32 + q.tobytes())`. `face.json` holds salt (hex), q (list), bbox, det_score, engine, model file sha256s; **`face.json` and `embedding.npy` are gitignored** except in the sanitized demo run (strip salt + vector there, keep commitment).
- Also compute `input_sha256` (file bytes) and `input_phash` (imagehash.phash, 64-bit hex).

**search** `(run) → search/<engine>.raw.json, search/hosting.json, candidates.json`
- Query image = face crop with ~40% margin, JPEG q90, ≤ 900 px, < 500 KB.
- Hosting (`search/hosting.py`): `POST https://serpapi.com/image` multipart field `image` + `api_key` → `image_id` (expires ~10 min) → pass `image_id=`; fallback `--image-url` pointing at `https://raw.githubusercontent.com/<user>/faceanchor/main/demo/<file>` (commit demo images early). Record which was used.
- Engine 1 (mandatory): SerpApi `GET https://serpapi.com/search?engine=google_lens&{url|image_id}=…&type=visual_matches&hl=en&country=us&api_key=…` then `type=exact_matches`; merge, dedupe by link. Fields: `search_metadata.id`, `search_metadata.created_at`, `visual_matches[]{position,title,link,source,thumbnail,image}`. Quota: `GET https://serpapi.com/account?api_key=…` before/after (`plan_searches_left`/`total_searches_left`). Fallback: SearchAPI.io `GET https://www.searchapi.io/api/v1/search?engine=google_lens&url=…&search_type=visual_matches&link=resolved&api_key=…` (URL input only).
- Optional consensus engines (flag `--engines lens,bing,yandex`, run in threads, any error degrades gracefully): SerpApi `engine=bing_reverse_image&image_url=…` → `pages_with_this_image[]{title,link,thumbnail}`; SerpApi `engine=yandex_images&url=…` → `image_results[]{title,link,thumbnail,original}`. Compute `engines_agreeing` per canonical URL.
- Social filter regex on `link`: `(instagram\.com|x\.com|twitter\.com|facebook\.com|reddit\.com|tiktok\.com|youtube\.com|youtu\.be|linkedin\.com|threads\.(net|com)|pinterest\.com)`; canonicalise (strip query/fragment, `twitter.com`→`x.com`, `m.`/`mobile.` hosts). Non-social hits stay in raw JSON, tagged `other`, not verified.
- For every social candidate: download `thumbnail` (gstatic/bing/yandex CDN; no login), embed **all** faces, `similarity = max cosine`; verdicts: `MATCH ≥ 0.40` (`strong ≥ 0.50`), `WEAK 0.30–0.40`, `REJECT < 0.30`, `NO_FACE`, `FETCH_FAIL`. Sort by (verdict, similarity, engines_agreeing). Print a rich table with ALL rows (rejects included), threshold, engine, metric. Save `candidates.json` and every thumbnail as `thumb_<rank>.jpg` with its sha256.
- Hop 2 (only if no candidate ≥ 0.40): name = majority vote over capitalised 2–3-grams in Lens titles (drop stopwords like "Instagram", "Photos"); query SerpApi `engine=google_images&q="<Name>" site:<platform>` for instagram.com, x.com, linkedin.com/posts, youtube.com, reddit.com (fallback Serper `POST https://google.serper.dev/images {"q":…,"num":20}` header `X-API-KEY` → `images[]{title,imageUrl,thumbnailUrl,link,source}`); face-verify `thumbnail`s the same way; tag `engine="…(hop2)"`.
- Still nothing ≥ 0.40 → print the full table, write files, **exit 2 NO_MATCH**. Never a curated URL.
- Cache: key `(engine, query-image sha256, type)` → reuse raw JSON in dev so reruns cost no quota (SerpApi cached identical searches are free anyway). Budget: ≤ 60 dev searches, keep ≥ 30 for recording day.

**extract** `(best candidate) → post.json, post_image.jpg, post_screenshot.png?`
- Tier 1: `requests.get(url, headers={User-Agent: Chrome UA, Accept-Language: en-US}, timeout=20)` → `og:image/og:title/og:description/og:url`, JSON-LD, `itemprop` dates. Enrichers: LinkedIn (JSON-LD `datePublished`; `activity_id >> 22` ms epoch), YouTube (`https://www.youtube.com/oembed?url=…&format=json`, `itemprop="uploadDate"`, `https://img.youtube.com/vi/<id>/maxresdefault.jpg` → `hqdefault.jpg`), Reddit (`https://www.reddit.com/oembed?url=…` + `<post>.rss` once; `i.redd.it` image; never `.json`), Instagram (OG page → `/p/<code>/embed/captioned/` → Chrome; handle+date regex on og:description `- (\S+) on ([A-Z][a-z]+ \d{1,2}, \d{4}): "(.*)"`), X (`https://api.fxtwitter.com/2/status/<id>` → `tweet.text/author.screen_name/created_at/media.photos[].url`; snowflake time `((id>>22)+1288834974657)` ms), TikTok (`https://www.tiktok.com/oembed?url=…`; time `id >> 32` s).
- Tier 2: Playwright `p.chromium.launch(channel="chrome", headless=True)` (uses installed Chrome, no browser download), read the same meta tags, `page.screenshot()`; also use for Reddit's JS challenge.
- Tier 3: the search thumbnail already on disk; `image_source="search_thumbnail"`, `posted_at_source="unknown"`.
- Always hash image bytes at fetch time (CDN URLs are signed/expiring). Re-run face verification on the full-res post image; record the higher of thumbnail/full-res similarity with its source. Compute post image pHash and hamming distance vs input. Extraction never aborts the pipeline; every degradation is logged with a reason.

**anchor** `(run, chain) → record.json, record.sha256, anchor.json`
- `record.json` (canonical, this exact object is hashed):
```json
{"schema":"faceanchor.record/v1","run_id":"20260906T101500Z-3f9a1c","created_at":"2026-09-06T10:15:00Z",
 "input":{"file":"input.jpg","sha256":"<hex>","phash":"<hex16>","width":1024,"height":1365,"source":"upload|webcam"},
 "face":{"engine":"insightface/buffalo_l","model_files":{"det_10g.onnx":"<sha256>","w600k_r50.onnx":"<sha256>"},"bbox":[x1,y1,x2,y2],"det_score":0.93,"embedding_dim":512,"commitment":"<hex>","commitment_scheme":"sha256(faceanchor-v1||salt32||int8(normed_emb*127))","threshold":{"match":0.40,"weak":0.30,"metric":"cosine"}},
 "search":{"hosting":"serpapi_upload|github_raw","engines":[{"name":"serpapi.google_lens","search_id":"…","created_at":"…","raw_sha256":"…","candidates":18}],"quota_before":231,"quota_after":229,"candidates_sha256":"<sha256 of candidates.json>","social_candidates":9,
           "candidates":[{"rank":1,"platform":"instagram","url":"…","similarity":0.5812,"verdict":"MATCH","engines_agreeing":2,"thumbnail_sha256":"…"},{"rank":2,"platform":"x","url":"…","similarity":0.2140,"verdict":"REJECT","engines_agreeing":1,"thumbnail_sha256":"…"}]},
 "post":{"platform":"instagram","url":"…","canonical_url":"…","url_sha256":"…","author":"…","caption_excerpt":"first 140 chars","caption_sha256":"…","posted_at":"2026-09-04T16:00:03Z","posted_at_source":"exact|derived_from_id|approx|unknown","image_url":"…","image_sha256":"…","image_phash":"…","phash_hamming_vs_input":6,"image_source":"post_og|embed|oembed|browser|search_thumbnail","similarity":0.6123,"similarity_tier":"strong"},
 "chain_intent":{"chain":"base-sepolia","chain_id":84532,"contract":"0x…"}}
```
- Canonical bytes = `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode()`; floats rounded to 4 dp; nulls dropped. `recordHash = sha256(canonical bytes)` (SHA-256, not keccak, so `sha256sum record.json` reproduces it; write record.json as exactly those bytes).
- `evidenceUri = "sha256:<hex of record.json>"`, or `ipfs://<cid>` if `--pin` and `PINATA_JWT` set (`POST https://uploads.pinata.cloud/v3/files`, Bearer JWT, multipart `file`, form `network=public` → `data.cid`; non-fatal on failure).
- Call `anchor(recordHash, inputImageSha256, faceCommitment, postUrlHash=sha256(canonical_url), postImageSha256, inputPHash(uint64), similarityBps(uint16), evidenceUri)`. Idempotent: if `exists(recordHash)` print the existing block and skip.
- `anchor.json` (outside the hashed record): chain, chain_id, contract, tx_hash, block_number, block_timestamp, gas_used, explorer_tx_url, explorer_address_url, decoded `Anchored` event.

**verify** `(record.json [+anchor.json | --tx | --record-hash], chain) → verify_log.txt, exit code`
- Recompute from files: input.jpg sha256 + pHash, post_image.jpg sha256, caption sha256, canonical record hash; commitment from `face.json` salt + int8 vector if present (`--biometric`: re-embed input.jpg, cosine vs stored vector ≥ 0.40).
- Read chain twice: `get(recordHash)` + `verify(...)` via eth_call AND the `Anchored` log (from the tx receipt if `--tx`, else `get_logs` from the deploy block). Print a field-by-field table (local vs on-chain, PASS/FAIL), block number, explorer link. Exit 0 VERIFIED, 2 MISMATCH, 4 NOT_FOUND/CHAIN_UNREACHABLE (never fake PASS from cache).
- `tamper-demo --field caption|post_url|post_image|input_image`: copy the run dir, mutate one byte/char, rerun verify, show the diff and `exists(tamperedHash)==false`, exit 2.
- `verify.py` at repo root: same logic, imports only `web3`, `hashlib`, `json`, `argparse`; reads `deployments/<chain>.json` for the ABI/address; documented as "verify our run yourself" (works from a fresh clone with only `pip install web3==7.16.0`).

### 4.3 Contract (`contracts/FaceAnchorRegistry.sol`, solc 0.8.26, optimizer 200, compiled once with py-solc-x; commit `contracts/build/FaceAnchorRegistry.json`)
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;
/// @title FaceAnchorRegistry - tamper-evident registry of face-match evidence records (hashes only, no biometrics)
contract FaceAnchorRegistry {
    struct Record {
        bytes32 inputImageSha256; bytes32 faceCommitment; bytes32 postUrlHash; bytes32 postImageSha256;
        uint64 inputPHash; uint16 similarityBps; uint64 anchoredAt; address submitter; string evidenceUri;
    }
    mapping(bytes32 => Record) private records;   // key: recordHash = sha256(canonical record.json)
    bytes32[] public recordIds;
    event Anchored(bytes32 indexed recordHash, bytes32 indexed postUrlHash, bytes32 inputImageSha256, bytes32 faceCommitment,
        bytes32 postImageSha256, uint64 inputPHash, uint16 similarityBps, address indexed submitter, uint64 anchoredAt, string evidenceUri);
    function anchor(bytes32 recordHash, bytes32 inputImageSha256, bytes32 faceCommitment, bytes32 postUrlHash,
        bytes32 postImageSha256, uint64 inputPHash, uint16 similarityBps, string calldata evidenceUri) external returns (uint256 index) {
        require(recordHash != bytes32(0), "zero hash");
        require(records[recordHash].anchoredAt == 0, "exists");
        records[recordHash] = Record(inputImageSha256, faceCommitment, postUrlHash, postImageSha256, inputPHash, similarityBps, uint64(block.timestamp), msg.sender, evidenceUri);
        recordIds.push(recordHash);
        emit Anchored(recordHash, postUrlHash, inputImageSha256, faceCommitment, postImageSha256, inputPHash, similarityBps, msg.sender, uint64(block.timestamp), evidenceUri);
        return recordIds.length - 1;
    }
    function exists(bytes32 recordHash) external view returns (bool) { return records[recordHash].anchoredAt != 0; }
    function get(bytes32 recordHash) external view returns (Record memory) { return records[recordHash]; }
    function count() external view returns (uint256) { return recordIds.length; }
    function verify(bytes32 recordHash, bytes32 inputImageSha256, bytes32 faceCommitment, bytes32 postUrlHash, bytes32 postImageSha256)
        external view returns (bool ok, bool found, bool imageOk, bool faceOk, bool postOk, bool postImageOk) {
        Record memory r = records[recordHash];
        found = r.anchoredAt != 0; imageOk = r.inputImageSha256 == inputImageSha256; faceOk = r.faceCommitment == faceCommitment;
        postOk = r.postUrlHash == postUrlHash; postImageOk = r.postImageSha256 == postImageSha256;
        ok = found && imageOk && faceOk && postOk && postImageOk;
    }
    function hamming(uint64 a, uint64 b) external pure returns (uint8 d) { uint64 x = a ^ b; while (x != 0) { d++; x &= x - 1; } }
}
```

### 4.4 Chain layer (`chain/client.py`)
- `local`: `Web3(EthereumTesterProvider())`, unlocked `w3.eth.accounts[0]`, `.transact({'from': …})`. Real in-process EVM; used by tests and as the guaranteed on-camera fallback.
- `base-sepolia` (chainId 84532) / `sepolia` (11155111): `HTTPProvider(rpc)` with an RPC list and retry/backoff; sign raw txs with `PRIVATE_KEY` (`w3.eth.account.sign_transaction(tx, key)` → `signed.raw_transaction`), EIP-1559 fields, `wait_for_transaction_receipt(timeout=180)`. One `send(fn)` helper shared by both modes.
- `deploy --chain X` writes `deployments/X.json` (address, deploy tx, block, chain id, solc version, abi sha256) and is committed. `CONTRACT_ADDRESS` env overrides.
- Compile with `solcx.install_solc("0.8.26")` once; always load the committed build JSON at runtime so the demo never depends on a solc download.
- Optional (≤30 min, Sept 7): verify source on base-sepolia.blockscout.com (no API key) and link it.

### 4.5 Genuineness evidence (design it in, show it on camera)
run_id + UTC timestamps on every stage; provider `search_id` + `created_at` + quota before/after printed; raw JSON on disk; candidate table with REJECT/NO_FACE rows; the matched post opened in a real browser; SerpApi dashboard "Searches" page showing the same id; Basescan tx page live; `verify.py` from a fresh terminal; on-camera edit → MISMATCH; a second input image producing different candidates.

### 4.6 CLI UX (typer + rich)
Stage panels (SCAN / SEARCH / EXTRACT / ANCHOR / VERIFY) with elapsed time, progress bars for downloads/tx wait, colored verdicts, explorer URLs as clickable links, `--json` for machine output. Exit codes: 0 ok, 2 NO_MATCH/MISMATCH, 3 NO_FACE, 4 CHAIN error, 5 PROVIDER error. `run` = all stages; each stage is resumable via `--run <id>`.

## 5. Tests, CI, repo hygiene
- pytest 10–15 focused tests, no keys, no model downloads: canonical JSON determinism (byte-identical across two builds), sha256/pHash helpers, int8 quantization + commitment, social filter + URL canonicalisation + consensus, candidate ranking on fixture JSON, extractor parsers on saved HTML fixtures, contract anchor/exists/verify/hamming/tamper on eth-tester, `verify.py` exit codes.
- `.github/workflows/ci.yml`: ubuntu-latest + windows-latest, Python 3.12, `pip install -r requirements-ci.txt` (chain + hashing deps only), `pytest -q`. Badge in README.
- `.gitignore`: `.env`, `.venv`, `models/*.onnx`, `evidence/*/face.json`, `evidence/*/embedding.npy`, `__pycache__`, `ui/node_modules`, `ui/dist`.
- `.env.example` with every variable and a comment. `run.ps1`, `run.sh`, `Makefile` (`make demo` = local chain run, `make verify`).
- Before submission: `git log -p | Select-String -Pattern "SERPAPI|SEARCHAPI|SERPER|PRIVATE_KEY|JWT"` must be empty.

## 6. README (≤ 200 lines; literal headings) and demo storyboard
README order: title + one-line pitch; badges (CI, Python 3.12, Base Sepolia, MIT); demo video link + one screenshot of the VERIFIED screen; **What it does** (3 bullets mapped to the 3 requirements); mermaid flowchart; **How to run** (clone, venv, pip, `.env`, `python -m faceanchor run --image demo/input_1.jpg --chain local`, then `--chain base-sepolia`); Configuration table; **Which blockchain** (network, chain id, contract address, deploy tx, demo tx, explorer links, what is / is not stored on-chain); Evidence bundle format; **Verify our run yourself** (one command + expected output); Tamper demo; How the search is genuinely performed; Face matching (model, metric, threshold, sample table incl. rejects); Tests & CI; **Known limitations** (thumbnail resolution, public figures only, platform login walls, no liveness, testnet, insightface model licence non-commercial research, SerpApi quota); **Ethics & privacy** (only hashes/commitments on-chain, embeddings never leave the machine, public-figure/consent policy, DPDP note, takedown contact); Repo layout; Credits. No emoji-per-heading, no inflated claims; every number in the README must match a linked artifact.

Recording (one unedited 1080p take, ≤ 4:30, mic on, hotspot, `.env` never on screen):
0:00 README top + mermaid, say the stack in one sentence. 0:20 terminal: `date`, `git log --oneline | head -15`, open `demo/input_1.jpg`, run `python -m faceanchor run --image demo/input_1.jpg --chain base-sepolia --engines lens,bing,yandex`. 0:40 SCAN panel (bbox, det_score, 512-d, pHash, commitment). 1:00 SEARCH: search_id + quota, candidate table streaming with REJECT rows and one MATCH ≥ 0.40; alt-tab to the post in Chrome and to the SerpApi dashboard showing the same search_id. 2:00 EXTRACT + ANCHOR: author/date/caption, canonical record + sha256, tx hash, block; open Basescan live and point at the `Anchored` event hash. 2:45 fresh terminal `python verify.py --record … --chain base-sepolia` → VERIFIED exit 0; edit one character on camera → MISMATCH exit 2 with field diff. 3:30 `tamper-demo --field post_image`, `tree evidence/<run>`, README "Which blockchain" + "Known limitations" + "Ethics" read in one sentence each. End. Rehearse 3 times; if Base RPC fails live, say so and rerun `--chain local`.

## 7. Optional UI (time-boxed: Sept 7 12:00–15:30 IST, only if the CLI recording already exists)
`ui/` = Vite 8 + React 19 + Tailwind 4 (`@tailwindcss/vite`) + `motion` + `lucide-react` + four React Bits components copied from reactbits.dev (Stepper, SpotlightCard, CountUp, DecryptedText). `faceanchor serve` runs FastAPI: `POST /api/runs` (multipart image or base64 webcam frame, chain, engines) → run_id; `GET /api/runs/{id}/events` SSE (`stage|log|candidate|match|record|tx|verified|error`, replay buffer); `GET /api/runs/{id}/files/{name}`; `POST /api/runs/{id}/verify`, `/tamper`. Single dark screen: webcam capture/drop zone → Stepper → CandidateGrid (rejected cards greyed but visible, CountUp similarity, engines pips) → AnchorPanel (DecryptedText hash + explorer button) → VerifyPanel (field table, tamper button). Strictly a viewer of the same StageEvents the CLI prints; record split-screen with the terminal visible. Test it with the Chrome DevTools MCP. Hard stop at 15:30 regardless of state; if incomplete, ship the CLI recording.

## 8. Milestones and checkpoints (IST)
- **M0 Sept 5 (user, parallel)** keys, faucets, hotspot, subjects, teammates.
- **M1 Sept 5 evening** repo (`gh repo create faceanchor --public`), `.gitignore` first, venv, pinned install (paste pip output showing the pure wheels), model downloads, `scan` for both engines, commits. **Checkpoint A:** scan prints bbox/det_score/512-d/pHash/commitment; same-person pair ≥ 0.40, different-person < 0.30 (use two Wikimedia photos each).
- **M2 Sept 5 late** SerpApi Lens adapter + hosting + social filter + thumbnail verification + rich table + raw/quota logging + cache. **Checkpoint B (go/no-go on subject):** ≥ 1 social candidate ≥ 0.40 for a public-figure portrait; try up to 3 subjects within 15 searches; report the table.
- **M3 Sept 6 morning** exact_matches merge, SearchAPI fallback, Bing/Yandex consensus, hop-2, NO_MATCH exit 2, second-image non-hardcoding check.
- **M4 Sept 6 midday** extraction tiers + enrichers + full-res re-verification + hamming.
- **M5 Sept 6 midday** canonical record + hashing + determinism tests.
- **M6 Sept 6 afternoon** contract, build JSON, local chain, anchor/verify/tamper-demo, verify.py, eth-tester tests. **Checkpoint C:** `run --chain local` → VERIFIED; `tamper-demo` → MISMATCH exit 2.
- **M7 Sept 6 afternoon** deploy to Base Sepolia, commit deployments, real anchor, Basescan shows `Anchored` with the printed hash. **Checkpoint D:** fresh clone + `verify.py` only → VERIFIED.
- **M8 Sept 6 evening** CLI polish, run scripts, `.env.example`, README skeleton, sanitized `evidence/demo/`.
- **M9 Sept 6 night** 3 rehearsals on hotspot → **record Take 1 (CLI only)**, upload unlisted, test link in incognito. **Checkpoint E:** submittable recording exists.
- **M10 Sept 7 08:00–10:00** tests + CI green on ubuntu + windows.
- **M11 Sept 7 10:00–12:00** full README, docs/, secrets audit, Blockscout verification (≤ 30 min), commit cadence check (12+ commits, teammates included).
- **M12 Sept 7 12:00–15:30** UI (§7) only if Checkpoint E passed. **Feature freeze 15:30.**
- **M13 Sept 7 15:30–17:00** re-record only if UI is in and stable.
- **M14 Sept 7 by 19:00** final checklist: repo public, README links open in incognito, video public/unlisted, contract address/tx links correct, form filled with correct task + names/emails matching registration, screenshot of confirmation. 19:00–23:59 is buffer only.

## 9. Definition of done
- [ ] `run --chain local` and `run --chain base-sepolia` succeed end-to-end on this machine, twice in a row, on the hotspot.
- [ ] Basescan tx shows `Anchored(recordHash)` equal to `record.sha256`; `deployments/base-sepolia.json` committed.
- [ ] `verify.py` from a fresh clone → VERIFIED; on-camera edit → MISMATCH exit 2.
- [ ] Candidate table shows real REJECT rows; raw provider JSON with search ids committed for the demo run; no URL literals in `search/`.
- [ ] Second input yields different candidates; a private face yields honest NO_MATCH.
- [ ] pytest green locally and in CI on ubuntu + windows.
- [ ] README has the literal headings What it does / How to run / Which blockchain / Known limitations / Ethics & privacy; every claim links to an artifact.
- [ ] No secrets in git history; burner wallet only.
- [ ] Unedited ≤ 4:30 1080p recording uploaded and opened in incognito; form submitted before 19:00 Sept 7.

## 10. Anti-patterns from the rejected Task 2 (do the opposite)
Single-author commit graph in a team-gated selection; headline metric claimed with a simulated component; README numbers not matching linked reports; last-day commits that are marketing edits; no test CI; giant hand-off docs; features mocked for the demo; product not frozen before recording. Here: real stack only, artifacts back every number, tests + CI, freeze at 15:30 Sept 7, teammates commit, README ≤ 200 lines with essays in `docs/`.
