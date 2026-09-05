# FaceAnchor — your setup checklist (do tonight, ~45 min, in parallel with the build)

Everything below is something only you can do. Claude Code will build while you do it.

## 1. Search API keys (all free, no card)
- [ ] **SerpApi** — https://serpapi.com/users/sign_up → email + SMS verification → Dashboard → copy API key → `.env`: `SERPAPI_KEY=...` (250 searches/month; the primary engine). If SMS fails, do the next two immediately.
- [ ] **SearchAPI.io** — https://www.searchapi.io/ → email only → `.env`: `SEARCHAPI_KEY=...` (100 free; Lens fallback).
- [ ] **Serper.dev** — https://serper.dev/ → email only → `.env`: `SERPER_KEY=...` (2,500 free; name-search fallback).

## 2. Testnet wallet + funds
- [ ] Claude will print a fresh **burner address** and tell you where the private key goes (`.env` → `PRIVATE_KEY=0x...`). Never paste the key in chat, never use a wallet with real funds.
- [ ] **Base Sepolia (primary):** https://portal.cdp.coinbase.com/products/faucet → free Coinbase Developer Platform account → network Base Sepolia → paste the burner address → claim 3–5 times (0.0001 ETH each; gas is ~0.01 gwei so this covers dozens of transactions).
- [ ] **Ethereum Sepolia (backup):** https://cloud.google.com/application/web3/faucet/ethereum/sepolia → sign in with Google → 0.05 ETH to the same address.
- [ ] Optional: https://www.alchemy.com/ free key → `.env`: `RPC_URL_FALLBACK=https://base-sepolia.g.alchemy.com/v2/<key>`.
- [ ] Optional: https://app.pinata.cloud/ → API key (JWT) → `.env`: `PINATA_JWT=...` (IPFS pin of the evidence file; skip if short on time).

## 3. Network (critical)
Your home router runs a Sophos web filter (192.168.0.1:8090) that blocks Instagram, X, Facebook, TikTok, Pinterest for scripts. The pipeline survives it (thumbnail fallback), but the demo looks better and extraction works fully on a **phone hotspot or VPN**.
- [ ] Connect the laptop to a phone hotspot and run in PowerShell:
  ```
  curl.exe -I https://x.com
  ```
  It must NOT show `Location: https://192.168.0.1:8090/...`. Use the hotspot for the final recording.

## 4. Demo subjects
- [ ] Pick 3 public figures with lots of public social posts (e.g. Sundar Pichai, Satya Nadella, Virat Kohli, Elon Musk). For each, download one portrait from Wikimedia Commons (note the file URL and licence) into `demo/`. Claude will test which one the search finds most reliably.
- [ ] Optional: be ready to do a webcam scan of yourself on camera; a private face should honestly return NO_MATCH, which is itself good evidence of genuineness.

## 5. Recording setup
- [ ] Recorder: OBS Studio (https://obsproject.com/) or Win+G Game Bar, 1080p, mic on.
- [ ] Windows Terminal with a large font; Chrome tabs pre-opened: https://sepolia.basescan.org, https://serpapi.com/searches (your dashboard), the README.
- [ ] One unedited take ≤ 4:30. No cuts, no speed-up. Upload to YouTube (unlisted) or Google Drive (anyone with link) and open the link in an incognito window before submitting.

## 6. Team and submission rules
- [ ] Check https://hhgoa.com/task3 and the form (https://forms.gle/oZbQGuwiNeHVcHWo8) for whether **each team member must submit**. Task 2 was likely rejected partly because only one person committed.
- [ ] Give Claude every teammate's GitHub handle; each should land real, scoped commits (tests/CI, extractors, README).
- [ ] Names and emails on the form must match your registration; repo must be public; video link must be public/unlisted.

## 7. Timeline (IST)
- Sept 5 evening: keys + faucet + hotspot done; Claude finishes face engine + first live search.
- Sept 6: full pipeline on Base Sepolia, verify + tamper demo, **record Take 1 by night**.
- Sept 7: tests/CI, README, optional UI until 15:30, final checks, **submit by 19:00**. Do not touch the repo after submitting.
