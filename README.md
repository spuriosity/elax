# Elax — Real-Time Audience Reactions for Zoom

Elax lets meeting participants tap reaction buttons (clap, laugh, boo, hype, cry) on their phones, producing escalating crowd audio that plays directly into a Zoom meeting. The more people react, the louder and more layered the sound becomes.

## How It Works

```
Audience phones ──> Web Server (Flask + Socket.IO)
                         |
                         |  reads actions_count directly
                         v
                    Zoom Bot (zoom-meeting-sdk)
                         |
                         |  sends PCM audio via virtual mic
                         v
                    Zoom Meeting (heard by all participants)
```

1. Audience members open the emitter page on their phones and tap reaction buttons
2. The server aggregates reaction counts across all participants
3. The bot evaluates threshold rules (e.g., 10+ claps triggers crowd applause) and mixes the appropriate audio files
4. Mixed PCM audio is sent through the Zoom SDK's virtual microphone — participants hear it as if the bot is speaking

## Quick Start — Local Development (macOS/any OS)

The web server runs anywhere. The Zoom bot requires Linux (Docker).

```bash
# Clone and install
cd elax_python
uv sync

# Run the server
uv run python server.py
```

Open in your browser:
- `http://localhost:8080/` — Emitter (reaction buttons)
- `http://localhost:8080/listen` — Listener (GIF + audio display, for testing without the bot)
- `http://localhost:8080/control` — Control panel (timer, reactions, bot controls)

This runs the web server only. Reactions are visible/audible in the listener page via the browser. The Zoom bot is not active in this mode.

## Full Setup — With Zoom Bot (Docker)

### Prerequisites

1. **Docker** installed
2. **ffmpeg** installed (used during Docker build to convert audio to PCM)
3. A **Zoom Marketplace app** with Meeting SDK enabled (see below)

### Step 1: Create a Zoom App

1. Go to [marketplace.zoom.us](https://marketplace.zoom.us/) → Develop → Build App
2. Create a **General App**
3. Under the **Embed** tab, enable **Meeting SDK**
4. Copy the **Client ID** and **Client Secret** from App Credentials

For internal use within a single Zoom account, the app can stay in **Development** mode (no Marketplace review needed). For cross-account meetings, submit for Marketplace review (4-6 week process).

### Step 2: Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
```
APP_PORT=8080
ZOOM_APP_CLIENT_ID=your_client_id_here
ZOOM_APP_CLIENT_SECRET=your_client_secret_here
BOT_NAME=Reactions
```

### Step 3: Build and run

```bash
docker compose up --build
```

This:
- Installs the Zoom Meeting SDK (Linux-only native binaries)
- Converts all audio files to raw PCM (32kHz, mono, 16-bit)
- Starts PulseAudio (required by the SDK)
- Starts the web server on port 8080

### Step 4: Connect to a meeting

**Option A — Control panel UI:**
1. Open `http://localhost:8080/control`
2. Paste a Zoom meeting URL into the "Zoom Meeting URL" field
3. Click **Start Bot**
4. The status badge shows the bot's progress: Authenticating → Joining → In meeting → Active
5. The meeting host must **grant recording permission** when prompted

**Option B — Auto-join via environment variable:**

Set `MEETING_URL` in your `.env`:
```
MEETING_URL=https://zoom.us/j/1234567890?pwd=abc123
```

The bot will automatically join this meeting 2 seconds after the server starts.

**Option C — Stop the bot:**

Click **Stop Bot** in the control panel, or send a `botStop` Socket.IO event.

### Step 5: Share the emitter page

Share the emitter URL with meeting participants:
```
http://your-server-ip:8080/
```

Participants tap reactions on their phones. The bot plays crowd audio into Zoom.

## End-to-End Flow

```
1. docker compose up --build          # Start server + bot environment
2. Open /control in browser           # Control panel
3. Paste Zoom meeting URL             # e.g. https://zoom.us/j/123?pwd=abc
4. Click "Start Bot"                  # Bot joins meeting as "Reactions"
5. Host grants recording permission   # One-time prompt in Zoom
6. Share http://your-ip:8080/ with    # Audience opens on phones
   meeting participants
7. Participants tap reactions          # Crowd audio plays in Zoom
8. Click "Stop Bot" when done         # Bot leaves meeting
```

## Audio Thresholds

The bot plays different audio based on how many people are reacting simultaneously:

| Reaction | 3+ | 5+ | 10+ | 15+ | 20+ | 30+ |
|----------|-----|-----|------|------|------|------|
| Clap | Single clap | A few claps | Crowd clapping | — | Clapping + whistles | — |
| Laugh | Quiet laugh | Medium laugh | Loud laugh | — | — | — |
| Boo | — | — | Quiet boo | Medium boo | — | Loud boo |
| Cry | Quiet cry | Quiet cry | Loud crying | — | — | — |
| Hype | Single cheer | Crowd cheering | Loud cheering | — | — | — |

Audio files are layered with volume scaling. Multiple reaction types can play simultaneously.

## Project Structure

```
server.py              Server (Flask + Socket.IO) + bot lifecycle management
bot/
  meeting.py           Zoom SDK integration (auth, join, virtual mic)
  thresholds.py        Threshold evaluation (count → audio selection)
  mixer.py             PCM audio mixing (numpy)
  sender.py            Bridges mixer → Zoom SDK audio sender
index.html             Emitter page (reaction buttons)
listen-timer.html      Listener page (GIFs + audio + timer)
listen.html            Simple listener (no timer)
cp.html                Control panel (timer, reactions, bot start/stop)
shake.html             Experimental accelerometer input
audios/                Audio assets (MP3/M4A/WAV)
pcm/                   Generated PCM files (gitignored, created by convert_audio.sh)
convert_audio.sh       Converts audio assets to 32kHz mono PCM
Dockerfile             Ubuntu 22.04 + Zoom SDK + PulseAudio
docker-compose.yml     One-command deployment
```

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `APP_PORT` | No | `8080` | Web server port |
| `ZOOM_APP_CLIENT_ID` | For bot | — | Zoom Marketplace app Client ID |
| `ZOOM_APP_CLIENT_SECRET` | For bot | — | Zoom Marketplace app Client Secret |
| `MEETING_URL` | No | — | Auto-join this meeting on startup |
| `BOT_NAME` | No | `Reactions` | Bot's display name in Zoom |

## Notes

- The Zoom Meeting SDK is **Linux x86_64 only**. On macOS/Windows, the server runs fine but the bot module is disabled. Use Docker for the full experience.
- The meeting host must grant **recording permission** to the bot — this is a Zoom SDK requirement for the raw audio pipeline.
- Audio is sent as the bot's **microphone feed**, not screen share audio. This means it plays naturally alongside the presenter's content.
- The server also works standalone (without the bot) for the original use case: displaying reactions in a browser via the listener page.
