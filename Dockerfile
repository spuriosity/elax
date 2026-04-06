FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System dependencies for Zoom SDK + audio
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev \
    ffmpeg \
    pulseaudio \
    alsa-utils \
    libglib2.0-0 \
    libglib2.0-dev \
    libgirepository-1.0-1 \
    gir1.2-glib-2.0 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for caching
COPY pyproject.toml uv.lock* ./

# Install dependencies (including zoom-meeting-sdk in Docker)
RUN uv sync && uv pip install zoom-meeting-sdk

# Copy the rest of the app
COPY . .

# Convert audio files to PCM
RUN bash convert_audio.sh

# Start PulseAudio and the server
CMD ["sh", "-c", "pulseaudio --start --daemonize && uv run python server.py"]
