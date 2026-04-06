"""PCM audio mixing engine.

Loads pre-converted PCM files (mono, s16le) into numpy arrays.
Mixes selected audio buffers with volume scaling for real-time playback.
"""

import os
import numpy as np

SAMPLE_RATE = int(os.environ.get('AUDIO_SAMPLE_RATE', 32000))
CHUNK_DURATION_MS = 100  # Send audio in 100ms chunks
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_DURATION_MS // 1000


class AudioMixer:
    """Loads PCM audio files and mixes them in real-time."""

    def __init__(self, pcm_dir: str):
        """Load all .pcm files from the given directory into memory.

        Args:
            pcm_dir: Path to directory containing pre-converted .pcm files
                     (mono, signed 16-bit little-endian)
        """
        self.buffers: dict[str, np.ndarray] = {}
        self.playback_positions: dict[str, int] = {}
        # Tracks sounds that are draining (below threshold but still playing)
        self._draining: dict[str, float] = {}
        # Last known volumes for sounds (used when transitioning to drain)
        self._last_volumes: dict[str, float] = {}

        if not os.path.isdir(pcm_dir):
            print(f"[mixer] PCM directory not found: {pcm_dir}")
            return

        for fname in os.listdir(pcm_dir):
            if fname.endswith('.pcm'):
                name = fname[:-4]  # strip .pcm
                path = os.path.join(pcm_dir, fname)
                raw = np.fromfile(path, dtype=np.int16)
                self.buffers[name] = raw
                print(f"[mixer] Loaded {name}: {len(raw)} samples ({len(raw)/SAMPLE_RATE:.1f}s)")

    def mix_chunk(self, active_sounds: list[tuple[str, float]]) -> bytes:
        """Mix active sounds into a single PCM chunk.

        Sounds that were previously active but are no longer in active_sounds
        continue playing until their buffer finishes (decay / drain behavior).

        Args:
            active_sounds: List of (filename, volume) from thresholds.evaluate()

        Returns:
            bytes: Raw PCM data (s16le, mono) for one chunk (CHUNK_DURATION_MS)
        """
        active_set = {name: vol for name, vol in active_sounds}

        # Move formerly-active sounds into draining (let them finish naturally)
        for name, vol in list(self._draining.items()):
            if name in active_set:
                # Re-activated — remove from draining
                del self._draining[name]
        for name in list(self.playback_positions):
            if name not in active_set and name not in self._draining:
                # Was playing, no longer active — start draining at last volume
                self._draining[name] = self._last_volumes.get(name, 0.5)

        # Merge active + draining into the sounds to render this chunk
        to_render: dict[str, float] = {}
        to_render.update(self._draining)
        to_render.update(active_set)  # active overrides draining volume

        # Track volumes for future drain
        self._last_volumes = dict(active_set)

        output = np.zeros(CHUNK_SAMPLES, dtype=np.float32)

        for name, volume in to_render.items():
            if name not in self.buffers:
                continue

            buf = self.buffers[name]
            pos = self.playback_positions.get(name, 0)

            # One-shot: if buffer is exhausted, skip (don't loop)
            if pos >= len(buf):
                continue

            end = min(pos + CHUNK_SAMPLES, len(buf))
            chunk = buf[pos:end].astype(np.float32)

            # Pad if chunk is shorter than needed (end of buffer)
            if len(chunk) < CHUNK_SAMPLES:
                chunk = np.pad(chunk, (0, CHUNK_SAMPLES - len(chunk)))

            output += chunk * volume
            self.playback_positions[name] = end

        # Clean up exhausted draining sounds
        for name in list(self._draining):
            pos = self.playback_positions.get(name, 0)
            if name in self.buffers and pos >= len(self.buffers[name]):
                del self._draining[name]
                del self.playback_positions[name]

        # Clip to int16 range
        output = np.clip(output, -32768, 32767)
        return output.astype(np.int16).tobytes()

    def silence(self) -> bytes:
        """Return a chunk of silence."""
        return np.zeros(CHUNK_SAMPLES, dtype=np.int16).tobytes()

    def has_draining(self) -> bool:
        """Return True if any sounds are still draining."""
        return bool(self._draining)

    def reset_positions(self):
        """Reset all playback positions (e.g., when all reactions and draining stop)."""
        self.playback_positions.clear()
        self._draining.clear()
