#!/usr/bin/env bash
# Convert all audio files to raw PCM (32kHz, mono, signed 16-bit LE)
# for use with the Zoom Meeting SDK virtual microphone.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PCM_DIR="${SCRIPT_DIR}/pcm"
SAMPLE_RATE="${AUDIO_SAMPLE_RATE:-32000}"

mkdir -p "$PCM_DIR"

echo "Converting audio files to PCM (${SAMPLE_RATE}Hz, mono, s16le)..."

for src in "$SCRIPT_DIR"/audios/*.mp3 "$SCRIPT_DIR"/audios/*.m4a "$SCRIPT_DIR"/audios/*.wav; do
    [ -f "$src" ] || continue
    name="$(basename "${src%.*}")"
    dst="${PCM_DIR}/${name}.pcm"
    echo "  ${src} → ${dst}"
    ffmpeg -y -i "$src" -f s16le -acodec pcm_s16le -ar "$SAMPLE_RATE" -ac 1 "$dst" 2>/dev/null
done

echo "Done. PCM files in: ${PCM_DIR}"
