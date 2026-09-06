import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import {
  Camera, CheckCircle2, ExternalLink, FileWarning, Fingerprint, Link2,
  Loader2, ScanFace, ShieldAlert, ShieldCheck, Upload, XCircle,
} from 'lucide-react'

import Stepper from './components/bits/Stepper.jsx'
import SpotlightCard from './components/bits/SpotlightCard.jsx'
import CountUp from './components/bits/CountUp.jsx'
import DecryptedText from './components/bits/DecryptedText.jsx'

const STEPS = ['scan', 'search', 'extract', 'prove', 'anchor', 'verify']
// Which finished artifact proves which stage ran, for the stepper.
const ARTIFACT_STAGE = [
  ['face', 'scan'], ['candidates', 'search'], ['post', 'extract'],
  ['zk', 'prove'], ['anchor', 'anchor'], ['verify', 'verify'],
]
const VERDICT = {
  MATCH: { color: 'text-good', ring: 'border-good/50', label: 'match' },
  WEAK: { color: 'text-warn', ring: 'border-warn/40', label: 'weak' },
  REJECT: { color: 'text-bad', ring: 'border-bad/30', label: 'rejected' },
  NO_FACE: { color: 'text-mute', ring: 'border-edge', label: 'no face' },
  FETCH_FAIL: { color: 'text-mute', ring: 'border-edge', label: 'unreachable' },
}

function Section({ title, aside, children }) {
  return (
    <section className="mb-6">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-[0.18em] text-mute">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  )
}

function Row({ label, value, mono = true }) {
  if (value === undefined || value === null || value === '') return null
  return (
    <div className="flex gap-3 py-1 text-sm">
      <span className="w-36 shrink-0 text-mute">{label}</span>
      <span className={`${mono ? 'mono hash' : ''} text-[13px] leading-relaxed`}>{value}</span>
    </div>
  )
}

export default function App() {
  const [status, setStatus] = useState(null)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState('')
  const [chain, setChain] = useState('base-sepolia')
  const [engines, setEngines] = useState('lens')
  const [maxCandidates, setMaxCandidates] = useState('20')
  const [runId, setRunId] = useState('')
  const [events, setEvents] = useState([])
  const [candidates, setCandidates] = useState([])
  const [state, setState] = useState({})
  const [running, setRunning] = useState(false)
  const [failedStep, setFailedStep] = useState(-1)
  const [camOn, setCamOn] = useState(false)
  const [tamperReport, setTamperReport] = useState(null)
  const [forge, setForge] = useState(null)
  const [forging, setForging] = useState(false)
  const [forgeError, setForgeError] = useState('')
  const videoRef = useRef(null)
  const logRef = useRef(null)

  useEffect(() => {
    fetch('/api/status').then((r) => r.json()).then(setStatus).catch(() => {})
  }, [])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [events])

  const currentStep = useMemo(() => {
    // Count only the stages that are actually in the stepper. `forge` and
    // `replicate` also emit stage_end, and counting those would push the bar
    // past the end of the run they belong to.
    const seen = new Set(events
      .filter((e) => e.kind === 'stage_end' && STEPS.includes(e.stage))
      .map((e) => e.stage))
    // The artifacts a run wrote are the ground truth for how far it got. Going
    // by stage_end events alone left a *finished* run pinned on ANCHOR whenever
    // one of those events was missed, which reads as a hang rather than a
    // dropped frame -- and cost fifteen minutes of waiting for a run that had
    // been over for three.
    for (const [key, stage] of ARTIFACT_STAGE) if (state[key]) seen.add(stage)
    return Math.min(STEPS.length, seen.size)
  }, [events, state])

  // --- webcam ---------------------------------------------------------------

  const startCam = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280 } })
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
      }
      setCamOn(true)
    } catch {
      alert('Could not open the camera. Upload a photo instead.')
    }
  }

  const capture = () => {
    const v = videoRef.current
    if (!v) return
    const canvas = document.createElement('canvas')
    canvas.width = v.videoWidth
    canvas.height = v.videoHeight
    canvas.getContext('2d').drawImage(v, 0, 0)
    canvas.toBlob((blob) => {
      const f = new File([blob], 'webcam.jpg', { type: 'image/jpeg' })
      setFile(f)
      setPreview(URL.createObjectURL(f))
      v.srcObject?.getTracks().forEach((t) => t.stop())
      setCamOn(false)
    }, 'image/jpeg', 0.95)
  }

  const pick = (f) => {
    if (!f) return
    setFile(f)
    setPreview(URL.createObjectURL(f))
  }

  // --- run ------------------------------------------------------------------

  const start = useCallback(async () => {
    if (!file || running) return
    setRunning(true)
    setEvents([])
    setCandidates([])
    setState({})
    setTamperReport(null)
    setForge(null)
    setForgeError('')
    setFailedStep(-1)

    const body = new FormData()
    body.append('image', file)
    body.append('chain', chain)
    body.append('engines', engines)
    body.append('max_candidates', maxCandidates)

    const res = await fetch('/api/runs', { method: 'POST', body })
    const { run_id } = await res.json()
    setRunId(run_id)

    const es = new EventSource(`/api/runs/${run_id}/events`)
    const push = (kind) => (e) => {
      const data = JSON.parse(e.data)
      setEvents((prev) => [...prev, { ...data, kind }])
      if (kind === 'candidate') {
        setCandidates((prev) => {
          const next = prev.filter((c) => c.url !== data.data.url)
          return [...next, data.data].sort(
            (a, b) => (b.similarity ?? -1) - (a.similarity ?? -1),
          )
        })
      }
      if (kind === 'error') setFailedStep(STEPS.indexOf(data.stage))
    }
    for (const kind of ['stage_start', 'stage_end', 'log', 'candidate', 'record',
      'tx', 'verified', 'error']) {
      es.addEventListener(kind, push(kind))
    }
    es.addEventListener('done', async () => {
      es.close()
      setRunning(false)
      const s = await fetch(`/api/runs/${run_id}`).then((r) => r.json())
      setState(s)
    })
    // A dropped stream must not look like a finished run: say so, then rejoin.
    // The server replays everything, so nothing is missed on reconnect.
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) {
        setEvents((prev) => [...prev, {
          kind: 'log', stage: 'stream', ts: new Date().toISOString(),
          message: 'event stream dropped, reconnecting...',
        }])
        setTimeout(() => {
          const again = new EventSource(`/api/runs/${run_id}/events`)
          for (const kind of ['stage_start', 'stage_end', 'log', 'candidate',
            'record', 'tx', 'verified', 'error']) {
            again.addEventListener(kind, push(kind))
          }
          again.addEventListener('done', async () => {
            again.close()
            setRunning(false)
            setState(await fetch(`/api/runs/${run_id}`).then((r) => r.json()))
          })
        }, 1200)
      }
    }
  }, [file, chain, engines, maxCandidates, running])

  const tamper = async (field) => {
    const body = new FormData()
    body.append('field', field)
    const r = await fetch(`/api/runs/${runId}/verify`, { method: 'POST', body })
    setTamperReport(await r.json().catch(() => null))
  }

  const verifyReport = state.verify
  // The claim only holds if the honest similarity is the *only* one taken.
  const forgeHeld = !!forge && forge.attempts?.[0]?.accepted
    && !forge.attempts.slice(1).some((a) => a.accepted)
  const anchor = state.anchor
  const post = state.post
  const face = state.face
  const zk = state.zk
  const [replication, setReplication] = useState(null)
  const [replicating, setReplicating] = useState(false)

  return (
    <div className="mx-auto max-w-[1240px] px-6 py-8">
      <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-2xl font-semibold tracking-tight">
            <ScanFace className="text-accent" size={26} />
            FaceAnchor
          </h1>
          <p className="mt-1 text-sm text-mute">
            A face scan becomes a web search, the match becomes an evidence
            bundle, and its hash becomes an immutable on-chain record.
          </p>
        </div>
        {status && (
          <div className="flex flex-wrap gap-2 text-[11px] text-mute">
            {[['Google Lens', status.serpapi], ['wallet', status.wallet]].map(([k, ok]) => (
              <span key={k}
                className={`rounded-full border px-2.5 py-1 ${ok ? 'border-good/40 text-good' : 'border-edge'}`}>
                {k} {ok ? 'ready' : 'not configured'}
              </span>
            ))}
            {status.quota?.searches_left != null && (
              <span className="rounded-full border border-edge px-2.5 py-1">
                {status.quota.searches_left} searches left
              </span>
            )}
          </div>
        )}
      </header>

      <div className="mb-7 rounded-xl border border-edge bg-panel/60 px-5 py-4">
        <Stepper steps={STEPS} current={currentStep} failed={failedStep} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[380px_1fr]">
        {/* -------- left column: input and live log -------- */}
        <div>
          <Section title="face scan input">
            <SpotlightCard className="p-4">
              <div className="relative mb-3 grid aspect-[4/3] place-items-center overflow-hidden rounded-lg border border-edge bg-ink/70">
                {camOn ? (
                  <video ref={videoRef} className="h-full w-full object-cover" muted playsInline />
                ) : preview ? (
                  <img src={preview} alt="input" className="h-full w-full object-cover" />
                ) : (
                  <label className="grid cursor-pointer place-items-center gap-2 text-mute">
                    <Upload size={22} />
                    <span className="text-xs">drop a photo or click to choose</span>
                    <input type="file" accept="image/*" className="hidden"
                      onChange={(e) => pick(e.target.files?.[0])} />
                  </label>
                )}
              </div>
              <div className="flex gap-2">
                {camOn ? (
                  <button onClick={capture}
                    className="flex-1 rounded-lg bg-accent/90 px-3 py-2 text-sm font-medium text-ink">
                    capture
                  </button>
                ) : (
                  <button onClick={startCam}
                    className="flex items-center gap-2 rounded-lg border border-edge px-3 py-2 text-sm hover:border-accent/60">
                    <Camera size={15} /> webcam
                  </button>
                )}
                <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-edge px-3 py-2 text-sm hover:border-accent/60">
                  <Upload size={15} /> file
                  <input type="file" accept="image/*" className="hidden"
                    onChange={(e) => pick(e.target.files?.[0])} />
                </label>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
                <label className="text-mute">
                  chain
                  <select value={chain} onChange={(e) => setChain(e.target.value)}
                    className="mt-1 w-full rounded-md border border-edge bg-ink px-2 py-1.5 text-sm text-white">
                    <option value="base-sepolia">Base Sepolia</option>
                    <option value="sepolia">Ethereum Sepolia</option>
                    <option value="local">local EVM (no explorer link)</option>
                  </select>
                </label>
                <label className="text-mute">
                  engines
                  <select value={engines} onChange={(e) => setEngines(e.target.value)}
                    className="mt-1 w-full rounded-md border border-edge bg-ink px-2 py-1.5 text-sm text-white">
                    <option value="lens">Google Lens</option>
                    <option value="lens,bing">Lens + Bing</option>
                    <option value="lens,bing,yandex">Lens + Bing + Yandex</option>
                  </select>
                </label>
                <label className="text-mute">
                  candidates scored
                  <select value={maxCandidates} onChange={(e) => setMaxCandidates(e.target.value)}
                    className="mt-1 w-full rounded-md border border-edge bg-ink px-2 py-1.5 text-sm text-white">
                    <option value="12">12 — fastest</option>
                    <option value="20">20</option>
                    <option value="40">40 — most thorough</option>
                  </select>
                </label>
              </div>
              <p className="mt-1.5 text-[10px] leading-relaxed text-mute">
                Every candidate costs a download and a face detection, so this is
                what a run's length is made of. Anything past the cap still
                appears, marked skipped — a truncated list must not read as full
                coverage.
              </p>

              <button onClick={start} disabled={!file || running}
                className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 font-medium text-ink disabled:cursor-not-allowed disabled:opacity-40">
                {running ? <><Loader2 className="animate-spin" size={16} /> running</>
                  : <><Fingerprint size={16} /> run the pipeline</>}
              </button>
            </SpotlightCard>
          </Section>

          <Section title="live log" aside={runId && <span className="mono text-[11px] text-mute">{runId}</span>}>
            <div ref={logRef}
              className="mono h-64 overflow-y-auto rounded-xl border border-edge bg-ink/60 p-3 text-[11px] leading-relaxed">
              {events.length === 0 && <span className="text-mute">waiting for a run…</span>}
              {events.map((e, i) => (
                <div key={i} className={
                  e.kind === 'error' ? 'text-bad'
                    : e.kind === 'stage_start' ? 'mt-2 text-accent'
                    : e.kind === 'verified' ? 'text-good' : 'text-slate-300'}>
                  <span className="text-mute">{e.ts?.slice(11, 19)} </span>
                  {e.kind === 'candidate'
                    ? `${e.data.verdict.padEnd(10)} ${(e.data.similarity ?? -1).toFixed(4)}  ${e.data.url}`
                    : e.message}
                </div>
              ))}
            </div>
          </Section>
        </div>

        {/* -------- right column: results -------- */}
        <div>
          {face && (
            <Section title="1  face encoded">
              <SpotlightCard className="p-4">
                <div className="flex gap-4">
                  {runId && (
                    <img src={`/api/runs/${runId}/files/face_crop.jpg`} alt="face crop"
                      className="h-24 w-24 rounded-lg border border-edge object-cover" />
                  )}
                  <div className="min-w-0 flex-1">
                    <Row label="engine" value={`${face.engine}  (${face.model_id})`} mono={false} />
                    <Row label="confidence" value={face.det_score} />
                    <Row label="embedding" value={`${face.embedding_dim}-d, L2 normalised`} mono={false} />
                    <Row label="input sha256" value={face.input?.sha256} />
                    <Row label="commitment" value={<DecryptedText text={face.commitment} />} />
                  </div>
                </div>
                <p className="mt-3 text-[11px] text-mute">
                  Only this salted commitment is published. The embedding and its
                  salt stay on the machine that ran the scan.
                </p>
              </SpotlightCard>
            </Section>
          )}

          {candidates.length > 0 && (
            <Section
              title="2  candidates scored against the scanned face"
              aside={<span className="text-[11px] text-mute">
                {candidates.filter((c) => c.verdict === 'MATCH').length} match ·{' '}
                {candidates.filter((c) => c.verdict !== 'MATCH').length} rejected
              </span>}>
              <div className="grid gap-2.5 sm:grid-cols-2">
                <AnimatePresence initial={false}>
                  {candidates.map((c) => {
                    const v = VERDICT[c.verdict] ?? VERDICT.REJECT
                    return (
                      <motion.div key={c.url} layout
                        initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.25 }}>
                        <SpotlightCard className={`p-3 ${c.verdict === 'MATCH' ? 'border-good/50' : v.ring}`}
                          glow={c.verdict === 'MATCH' ? '56, 217, 150' : '125, 139, 161'}>
                          <div className="flex items-start justify-between gap-2">
                            <span className={`text-[11px] font-semibold uppercase tracking-wider ${v.color}`}>
                              {v.label}
                            </span>
                            <span className={`mono text-sm ${v.color}`}>
                              {c.similarity >= 0 ? <CountUp to={c.similarity} /> : '—'}
                            </span>
                          </div>
                          <a href={c.url} target="_blank" rel="noreferrer"
                            className="mt-1.5 block truncate text-[12px] text-slate-300 hover:text-accent">
                            {c.url}
                          </a>
                          <div className="mt-1.5 flex items-center gap-3 text-[10px] text-mute">
                            <span>{c.platform}</span>
                            <span>{c.faces_found} face{c.faces_found === 1 ? '' : 's'}</span>
                            <span>{c.engines_agreeing} engine{c.engines_agreeing === 1 ? '' : 's'}</span>
                          </div>
                        </SpotlightCard>
                      </motion.div>
                    )
                  })}
                </AnimatePresence>
              </div>
            </Section>
          )}

          {post && (
            <Section title="3  matched post">
              <SpotlightCard className="p-4">
                <div className="flex gap-4">
                  {runId && post.image_file && (
                    <img src={`/api/runs/${runId}/files/${post.image_file}`} alt="post"
                      className="h-28 w-28 rounded-lg border border-edge object-cover" />
                  )}
                  <div className="min-w-0 flex-1">
                    <Row label="platform" value={post.platform} mono={false} />
                    <Row label="author" value={post.author || 'not public'} mono={false} />
                    <Row label="posted" value={post.posted_at
                      ? `${post.posted_at}  (${post.posted_at_source})` : 'unknown'} mono={false} />
                    <Row label="image via" value={`${post.image_source} · ${post.extraction_method || 'n/a'}`} mono={false} />
                    <Row label="similarity" value={`${post.similarity} from ${post.similarity_source}`} />
                    <a href={post.canonical_url} target="_blank" rel="noreferrer"
                      className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-accent hover:underline">
                      <Link2 size={13} /> open the post
                    </a>
                  </div>
                </div>
                {post.caption && (
                  <p className="mt-3 border-t border-edge pt-3 text-[12px] text-slate-300">
                    {post.caption.slice(0, 260)}
                  </p>
                )}
              </SpotlightCard>
            </Section>
          )}

          {zk && (
            <Section title="4  proved without revealing the face">
              <SpotlightCard className="p-4" glow="167, 139, 250">
                <Row label="scheme" value={`${zk.scheme}  (${zk.dimensions}-d)`} mono={false} />
                <Row label="commit A" value={<DecryptedText text={String(zk.commitment_a).slice(0, 44)} />} />
                <Row label="commit B" value={<DecryptedText text={String(zk.commitment_b).slice(0, 44)} />} />
                <Row label="dot product" value={zk.dot} />
                <Row label="norms" value={`|A|^2 ${zk.norm_a}   |B|^2 ${zk.norm_b}`} mono={false} />
                <Row
                  label="similarity"
                  value={<span className="text-good"><CountUp to={zk.similarity} decimals={4} /> proven</span>}
                  mono={false}
                />
                <p className="mt-3 text-[11px] leading-relaxed text-mute">
                  <span className="text-good">Proves</span> the dot product and norms above belong to the
                  two committed embeddings, so the similarity written on-chain cannot be inflated.
                </p>
                <p className="mt-1 text-[11px] leading-relaxed text-mute">
                  <span className="text-warn">Does not prove</span> those embeddings came from running the
                  face model on the two images &mdash; that would need the model itself inside the circuit.
                </p>
              </SpotlightCard>
            </Section>
          )}

          {zk && (
            <Section title="4b  re-derive it yourself">
              <SpotlightCard className="p-4" glow="167, 139, 250">
                <p className="text-[12px] leading-relaxed text-mute">
                  The post image is public, so its salt is published too. Anyone can run the
                  same model over it and reproduce the committed vector &mdash; no secret, no
                  proving key, no trust in us.
                </p>
                <button
                  type="button"
                  disabled={replicating}
                  onClick={async () => {
                    setReplicating(true)
                    try {
                      const body = new FormData()
                      body.append('live', 'false')
                      const r = await fetch(`/api/runs/${runId}/replicate`, { method: 'POST', body })
                      setReplication(await r.json())
                    } catch {
                      setReplication({ verdict: 'ERROR', checks: [] })
                    } finally {
                      setReplicating(false)
                    }
                  }}
                  className="mt-3 rounded-md border border-edge px-3 py-1.5 text-[12px]
                             hover:border-accent disabled:opacity-50"
                >
                  {replicating ? 're-deriving...' : 're-derive the post face'}
                </button>
                {replication && (
                  <div className="mt-3 space-y-1">
                    {(replication.checks || []).map((c) => (
                      <Row
                        key={c.check}
                        label={c.check}
                        mono={false}
                        value={
                          <span className={
                            c.state === 'PASS' ? 'text-good'
                              : c.state === 'FAIL' ? 'text-bad' : 'text-mute'
                          }>{c.state}</span>
                        }
                      />
                    ))}
                    <p className={`mt-2 text-[12px] ${
                      replication.verdict === 'REPLICATED' ? 'text-good' : 'text-bad'}`}>
                      {replication.verdict}
                    </p>
                  </div>
                )}
              </SpotlightCard>
            </Section>
          )}

          {anchor && (
            <Section title="5  anchored on-chain">
              <SpotlightCard className="p-4" glow="56, 217, 150">
                <Row label="chain" value={`${anchor.chain}  (id ${anchor.chain_id})`} mono={false} />
                <Row label="contract" value={anchor.contract} />
                <Row label="record hash" value={<DecryptedText text={anchor.record_hash} />} />
                <Row label="transaction" value={<DecryptedText text={anchor.tx_hash || ''} />} />
                <Row label="block" value={`${anchor.block_number ?? '—'}  ${anchor.block_time_utc ?? ''}`} mono={false} />
                <Row label="gas used" value={anchor.gas_used} />
                {anchor.explorer_tx && (
                  <a href={anchor.explorer_tx} target="_blank" rel="noreferrer"
                    className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-accent hover:underline">
                    <ExternalLink size={13} /> view on the block explorer
                  </a>
                )}
              </SpotlightCard>
            </Section>
          )}

          {anchor && (
            <Section title="5b  try to forge it">
              <SpotlightCard className="p-4" glow="255, 107, 107">
                <p className="text-[12px] leading-relaxed text-mute">
                  A registry that just stores a number is only as honest as whoever
                  wrote it. Below, the same proof is re-submitted three times with
                  three different similarities claimed against it &mdash; the honest
                  one, an inflated one, and one a single basis point too high.
                  Each goes through <span className="mono">eth_call</span>, which
                  runs the real contract against real chain state and throws the
                  result away: no gas, nothing written.
                </p>
                <button
                  type="button"
                  disabled={forging}
                  onClick={async () => {
                    setForging(true)
                    setForgeError('')
                    setForge(null)
                    try {
                      const body = new FormData()
                      body.append('forged_bps', '9999')
                      const r = await fetch(`/api/runs/${runId}/forge`, { method: 'POST', body })
                      const j = await r.json()
                      if (!r.ok) setForgeError(j.detail || 'the forge attempt could not run')
                      else setForge(j)
                    } catch {
                      setForgeError('could not reach the chain')
                    } finally {
                      setForging(false)
                    }
                  }}
                  className="mt-3 flex items-center gap-2 rounded-md border border-edge px-3 py-1.5
                             text-[12px] hover:border-bad/60 hover:text-bad disabled:opacity-50"
                >
                  {forging
                    ? <><Loader2 className="animate-spin" size={13} /> asking the chain…</>
                    : <><ShieldAlert size={13} /> try to forge it</>}
                </button>

                {forgeError && <p className="mono mt-3 text-[12px] text-bad">{forgeError}</p>}

                {forge && (
                  <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-3">
                    <div className="space-y-1">
                      {forge.attempts.map((a) => (
                        <div key={a.label} className="flex items-center gap-2 text-[12px]">
                          {a.accepted ? <CheckCircle2 size={13} className="shrink-0 text-good" />
                            : <XCircle size={13} className="shrink-0 text-bad" />}
                          <span className="w-24 shrink-0 text-mute">{a.label}</span>
                          <span className="mono w-16 shrink-0 text-right">
                            {(a.claimed_bps / 10000).toFixed(4)}
                          </span>
                          <span className={`mono ${a.accepted ? 'text-good' : 'text-bad'}`}>
                            {a.accepted ? 'ACCEPTED' : `REJECTED  ${a.error}`}
                          </span>
                        </div>
                      ))}
                    </div>
                    <p className={`mt-3 border-t border-edge pt-3 text-[12px] ${
                      forgeHeld ? 'text-good' : 'text-bad'}`}>
                      {forge.conclusion}
                    </p>
                    <p className="mono mt-1 text-[11px] text-mute">
                      {forge.method} · {forge.contract}
                    </p>
                  </motion.div>
                )}
              </SpotlightCard>
            </Section>
          )}

          {verifyReport && (
            <Section title="6  re-verification">
              <SpotlightCard className="p-4"
                glow={verifyReport.verdict === 'VERIFIED' ? '56, 217, 150' : '255, 107, 107'}>
                <div className="mb-3 flex items-center gap-2">
                  {verifyReport.verdict === 'VERIFIED'
                    ? <ShieldCheck className="text-good" size={20} />
                    : <ShieldAlert className="text-bad" size={20} />}
                  <span className={`font-semibold ${verifyReport.verdict === 'VERIFIED' ? 'text-good' : 'text-bad'}`}>
                    {verifyReport.verdict}
                  </span>
                  <span className="text-[11px] text-mute">
                    every hash recomputed from the files, then read back from the chain
                  </span>
                </div>
                <div className="space-y-1">
                  {verifyReport.local_checks.map((c) => (
                    <div key={c.field} className="flex items-center gap-2 text-[12px]">
                      {c.ok ? <CheckCircle2 size={13} className="text-good shrink-0" />
                        : <XCircle size={13} className="text-bad shrink-0" />}
                      <span className="w-56 shrink-0 text-mute">{c.field}</span>
                      <span className="mono hash truncate text-slate-300">{String(c.recomputed)}</span>
                    </div>
                  ))}
                  {Object.entries(verifyReport.onchain).map(([k, ok]) => (
                    <div key={k} className="flex items-center gap-2 text-[12px]">
                      {ok ? <CheckCircle2 size={13} className="text-good shrink-0" />
                        : <XCircle size={13} className="text-bad shrink-0" />}
                      <span className="w-56 shrink-0 text-mute">chain · {k}</span>
                    </div>
                  ))}
                </div>

                <div className="mt-4 border-t border-edge pt-3">
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] text-mute">
                    <FileWarning size={13} /> tamper with one field and verify again
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {['caption', 'post_url', 'similarity', 'input_image'].map((f) => (
                      <button key={f} onClick={() => tamper(f)}
                        className="rounded-md border border-edge px-2.5 py-1 text-[11px] hover:border-bad/60 hover:text-bad">
                        {f}
                      </button>
                    ))}
                  </div>
                  {tamperReport && (
                    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                      className="mono mt-3 rounded-lg border border-bad/40 bg-bad/5 p-3 text-[11px]">
                      <div className="mb-1 font-semibold text-bad">
                        {tamperReport.verdict} after altering “{tamperReport.tampered_field}”
                      </div>
                      <div className="hash text-mute">local  {tamperReport.record_hash_local}</div>
                      <div className="hash text-mute">chain  {tamperReport.record_hash_anchored}</div>
                    </motion.div>
                  )}
                </div>
              </SpotlightCard>
            </Section>
          )}
        </div>
      </div>

      <footer className="mt-10 border-t border-edge pt-4 text-[11px] text-mute">
        Hashes only on-chain: no image, no embedding and no personal data is
        published. Search covers public posts; run it on people who have
        consented or on public figures.
      </footer>
    </div>
  )
}
