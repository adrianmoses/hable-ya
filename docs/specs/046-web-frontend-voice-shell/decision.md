# Decision Record: Web Frontend Voice Shell (Home + Session)

| Field | Value |
|---|---|
| id | 046 |
| status | implemented |
| created | 2026-04-19 |
| spec | [spec.md](./spec.md) |

---

## Context

Spec 021 had landed the voice pipeline but all validation had been CLI-only (`scripts/voice_client.py` + WAV files). There was no way to feel the product — orb behavior, turn-taking latency, the "always-on mic" promise — against anything resembling the real UI. Spec 046 scoped the first browser client to two screens (Home, Session), reusing the approved Claude Design bundle at `docs/artifacts/hable-ya/`, and explicitly deferring everything that needed persistence (#026) or a learner model (#029–#033).

Three things shaped implementation beyond what the spec captured when it was drafted:

1. **The spec's wire-protocol assumption was wrong.** It assumed Pipecat's default serializer was in effect on `/ws/session` and that the official Pipecat JS client (`@pipecat-ai/client-web`) would speak it. A validation spike (`web/spike/`) built before the port revealed that Pipecat's `FastAPIWebsocketTransport` silently drops every inbound WS message when its `serializer` param is `None` (which it was). `scripts/voice_client.py` had almost certainly been hitting its silence timeout the whole time; nobody noticed because the CLI script's "no audio received" path is indistinguishable from a quiet server. This forced a small backend change (`RawPCMSerializer`) that the spec had explicitly disclaimed as a non-goal, and it retired the Pipecat JS SDK from the plan entirely.

2. **The spike itself became the frontend voice client.** Once raw PCM over native WebSocket was known to work end-to-end (Chrome/Linux, validated against a live pipeline), re-implementing the same protocol via a third-party SDK added no value and removed control over the `AudioContext`/`AnalyserNode` graph that the orb needs.

3. **React 18 StrictMode forced a call.** Double-invoked effects open two WebSockets + two mic streams on Session mount, which turned out to be a real observable bug. Hardening `VoiceClient.connect()` to be idempotent was roughly half a day of work with no product benefit for a dev-only slice; StrictMode was switched off in `main.tsx` with a TODO.

## Decision

A Vite + React 18 + TypeScript SPA lives in `web/`. Two screens: Home (editorial greeting, health-aware CTA, static placeholder persistence data, inert "próximamente" affordance for the Profile link) and Session (top status bar, breathing halo-variant orb reacting to real amplitude, pause/resume, close). The voice client is a native-WebSocket + `AudioWorklet` port of `web/spike/spike.js` that speaks 16 kHz mono int16 PCM directly against `/ws/session`. A small backend change — `hable_ya/pipeline/serializer.py::RawPCMSerializer` wired into `api/routes/session.py` — makes that protocol work with Pipecat's transport. Vite's dev server proxies `/ws/session` and `/health` to `http://localhost:8000` and is pinned to `host: 'localhost'` so Chrome treats the origin as secure.

---

## Alternatives Considered

### Wire protocol between browser and `/ws/session`

**Option A:** Use `@pipecat-ai/client-web` to speak Pipecat's default protobuf serializer (the spec's original plan).
- Pros: Zero custom protocol code; ostensibly the sanctioned way to talk to a Pipecat transport from the browser.
- Cons: Required a working default serializer on the server — it turned out the default is `None`, and `FastAPIWebsocketTransport` silently drops every message when that's the case. Would have forced a backend change anyway (enable a protobuf serializer), plus an SDK dependency, plus the loss of direct `AudioContext` control that the orb's amplitude hook depends on.

**Option B:** Native `WebSocket` + `AudioWorklet` + raw int16 PCM (the spike's approach). Server gets a minimal `RawPCMSerializer` that bridges bytes ↔ `InputAudioRawFrame`/`OutputAudioRawFrame`.
- Pros: Already validated end-to-end by the spike. ~200 LOC of vanilla code, no SDK dependency. Full control over `AnalyserNode`s for the orb. Same wire format as `scripts/voice_client.py` — one protocol, two clients.
- Cons: A backend change the spec had ruled out. Custom serializer code the project now owns.

**Chosen:** B. The spike had proven the round-trip; rewriting against a theoretical SDK integration would have thrown away a known-working lane for speculative cleanliness. The backend change is 30 lines and was non-negotiable anyway — Pipecat's "no serializer" silent-drop behavior made the spec's "no backend changes" position untenable.

### React 18 StrictMode in dev

**Option A:** Keep StrictMode on; make `VoiceClient.connect()` idempotent so the second invocation is a no-op.
- Pros: Correct long-term; catches future lifecycle bugs; aligned with React 18 guidance.
- Cons: Non-trivial state machine — needs a guard flag, coordinated teardown semantics, careful handling of half-initialized graphs, and test surface for both paths. Doubles the code's effective complexity for a dev-only scenario.

**Option B:** Disable StrictMode via a bare `createRoot(...).render(<App />)` with a TODO.
- Pros: Immediate; ~1 line; keeps `VoiceClient` simple. The production bundle behaves identically either way.
- Cons: Re-enabling StrictMode later requires the same hardening work, and until then future hook-lifecycle bugs won't be caught in dev.

**Chosen:** B. The user picked it explicitly during plan review; the cost/benefit for a solo-developer dev-only slice wasn't close.

### React state for the live `VoiceClient` instance in `Session.tsx`

**Option A:** `useRef<VoiceClient | null>` (stable mutable slot, no re-render on assignment) + a separate `useState` handle that flips once analysers exist so `useAmplitude` re-subscribes.
- Pros: Matches the "client is mutable, shouldn't drive renders" instinct.
- Cons: Two pieces of state tracking the same object. The `useState` handle is exactly what re-triggers `useAmplitude`; the ref adds nothing.

**Option B:** Single `useState<VoiceClient | null>`; set after `connect()` resolves; pass directly to `useAmplitude` and event handlers.
- Pros: One source of truth. The one re-render on assignment is precisely what we want — `useAmplitude` needs to re-run its effect when the client appears.
- Cons: None observed.

**Chosen:** B (refactor applied during `/simplify` pass).

### Styling migration (spec Open Question #2)

**Option A:** CSS modules for layout.
- Pros: Scoped class names, easier refactoring later.
- Cons: Doubles the number of files per component; loses 1:1 correspondence with the design prototype; premature given that no styling hotspot has shown pain yet.

**Option B:** Inline style objects 1:1 with the prototype; CSS variables + keyframes + `.no-scrollbar` in global stylesheets.
- Pros: Direct port; design fidelity is easy to audit against `docs/artifacts/hable-ya/project/`; no per-file overhead.
- Cons: Long inline style blocks; harder to reuse styles across components (not a current problem).

**Chosen:** B. The spec already recommended this; the port confirmed it was the right call — no styling pain emerged.

### Profile nav link (spec Open Question #3)

Three options were enumerated in the spec: (a) hide the link, (b) inert "próximamente" toast on click, (c) hover-but-no-op. Option (b) was chosen interactively during spec review (preserves design layout, signals intent, ~5 lines over hiding). No further alternatives were considered.

---

## Tradeoffs

What the chosen approach optimizes for:

- **Shipping a working browser client fast.** The spike retired the voice-integration risk before implementation started; the port was mostly mechanical after that.
- **Controlling the audio graph.** Owning `AudioContext`/`AnalyserNode`/playback scheduling directly means the orb can drive off real RMS instead of simulated sine waves, and future features (captions, side-channel events, amplitude-aware UI) don't fight an SDK's abstraction.
- **Design fidelity.** Inline styles + global tokens make it trivial to spot drift from `docs/artifacts/hable-ya/project/`.

What it gives up:

- **Dev-mode StrictMode safety.** `VoiceClient.connect()` is not idempotent under double-mount; re-enabling StrictMode requires a follow-up hardening pass.
- **Cross-browser validation.** Only Chrome on Linux was exercised. Firefox and Safari ship different `AudioContext` sample-rate handling and their own autoplay quirks — the worklet's linear-interp resample branch covers the common case but hasn't been confirmed against either.
- **RMS-based speaker inference.** `useAmplitude` decides `speaker: 'user' | 'agent' | 'idle'` from analyser RMS thresholds because the backend doesn't emit speaker events in this slice. It's a workable approximation, but won't match the semantic truth the Pipecat pipeline already knows — which means for any UI that depends on exact turn boundaries (captions, hint chips, recap-style bookkeeping), a side-channel events protocol is a prerequisite, not a polish item.
- **Ownership of a small wire protocol.** `RawPCMSerializer` is ours to maintain; if Pipecat later ships its own, we'd need to assess whether to switch.

---

### Spec Divergence

The implementation diverged from the spec on two material points and several minor ones. Both material divergences were discussed with the user and back-ported into the spec during implementation; they're listed here for completeness.

| Spec Said | What Was Built | Reason |
|---|---|---|
| Non-Goals line 72: "No backend changes. Spec 021's endpoint and default Pipecat serializer are used as-is." (original wording) | `hable_ya/pipeline/serializer.py::RawPCMSerializer` added; wired into `api/routes/session.py` | Pipecat's `FastAPIWebsocketTransport` silently drops every inbound WS message when `serializer is None`. The spec's "no-op default" assumption was simply false. Spec was updated to "minimal backend change only." |
| Approach lines 115–119 (original): client is the Pipecat JS SDK (`@pipecat-ai/client-web`) speaking the default protobuf serializer | Client is a native-WebSocket + `AudioWorklet` port of `web/spike/spike.js` speaking raw int16 PCM | With no default serializer, there was no protobuf protocol to speak. The spike had already validated raw PCM end-to-end. Spec was updated to drop the SDK. |
| Validate-before-proceeding (spec lines 138–143, original): two pending spikes | Both spikes retired by `web/spike/` before implementation started | Spec's §Confidence was rewritten as "Spike outcomes" with concrete findings. |
| No explicit guidance on `React.StrictMode` | StrictMode disabled in `main.tsx` with a TODO | React 18 double-invocation of effects would open two WebSockets + two mic streams on Session mount. Hardening `VoiceClient.connect()` for idempotency was deferred. |
| Directory layout (spec line 108): `voice/client.ts` as "Thin wrapper over Pipecat JS SDK" | `voice/client.ts` is a class-shaped port of `web/spike/spike.js`; added `public/pcm-worklet.js` (copy of `web/spike/pcm-worklet.js`) | Consequence of the wire-protocol decision above. Spec line was updated. |
| AC: "static nivel badge (placeholder — no learner adaptation in this slice)" with "NIVEL A2" | Rendered as `NIVEL A2` literally | Matches spec. |
| `npm run dev` from `web/` | `npm run dev` works; `web/README.md` added with the `http://localhost:5173/` vs `http://0.0.0.0/` warning the spike uncovered | Not a divergence — but the Chrome secure-context gotcha wasn't in the spec's original copy; `vite.config.ts` pins `host: 'localhost'` so the dev banner can't mislead. |

No other acceptance criteria diverged.

---

## Spec Gaps Exposed

1. **The "default Pipecat serializer" assumption was unverifiable from the spec alone.** Spec 021 ("voice pipeline") didn't document that its `/ws/session` endpoint relied on a `None` serializer that would silently drop inbound frames. The spike is what surfaced this. Candidate follow-up: amend spec 021's decision record to note the serializer gap explicitly and that the manual validation harness (`scripts/voice_client.py`) was never round-tripping as believed.

2. **No side-channel events protocol.** The spec deferred captions, hint chips, and the pivot overlay because their data sources don't exist in this slice. It didn't sketch *how* a future slice would get that data across the WebSocket — whether to extend `RawPCMSerializer` to multiplex text frames, add a second WS, or ship a separate HTTP side-channel. This is the single biggest unknown for the next frontend increment and deserves its own short spec before captions work starts.

3. **Cross-browser expectations.** The spec's browser matrix (Chrome must pass, Firefox/Safari best-effort) is fine as a position, but the implementation never validated either non-Chrome browser. A follow-up spike (<1 day) could confirm the worklet's resample branch works under Firefox's 48 kHz default `AudioContext`.

4. **React 18 StrictMode behavior for imperative-resource components** isn't addressed anywhere in the repo's conventions. A short note in a shared doc ("voice components own imperative resources; re-enable StrictMode only after those components are idempotent") would save re-arguing this for future slices.

5. **The `health` polling protocol** is simple enough to not need a spec, but it's worth observing that the `/health` 503-during-warmup → 200-ready contract comes from spec 021 and was never re-confirmed for frontend-side use. It worked as expected in this slice; no action needed, but it's a spec-adjacent contract that's now relied on in two places.

---

## Test Evidence

The spec (§Testing Approach) explicitly positions this slice as manual-smoke-primary. It is outside the project's pytest suite.

**Automated gates — passing.** `npm run build` runs `tsc --noEmit && vite build` and is clean:

```
> hable-ya-web@0.0.0 build
> tsc --noEmit && vite build

vite v5.4.21 building for production...
transforming...
✓ 39 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.76 kB │ gzip:  0.42 kB
dist/assets/index-LLVNysuQ.css    0.99 kB │ gzip:  0.58 kB
dist/assets/index-Cif_OhiD.js   163.06 kB │ gzip: 52.39 kB
✓ built in 340ms
```

**Dev-server wiring — passing.** With `npm run dev` up at `http://localhost:5173`:

```
root: 200
worklet: 200
```

(The `/health` proxy returns 500 when `api/main.py` is not running — expected; confirms the proxy is in place.)

**Voice round-trip — validated at the protocol level.** The `web/spike/` client — which speaks the same wire protocol as `src/voice/client.ts` — was manually confirmed earlier in this development session: Chrome, Ubuntu 24.04, bidirectional Spanish voice against a live `api/main.py` once `RawPCMSerializer` was wired in. The two clients share `public/pcm-worklet.js` (verbatim copy) and identical connect/teardown sequences; differences are structural (class vs. procedural), not protocol-level.

**Full acceptance-criterion walk in-browser — pending.** The acceptance criteria in spec §What require `api/main.py` + llama.cpp warm, plus a human driving the page through Home → mic permission → Session → pause/resume → close → unexpected-disconnect error banner → health-warmup CTA state. This is explicitly scoped as the user's manual step, not an automatable one, and has not been walked end-to-end against the final (post-`/simplify`) code. The build cleanliness and shared-protocol validation above are the strongest automated evidence this slice admits; a full in-browser pass is the next concrete action.
