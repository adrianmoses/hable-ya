# Spec: Web Frontend Voice Shell (Home + Session)

| Field | Value |
|---|---|
| id | 046 |
| status | implemented |
| created | 2026-04-19 |
| covers roadmap | #046 (Web frontend for the voice agent) |
| design bundle | `docs/artifacts/hable-ya/` (Claude Design handoff, 2026-04-19) |
| spike | `web/spike/` — validated raw-PCM browser client (2026-04-19) |

---

## Why

Spec 021 landed the server-side voice pipeline — `/ws/session` streams a full VAD → Whisper STT → llama.cpp Gemma → piper TTS loop — but there is no way to talk to it from anything that resembles the actual product surface. All validation has been CLI-based. Without a browser client we can't evaluate the voice UX (turn-taking feel, perceived latency, the "always-on mic" promise), and we can't iterate on product design at all.

The project owner now has an approved design bundle in `docs/artifacts/hable-ya/` (warm editorial aesthetic, breathing orb, Home + Session + Recap + Profile screens) from Claude Design. Rather than build a generic dev client, we land the actual product UI — scoped to the two screens needed to exercise the pipeline — as the first frontend increment. Later slices fold in captions, recap, profile, and learner state once the data paths they depend on exist.

### Consumer Impact

- **End user (learner):** Gets a real browser to talk to. Opens the web app, sees a warm editorial Home screen with a single "Empezar a hablar" CTA, clicks it, grants mic permission, and has a streaming Spanish voice conversation with the agent anchored visually by the breathing orb.
- **Project owner (researcher/developer):** Gets a product-shaped harness for testing the voice pipeline — can hear real agent latency and feel the turn-taking against a real UI, not a CLI. Unblocks design iteration on orb behavior, mic UX, and session controls.
- **Downstream features:** Captions (requires side-channel transcription events — future spec); Recap (requires session persistence — #026, #028); Profile (requires learner model — #029–#033); remaining orb variants and Tweaks panel (deferred polish).

### Roadmap Fit

Covers roadmap item #046. Fast-follows spec 021 (voice pipeline) and deliberately lands ahead of #025–#033 (tool handler, turn observer, learner profile, knowledge graph) so that:

- product-UX decisions can be validated against the real pipeline, not an abstraction;
- the feature work that depends on persistence (#025–#033) can be shaped by actual sessions users have had, rather than designed in the abstract.

Dependencies:

- Upstream (needed before this): #021 (voice pipeline) ✓ implemented.
- Downstream (unblocked by this): future spec for captions + side-channel events; eventual Recap (needs #026/#028) and Profile (needs #029–#033) slices.

---

## What

### Acceptance Criteria

From the consumer's perspective, with llama.cpp and `api/main.py` running:

- [ ] A user opens the web app in a modern browser and lands on the Home screen, visually matching `docs/artifacts/hable-ya/project/components/home.jsx` (greeting, stats strip, CTA, agent card, recent-topics row). Data that requires persistence (racha, última sesión, recent topics) is rendered as static placeholder values.
- [ ] Clicking "Empezar a hablar" prompts for microphone permission (if not already granted), opens a WebSocket to `/ws/session`, and transitions to the Session screen.
- [ ] On the Session screen, the halo-variant orb is rendered ~460px, centered, breathing on its own when idle.
- [ ] The user can speak Spanish and hear the agent respond in Spanish, with a latency consistent with CLI testing of spec 021.
- [ ] The orb visually reacts to audio: idle breathing when silent; user-speaking state (clay tint + higher amplitude drive) when local mic audio is above a threshold; agent-speaking state (deeper tint) when received audio frames are being played back.
- [ ] The top status bar shows a pulsing "MICRÓFONO ACTIVO" dot, an elapsed timer (`mm:ss`), a static nivel badge (placeholder — no learner adaptation in this slice), and pause + close buttons.
- [ ] Pause mutes the local microphone (sent audio frames stop) and flips the status label to "PAUSADO"; resume restores it. The WebSocket stays open.
- [ ] Close tears down the pipeline (stops mic tracks, closes the WebSocket cleanly, flushes any remaining playback) and returns to Home.
- [ ] The WebSocket unexpectedly closing (e.g. server 1013 warmup, network error) returns the user to Home with a non-blocking inline error ("Se perdió la conexión").
- [ ] `GET /health` returning 503 during warmup is surfaced on Home as a disabled CTA labeled "María está despertando…" until the endpoint returns 200.
- [ ] Captions, hint chips, pivot overlay, Recap screen, Profile screen, orb variants B/C, and the Tweaks panel are NOT present in this slice (explicitly deferred).
- [ ] `npm run dev` (or `pnpm dev`) from `web/` starts a Vite dev server that successfully talks to `api/main.py` on a fresh checkout.
- [ ] `npm run build` produces a production bundle without type errors (`tsc --noEmit` clean, `vite build` clean).

### Non-Goals

- **No captions or transcription display.** STT output is not surfaced to the UI. Side-channel WS messages (transcripts, tool calls, level events) are not added to the backend in this spec. Revisited when async transcription processing lands.
- **No Recap screen.** Requires session persistence (#026) and learner state (#029) to be meaningful. The prototype's recap variants are not ported.
- **No Profile screen.** Requires the learner model (#029–#033). The nav link in Home either renders an inert "próximamente" state or is hidden.
- **No orb variants B (bars) or C (liquid).** Variant A (halo) only. Variant switching deferred.
- **No Tweaks panel.** The postMessage iframe editor protocol from the prototype is removed entirely.
- **No learner adaptation.** Level badge is static. No dynamic pivot announcement. No hint chips.
- **No authentication, no multi-tenancy.** Matches OVERVIEW (single-tenant).
- **No mobile responsive layouts.** The design is desktop-first at 1280+; this slice matches that.
- **No production deployment.** Dev-only, served via `vite dev`. Static bundling + FastAPI static-serving is a later concern.
- **No SSR.** Client-rendered SPA.
- **No state persistence beyond the tab.** No localStorage for sessions (spec 021 does not persist either).
- **Minimal backend change only** — a `RawPCMSerializer` was added to spec 021's WebSocket route during the validation spike (see §How / Spike outcomes). No other backend work is in scope for this slice.

### Open Questions

1. ~~**Wire protocol for `FastAPIWebsocketTransport`.**~~ **Resolved via spike (`web/spike/`).** `FastAPIWebsocketTransport` silently drops inbound frames when its `serializer` param is `None`, and no raw-PCM serializer ships with Pipecat. Added `hable_ya/pipeline/serializer.py::RawPCMSerializer` to bridge int16 little-endian mono PCM ↔ `InputAudioRawFrame`/`OutputAudioRawFrame` and wired it into `api/routes/session.py`. The browser speaks the same raw-PCM protocol as `scripts/voice_client.py`. The Pipecat JS client (`@pipecat-ai/client-web`) is **not** used.
2. **Styling migration: inline styles vs CSS modules.** Prototype uses inline style objects. Proposed: keep CSS variables as a global stylesheet (`src/styles/tokens.css`); keep layout as inline styles on first port (1:1 with prototype); migrate hot spots to CSS modules only if they actually cause pain. Confirm with owner before implementation.
3. ~~**Profile nav link behavior.**~~ **Resolved:** the link stays in the Home layout for design fidelity, but clicking it renders an inert "próximamente" state (e.g. a small toast or disabled affordance) instead of navigating. No Profile route is wired in this slice.

---

## How

### Approach

Land a Vite + React 18 + TypeScript SPA in `web/`, porting two screens from the design bundle (Home, Session) and wiring Session to the existing `/ws/session` WebSocket.

**Directory layout** (`web/`, sibling to `api/` and `hable_ya/`):

```
web/
├── package.json
├── tsconfig.json
├── vite.config.ts
├── index.html
└── src/
    ├── main.tsx                     React root + Router
    ├── App.tsx                      Shell: route Home ↔ Session; health probe
    ├── styles/
    │   └── tokens.css               CSS variables ported from design :root
    ├── components/
    │   ├── icons.tsx                Line icons from design (MicIcon, etc.)
    │   └── orb/
    │       └── OrbHalo.tsx          Variant A only; { speaker, amp, size }
    ├── routes/
    │   ├── Home.tsx                 Port of home.jsx → TSX, placeholder data
    │   └── Session.tsx              Port of session.jsx → TSX, wired to voice client
    ├── voice/
    │   ├── client.ts                Port of web/spike/spike.js: native WebSocket + AudioWorklet, raw int16 PCM both ways
    │   ├── pcm-worklet.js           Port of web/spike/pcm-worklet.js: Float32 mic → 20ms int16 chunks at 16 kHz
    │   └── amplitude.ts             useAmplitude hook driven by real AudioContext AnalyserNodes
    └── lib/
        └── health.ts                /health polling hook used by Home
```

**Voice integration** is the core technical piece. The backend (spec 021, plus the `RawPCMSerializer` added during the spike) speaks raw int16 little-endian mono PCM on `/ws/session`, the same protocol `scripts/voice_client.py` uses. The browser talks to it directly via the native `WebSocket` API — no Pipecat JS SDK. The voice client is a port of `web/spike/spike.js` (validated working) and is responsible for:

- requesting `getUserMedia({ audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false } })`,
- running an `AudioWorklet` (`pcm-worklet.js`) that converts Float32 mic samples to 20 ms Int16 chunks (320 samples / 640 bytes) at 16 kHz, with linear-interp resampling if the `AudioContext` sample rate differs from 16 kHz,
- sending those chunks as binary WebSocket frames,
- decoding incoming int16 frames back to Float32 and scheduling them on an `AudioContext` playback chain,
- surfacing an `AnalyserNode` on both the mic source and the playback destination so `useAmplitude` can drive the orb.

`useAmplitude` replaces the prototype's simulated sine-wave hook. `speaker` state is inferred locally from analyser RMS — `user` when mic RMS > threshold, `agent` when playback RMS > threshold, `idle` otherwise. This is a necessary compromise since the backend does not emit speaker events in this slice; revisit when side-channel events ship.

**Health-aware Home**: on mount, `Home` polls `GET /health` until 200; while 503, the CTA is disabled with "María está despertando…". Matches spec 021's warmup behavior without a tight retry loop.

**Styling**: CSS variables move to `src/styles/tokens.css` and import once in `main.tsx`. Layout + per-element styling stays as inline style objects (TSX), 1:1 with the prototype. Google Fonts (`Instrument Serif`, `Geist`, `Geist Mono`) linked in `index.html`. `no-scrollbar` utility and keyframes move into a small global stylesheet.

**Port fidelity**: match the design's visual output pixel-for-pixel where it doesn't fight React + TS ergonomics. Drop the `Object.assign(window, ...)` globals in favor of ESM imports. Drop the `EDITMODE` markers and Tweaks postMessage protocol entirely. Replace the autoplay `SCRIPT` in `Session.tsx` with real voice state driven by the client.

**Dev integration with the backend**: `vite.config.ts` sets `server.proxy` to forward `/ws/session` and `/health` to `http://localhost:8000`. No CORS needed in dev. Production serving is out of scope.

### Confidence

**Level:** Medium-high (after spike)

**Rationale:** The voice-integration risk is now retired — a working browser ↔ pipeline round-trip exists in `web/spike/` on Chrome/Linux. The component port from the design bundle is low-risk and well-understood. Remaining uncertainty is mostly React ergonomics around `useAmplitude` (wiring an `AnalyserNode` into a hook without re-creating the audio graph on render) and cross-browser behavior (Firefox/Safari `AudioContext` sample-rate quirks).

**Spike outcomes (`web/spike/`, validated 2026-04-19):**

1. **Bidirectional audio works** over the native `WebSocket` API with a 16 kHz int16 mono PCM protocol. The spike page (`web/spike/index.html` + `spike.js` + `pcm-worklet.js`) connects to `/ws/session`, streams mic audio, and plays back agent audio.
2. **Pipecat default serializer is incompatible.** `FastAPIWebsocketTransport` silently drops inbound WS messages when `params.serializer is None`, and no raw-PCM serializer ships with Pipecat. Fix: `hable_ya/pipeline/serializer.py::RawPCMSerializer` wired into `api/routes/session.py`. This is the only backend change in-scope for #046.
3. **Pipecat JS SDK not used.** We can speak the pipeline's wire protocol directly from ~200 LOC of vanilla JS (worklet + WebSocket). Dropping the SDK dependency simplifies the voice module and retires Open Question #1.
4. **Amplitude hook points are trivial.** Because we own the `AudioContext`, connecting `AnalyserNode`s to both the mic source and the playback gain is direct — no SDK fork needed. This resolves the second original spike item.

**Gotchas documented by the spike:**

- Chrome treats `http://0.0.0.0:PORT` as an **insecure context** and hides `navigator.mediaDevices`. Dev must use `http://localhost:…` or `http://127.0.0.1:…`. Document this in `web/README.md` or the Vite dev output.
- The spike's `/health` polling fails CORS when served from a different origin than `api/main.py`. The production plan — `vite.config.ts` proxying `/health` and `/ws/session` to `http://localhost:8000` — sidesteps this; no CORS middleware needed on the API.

**Remaining implementation-time checks (not blocking):**

- Verify Firefox and Safari resample correctly through the worklet's fallback path (the spike only confirmed Chrome on Linux).
- Confirm `useAmplitude`'s RMS thresholds for `speaker` inference feel right against real turn-taking — expect to tune.

### Key Decisions

1. **Framework: Vite + React 18 + TypeScript.** Vite over Next.js because the frontend is a browser-only SPA with no SSR/routing needs Next.js would simplify, and the backend is FastAPI so there's no shared runtime benefit. TS for parity with the repo's strictness (mypy strict on Python).
2. **Directory: `web/`.** Short, unambiguous, pairs with `api/` and `hable_ya/`.
3. **Scope: Home + Session only; orb variant A only.** Matches the goal of exercising the voice pipeline's UX before building persistence-dependent screens. Recap/Profile need data layers that don't exist yet; building them against mocks is throwaway work.
4. **Backend change limited to the raw-PCM serializer.** Adding `RawPCMSerializer` was the minimum change required to make spec 021's `/ws/session` actually receive audio — without it, `FastAPIWebsocketTransport` drops every inbound frame (see §How / Spike outcomes). Side-channel events (captions, learner events) remain deliberately deferred to a follow-up spec.
5. **Speaker state inferred locally** (not received from backend) via amplitude thresholds on analyser nodes. Imperfect but avoids changing the wire protocol in this slice.
6. **Static placeholder data** for anything that requires persistence (racha, última sesión, recent topics, level badge). Marked as placeholders in code comments so a later slice can swap them for real endpoints.

### Testing Approach

This slice lives in the frontend and talks to a live backend; it sits outside the project's pytest suite. Per OVERVIEW, pytest covers scoring heuristics, not UX validation. Testing is primarily manual:

- **Manual smoke test (primary, required):** Start llama.cpp + `api/main.py` + `vite dev`. Open the app in Chrome. Verify each acceptance criterion end-to-end — mic permission grant, bidirectional audio, orb reactivity in idle/user/agent states, pause/resume, close tears down cleanly, warmup disabled-CTA state, unexpected WS close returns home with error.
- **Type check:** `tsc --noEmit` clean on every change.
- **Build:** `vite build` succeeds.
- **Lint (non-gating):** ESLint + Prettier configured to match repo conventions; not a release gate for this slice.
- **No automated UI tests.** Playwright against a full voice pipeline is high-cost-low-value at this stage. Revisit once the UI stabilizes and captions provide automatable assertions.

Browser matrix:

- Chrome latest — must pass.
- Firefox latest — should pass; Pipecat SDK is the risk.
- Safari — best-effort; mic sample-rate handling is historically quirky.
