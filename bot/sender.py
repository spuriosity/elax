"""Audio sender — bridges the mixer and the Zoom SDK virtual microphone.

Runs a single persistent thread that:
1. Reads the current actions_list from the server
2. Evaluates thresholds
3. Mixes audio (including draining buffers)
4. Sends PCM to the Zoom SDK
"""

import time
import threading
from typing import Callable

from . import thresholds
from .mixer import AudioMixer, CHUNK_DURATION_MS, SAMPLE_RATE


class AudioSender:
    """Periodically mixes and sends audio to the Zoom meeting."""

    def __init__(self, mixer: AudioMixer, get_actions: Callable[[], dict]):
        """
        Args:
            mixer: The AudioMixer with pre-loaded PCM buffers
            get_actions: Callable that returns the current actions_list dict
        """
        self.mixer = mixer
        self.get_actions = get_actions
        self.zoom_sender = None  # Set when mic is ready
        self._running = False
        self._thread = None
        self._prev_active_names: set[str] = set()

    def set_zoom_sender(self, sender):
        """Called when the Zoom virtual mic is ready."""
        self.zoom_sender = sender
        self.start()

    def start(self):
        """Start the persistent send thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the send loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None

    def _run_loop(self):
        interval = CHUNK_DURATION_MS / 1000.0
        while self._running:
            start = time.monotonic()
            self._send_chunk()
            elapsed = time.monotonic() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _send_chunk(self):
        if not self.zoom_sender:
            return

        actions = self.get_actions()
        active = thresholds.evaluate(actions)
        active_names = {name for name, _ in active}

        # Reset playback position for newly triggered sounds
        for name in active_names - self._prev_active_names:
            self.mixer.playback_positions.pop(name, None)
        self._prev_active_names = active_names

        if active or self.mixer.has_draining():
            pcm = self.mixer.mix_chunk(active)
        else:
            pcm = self.mixer.silence()
            self.mixer.reset_positions()

        try:
            self.zoom_sender.send(pcm, SAMPLE_RATE, _get_mono_channel())
        except Exception as e:
            print(f"[sender] Error sending audio: {e}")


def _get_mono_channel():
    """Get the ZoomSDKAudioChannel_Mono enum value."""
    try:
        import zoom_meeting_sdk as zoom
        return zoom.ZoomSDKAudioChannel_Mono
    except ImportError:
        return None
