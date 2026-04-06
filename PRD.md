# PRD: Elax Direct Zoom Integration (Audio Bot)

**Date**: 2026-04-04
**Status**: Draft
**Author**: Jack (assisted by Claude)
**Source project**: `/Users/jack/Dev/apps/elax_python` (Node.js, [github.com/spuriosity/elax](https://github.com/spuriosity/elax))

---

## 1. Problem Statement

Elax is a real-time audience reaction system for Zoom meetings. Participants tap reaction buttons (clap, laugh, boo, hype, cry) on their phones, and a listener page renders escalating crowd sounds — louder and more layered as more people react simultaneously.

**The current delivery mechanism is fragile and manual:**
- A second laptop joins the Zoom call as a participant
- OBS Studio captures the Elax listener page (browser window + system audio)
- A digital audio cable routes the page's audio output into Zoom's microphone input
- This requires dedicated hardware, manual setup per meeting, and a physical audio routing chain

**Goal**: Eliminate the second laptop, OBS, and audio cable entirely. Replace them with a software bot that joins the Zoom meeting as a participant and plays the reaction audio directly through its microphone feed.

### Scope Decision: Companion Bot vs. Full Rewrite

This project can be built two ways:

| Approach | Description |
|----------|-------------|
| **A. Companion bot** | Keep the Node.js Elax server as-is. Add a Python bot that connects to it as a Socket.IO client and injects audio into Zoom. Two processes, two languages. |
| **B. Unified Python rewrite** | Rewrite the 95-line `server.js` in Python (Flask/FastAPI + python-socketio), merge the bot into the same process, and serve the existing HTML/JS/audio files from Python. One process, one language, one deploy. |

**Recommendation: Option B (unified rewrite).** Rationale:
- `server.js` is 95 lines — the rewrite is trivial (Flask + python-socketio replicate it in ~80 lines)
- The existing HTML/JS frontend pages remain **completely unchanged** — they're static files served over HTTP with Socket.IO, which works identically from a Python server
- A single process eliminates inter-service coordination, Docker networking between Node.js and Python, and duplicate Socket.IO connection management
- The Zoom Meeting SDK requires Linux + Docker anyway — running Node.js alongside adds unnecessary complexity to the container
- Python is already the required language for the Zoom SDK bindings — keeping everything in one language simplifies debugging, dependency management, and deployment
- The project name is literally `elax_python` — the intent to migrate is already in the name

**What stays the same in Option B:**
- `index.html` — emitter page (no changes)
- `listen-timer.html` — listener page (still works for non-bot use cases, e.g., direct browser viewing)
- `listen.html` — simple listener (no changes)
- `cp.html` — control panel (no changes, gains bot start/stop buttons)
- `shake.html` — motion sensor (no changes)
- `audios/` — all audio assets (no changes)
- All Socket.IO events and room semantics (emitters, listeners, cp) — protocol-identical

**What changes:**
- `server.js` → `server.py` (Flask + python-socketio, same routes, same events)
- `package.json` / `node_modules` → `pyproject.toml` / `uv`
- New: Zoom bot integration as a built-in module of the same server

---

## 2. User Stories

### Meeting Host
> As a meeting host, I want to start the reaction bot with a single action (button click or CLI command) so that audience reactions are audible in the meeting without setting up a second laptop.

### Audience Member
> As a meeting participant, I want to hear crowd reactions (clapping, laughing, cheering) in real-time when I and others tap reaction buttons, without installing anything or opening extra apps.

### Presenter
> As a presenter, I want the reaction audio to come from a regular participant's microphone (not a screen share), so it plays naturally in the background while I present with screen share active.

---

## 3. Requirements

### 3.1 Must Have

| ID | Requirement |
|----|-------------|
| M1 | Bot joins a Zoom meeting as a named participant ("Reactions" or configurable) |
| M2 | Bot receives real-time `actions_count` from the Elax Socket.IO server |
| M3 | Bot plays threshold-based crowd audio into the meeting (matching current `listen-timer.html` logic) |
| M4 | Audio is heard through the bot's microphone channel, not screen share audio |
| M5 | Bot can be started with a single command: `python bot.py --meeting-url <url>` |
| M6 | Bot runs in a Docker container (Linux SDK requirement) |
| M7 | Audio files are the existing MP3/M4A assets, pre-converted to PCM at build time |

### 3.2 Should Have

| ID | Requirement |
|----|-------------|
| S1 | Start/stop bot from the Elax control panel (`cp.html`) |
| S2 | Graceful shutdown when the meeting ends or bot is kicked |
| S3 | Volume mixing — multiple simultaneous audio files blended into a single PCM stream |
| S4 | Silence when no reactions are active (no background noise/hum) |

### 3.3 Nice to Have

| ID | Requirement |
|----|-------------|
| N1 | Video feed showing the reaction GIFs (requires deeper SDK work, defer to v2) |
| N2 | Support for Google Meet / Microsoft Teams via Recall.ai fallback |
| N3 | Web dashboard showing bot status, active meeting, reaction counts |

---

## 4. Technical Design

### 4.1 Architecture

**Option B — Unified Python application:**

```
                    Unified Python Process (Docker, Linux x86_64)
                    ┌──────────────────────────────────────────────┐
                    │                                              │
Audience phones ──▶ │  Web Server (Flask + python-socketio)        │
                    │  ├── Serves index.html, cp.html, etc.       │
                    │  ├── Serves /audios/ static files            │
                    │  ├── Socket.IO rooms (emitters, listeners)   │
                    │  ├── actions_count broadcast (every 1s)      │
                    │  │                                           │
                    │  └── Bot Module (in-process, no network hop) │
                    │      ├── Reads actions_count directly        │
                    │      ├── Audio Mixer (numpy)                 │
                    │      │   ├── Threshold engine                │
                    │      │   ├── Pre-loaded PCM buffers          │
                    │      │   └── Real-time mixing at 32kHz s16le │
                    │      └── Zoom Meeting SDK                    │
                    │          ├── JWT auth                        │
                    │          ├── Join + JoinVoip()               │
                    │          ├── Virtual mic sender              │
                    │          └── GLib main loop                  │
                    └──────────────────────────────────────────────┘
```

The bot reads `actions_count` directly from the server's in-memory state — no Socket.IO client needed. The threshold engine, mixer, and Zoom SDK sender are all in the same process.

### 4.2 Component Breakdown

#### 4.2.1 Meeting Connector

Handles Zoom SDK lifecycle: init, auth, join, VoIP, recording privilege, reminder auto-accept, leave/cleanup.

**Key API calls:**
- `zoom.InitSDK(init_param)` — one-time SDK initialization
- `zoom.CreateAuthService()` → `SDKAuth(jwt_context)` — authenticate with JWT
- `meeting_service.Join(join_param)` — join with meeting number + password, video off, audio on
- `audio_ctrl.JoinVoip()` — required workaround for SDK >6.3.5
- `recording_ctrl.StartRawRecording()` — enables the raw audio pipeline
- `audio_helper.setExternalAudioSource(mic_event)` — registers the virtual microphone

**Authentication:** JWT signed with `ZOOM_APP_CLIENT_ID` / `ZOOM_APP_CLIENT_SECRET` from a Zoom Marketplace General App with Meeting SDK enabled.

#### 4.2.2 Web Server (replaces server.js)

A direct Python port of the 95-line `server.js`. Implements the same routes and Socket.IO events:

```python
# Flask routes (identical to Express routes)
@app.route('/')              → index.html
@app.route('/control')       → cp.html
@app.route('/shake')         → shake.html
@app.route('/listen')        → listen-timer.html

# Socket.IO events (identical protocol)
'join'              → socket.enter_room(room_name)
'action'            → actions_list[action] += 1; schedule decrement in 1s
'addTime'           → emit to listeners
'timerReset'        → emit to listeners
'setReactionStatus' → emit to listeners

# Background task: emit actions_count to 'listeners' room every 1s
```

The `actions_list` dict is shared in-process with the bot module — no network hop or serialization.

#### 4.2.3 Reaction State

The `actions_list` dict (`{"clap": 0, "laugh": 0, "boo": 0, "woo": 0, "cry": 0}`) is the shared state between the web server and the bot. The server increments on `action` events and decrements after 1s (same as current JS). The bot reads it directly for threshold evaluation.

#### 4.2.3 Audio Mixer

The core logic, ported from `listen-timer.html` lines ~280-380. Implements:

**Threshold table** (directly from the existing JS):

| Reaction | 3+ | 5+ | 10+ | 15+ | 20+ | 30+ |
|----------|-----|-----|------|------|------|------|
| clap | clap_one (0.6) | clap_few (0.6) | clap_alot (0.6) | — | clap_whistle (0.7) | — |
| laugh | laugh (0.2) | laugh (0.4) | laugh (0.7) | — | — | — |
| boo | — | — | boo (0.2) | boo (0.4) | — | boo (0.7) |
| cry | cry_one (0.1) | cry_one (0.1) | cry_alot (0.7) | — | — | — |
| woo | woo_one (0.2) | woo_alot (0.4) | woo_alot (0.7) | — | — | — |

**Mixing strategy:**
1. On each `actions_count` update, determine which audio files should be active and at what volume
2. Overlay active audio buffers (pre-loaded as numpy int16 arrays at 32kHz mono) with volume scaling
3. Clip to int16 range to prevent distortion
4. Queue the mixed PCM chunks for the sender

**Pre-conversion at build time:**
```bash
for f in audios/*.mp3 audios/*.m4a audios/*.wav; do
    ffmpeg -i "$f" -f s16le -acodec pcm_s16le -ar 32000 -ac 1 "pcm/$(basename ${f%.*}).pcm"
done
```

#### 4.2.4 Audio Sender

A GLib timer callback (~100ms interval) that dequeues PCM chunks from the mixer and calls:

```python
audio_sender.send(chunk, 32000, zoom.ZoomSDKAudioChannel_Mono)
```

When no reactions are active, sends silence (zero bytes) to maintain the audio stream.

### 4.4 Threading Model

```
Thread 1: GLib Main Loop
├── Zoom SDK callbacks (auth, meeting status, mic init/start)
├── Audio sender timer (GLib.timeout_add, every 100ms)
│   ├── Reads actions_list directly (shared dict)
│   ├── Evaluates thresholds
│   ├── Mixes PCM buffers
│   └── Sends via audio_sender.send()

Thread 2: Flask + python-socketio (eventlet/gevent)
├── HTTP server (serves HTML pages + static audio files)
├── Socket.IO event handling (action, join, timerReset, etc.)
├── actions_list increment/decrement (with threading.Lock)
├── 1s broadcast loop (actions_count → listeners room)
```

The `actions_list` dict is the only shared state. Protected by a `threading.Lock` for the increment/decrement operations. Reads from the GLib thread are safe since Python's GIL guarantees atomic dict reads.

### 4.4 Docker Environment

Based on the `zoom-meeting-sdk` project's Dockerfile:
- **Base**: `ubuntu:22.04`
- **Dependencies**: PulseAudio, ALSA, GLib, Python 3.12, ffmpeg
- **Volumes**: Mount `audios/` directory for audio assets
- **Env vars**: `ZOOM_APP_CLIENT_ID`, `ZOOM_APP_CLIENT_SECRET`, `MEETING_ID`, `MEETING_PWD`, `ELAX_SERVER_URL`

---

## 5. Zoom App Setup

### 5.1 Marketplace App Registration

1. Go to [Zoom Marketplace](https://marketplace.zoom.us/) → Develop → Build App
2. Create a **General App**
3. Under **Embed** tab, enable **Meeting SDK**
4. Note the `Client ID` and `Client Secret` from App Credentials
5. For internal use only: keep app in **Development** mode (no Marketplace review needed if all meeting participants are on the same Zoom account)
6. For cross-account meetings: submit for **Marketplace review** (4-6 week process)

### 5.2 Meeting Host Requirements

- Host must grant **recording privilege** when the bot requests it (one-time prompt per meeting)
- Alternatively, pre-authorize the bot as a recording participant in Zoom meeting settings

---

## 6. Audio Behavior Specification

### 6.1 Timing

- `actions_count` arrives every **1 second** from the Elax server
- Audio mixer evaluates thresholds on each update
- Audio sender pushes PCM chunks every **100ms** (3,200 samples per chunk at 32kHz)
- End-to-end latency target: **<500ms** from button tap to audible sound in meeting

### 6.2 Audio Lifecycle

1. **Trigger**: `actions_count['clap']` crosses threshold (e.g., 0 → 3)
2. **Play**: Start sending the `clap_one.pcm` buffer from the beginning, scaled to volume 0.6
3. **Overlap**: If count rises to 10 while `clap_one` is still playing, layer `clap_alot.pcm` on top
4. **Decay**: When count drops below threshold (e.g., 3 → 0), let current audio buffers finish naturally (don't hard-cut)
5. **Silence**: When all buffers have finished, send zero-filled PCM chunks

### 6.3 Mixing Rules

- **Additive mixing**: Sum int16 sample values across all active buffers
- **Volume scaling**: Multiply samples by the threshold's volume factor before summing
- **Clipping**: Clamp summed values to [-32768, 32767] to prevent wrap-around distortion
- **No compression or normalization** — keep it simple, match the browser audio behavior

---

## 7. File Structure

```
elax_python/
├── index.html                    # Emitter page (UNCHANGED)
├── listen-timer.html             # Listener page (UNCHANGED — still works for browser viewing)
├── listen.html                   # Simple listener (UNCHANGED)
├── cp.html                       # Control panel (MINOR — add bot start/stop buttons)
├── shake.html                    # Motion sensor (UNCHANGED)
├── audios/                       # Audio assets (UNCHANGED)
│   ├── clap_one.mp3
│   ├── clap_few.mp3
│   ├── ...
│
├── server.py                     # Python port of server.js (Flask + python-socketio)
├── bot/
│   ├── __init__.py
│   ├── meeting.py                # Zoom SDK lifecycle: auth, join, voip, recording, cleanup
│   ├── thresholds.py             # Threshold table + evaluation (ported from listen-timer.html JS)
│   ├── mixer.py                  # PCM audio mixing: load buffers, scale, sum, queue
│   └── sender.py                 # GLib timer callback, dequeue + send PCM chunks
├── pcm/                          # Pre-converted PCM files (gitignored, generated by convert_audio.sh)
│
├── Dockerfile                    # Ubuntu 22.04 + PulseAudio + Python 3.12 + ffmpeg
├── docker-compose.yml            # One-command startup: docker compose up
├── convert_audio.sh              # ffmpeg script: MP3/M4A/WAV → 32kHz mono s16le PCM
├── pyproject.toml                # uv-managed deps: flask, python-socketio, numpy, PyJWT, etc.
├── .env.example                  # Template for credentials
└── PRD.md                        # This document
```

---

## 8. Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ZOOM_APP_CLIENT_ID` | Yes | — | Zoom Marketplace app Client ID |
| `ZOOM_APP_CLIENT_SECRET` | Yes | — | Zoom Marketplace app Client Secret |
| `MEETING_URL` | Yes* | — | Full Zoom meeting URL (parsed for ID + password) |
| `MEETING_ID` | Yes* | — | Numeric meeting ID (alternative to URL) |
| `MEETING_PWD` | Yes* | — | Meeting password (alternative to URL) |
| `APP_PORT` | No | `8080` | Web server port (same as current .env) |
| `BOT_NAME` | No | `Reactions` | Display name in Zoom participant list |
| `AUDIO_SAMPLE_RATE` | No | `32000` | PCM sample rate in Hz (must match `convert_audio.sh` `-ar` value) |

*Either `MEETING_URL` or both `MEETING_ID` + `MEETING_PWD` are required.

---

## 9. Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Python SDK is beta (36/235 objects) | Audio API breaks in future release | Medium | Pin SDK version, test on upgrade. Audio send is a core feature unlikely to be removed. |
| Recording privilege not granted by host | Bot joins but is silent | High | Add clear bot status feedback. Document host requirement. Consider auto-requesting with retry. |
| Zoom Marketplace review required for cross-account meetings | 4-6 week delay | High if cross-account | Start review process early. For internal use, Development mode bypasses this. |
| Audio mixing latency exceeds 500ms | Reactions feel laggy | Low | PCM mixing is trivial compute. Socket.IO latency is the bottleneck (already ~1s by design). |
| GLib + Flask threading conflicts | Deadlocks or missed events | Medium | Flask runs in its own thread via eventlet/gevent. Shared `actions_list` protected by Lock. GIL provides atomic dict reads. |
| Docker overhead for local development | Friction for contributors | Low | Provide `docker compose up` one-liner. Mount source code for live reload. Server-only mode (no bot) can run natively on macOS without Docker. |

---

## 10. Implementation Phases

### Phase 1: Python Server Port (1-2 days)
- Rewrite `server.js` → `server.py` (Flask + python-socketio)
- Verify all HTML pages work identically from the Python server
- Verify Socket.IO events (action, timerReset, setReactionStatus) work with existing frontend
- This phase produces a working Elax system with no Node.js dependency

### Phase 2: Zoom Bot — Proof of Concept (3-5 days)
- Docker environment with `zoom-meeting-sdk` + PulseAudio + GLib
- Bot joins a meeting and plays a static PCM file through the virtual microphone
- Validates: SDK auth, meeting join, VoIP, recording privilege, audio send pipeline
- This is the highest-risk phase — if the SDK audio send doesn't work reliably, fall back to Recall.ai

### Phase 3: Threshold Engine + Audio Mixing (2-3 days)
- Port threshold table from `listen-timer.html` JS to Python
- Pre-convert all audio files to PCM at build time (`convert_audio.sh`)
- Load PCM buffers at startup as numpy int16 arrays
- Real-time mixing with volume scaling and clipping
- Silence generation when idle
- Decay behavior (let playing audio finish naturally)

### Phase 4: Integration + Hardening (2-3 days)
- Wire the bot module to read `actions_list` directly from the server
- Add bot start/stop controls to `cp.html` (new Socket.IO events: `botStart`, `botStop`)
- Graceful shutdown on meeting end
- Error recovery (reconnect on disconnect)
- Logging and status reporting

### Phase 5 (Optional): Video Feed
- Render reaction GIFs to YUV420 frames
- Send via `IZoomSDKVideoSource` (requires deeper SDK work)
- Defer unless audio-only feels incomplete

---

## 11. Alternatives Considered

| Approach | Why Not |
|----------|---------|
| **Recall.ai Output Media API** | $0.50/hr ongoing cost. Best fallback if self-hosted bot proves too complex. Near-zero code change — just point Recall at the existing listener page URL. |
| **Zoom Meeting SDK (C++)** | Same capability as the Python bindings but requires C++ development. Python wrapper is sufficient and faster to iterate on. |
| **Virtual camera/audio on Linux** | ToS risk. Requires running a full Zoom desktop client in Docker with Xvfb. More fragile than the SDK approach. |
| **Browser-based audio on each device** | Eliminates the injection problem entirely, but audio isn't in the Zoom call itself — participants need the page open and unmuted on a separate device/tab. |
| **Zoom Apps SDK (sidebar)** | Clean architecture but requires Marketplace review and every participant must open the app panel. |
| **OBS + second laptop (current)** | Works but requires dedicated hardware and manual setup per meeting. This PRD exists to replace it. |

---

## 12. Success Criteria

1. Bot joins a Zoom meeting with a single `docker compose up` command (plus env vars)
2. Audience members tap reactions on `index.html` and hear crowd audio in the Zoom call within 2 seconds
3. Audio escalation matches the existing `listen-timer.html` behavior (thresholds, volumes, layering)
4. No second laptop, OBS, or audio cable required
5. Bot is stable for a 60-minute meeting without crashes or audio dropout

---

## 13. Open Questions

1. **Cross-account meetings**: Will this be used only within a single Zoom organization, or across different orgs? Determines whether Marketplace review is needed.
2. **Host cooperation**: Is it acceptable that the host must grant recording privilege? Or should the bot account be pre-authorized?
3. **Audio file updates**: Should the audio assets be configurable/swappable at runtime, or is build-time conversion sufficient?
4. **Concurrent meetings**: Will the bot ever need to be in multiple meetings simultaneously? (Affects architecture — one process per meeting vs. multi-meeting orchestrator.)
5. **Server-only mode**: Should `server.py` work without the Zoom SDK installed (i.e., on macOS for local dev without bot functionality)? This would let contributors work on the web UI without Docker. The bot module would be an optional import.
6. **Listener page deprecation**: With the bot handling audio injection directly, is `listen-timer.html` still needed? It could remain for debugging/testing (view reactions in a browser without Zoom), or be removed to reduce surface area.
