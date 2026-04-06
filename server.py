import os
import re
import signal
import sys
import threading
import eventlet
eventlet.monkey_patch()

from dotenv import load_dotenv
load_dotenv()

import socketio
from flask import Flask, send_from_directory

# --- Config ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PORT = int(os.environ.get('APP_PORT', 8080))

# --- Bot state ---
bot_instance = None
audio_sender_instance = None

# --- App setup ---
sio = socketio.Server(async_mode='eventlet')
flask = Flask(__name__)
app = socketio.WSGIApp(sio, flask)

# --- State ---
actions_lock = threading.Lock()
actions_list = {
    'clap': 0,
    'laugh': 0,
    'boo': 0,
    'woo': 0,
    'cry': 0,
}


# --- Routes ---
@flask.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')


@flask.route('/control')
def control():
    return send_from_directory(BASE_DIR, 'cp.html')


@flask.route('/shake')
def shake():
    return send_from_directory(BASE_DIR, 'shake.html')


@flask.route('/listen')
def listen():
    return send_from_directory(BASE_DIR, 'listen-timer.html')


@flask.route('/.well-known/acme-challenge/<token>')
def acme_challenge(token):
    return token


# Static file serving: audios/, images/, css/ directories served at root URL
# e.g. /clap_one.mp3 -> audios/clap_one.mp3
@flask.route('/<path:filename>')
def static_files(filename):
    for subdir in ['audios', 'images', 'css']:
        full_path = os.path.join(BASE_DIR, subdir, filename)
        if os.path.isfile(full_path):
            return send_from_directory(os.path.join(BASE_DIR, subdir), filename)
    # Fallback: serve from project root (for files like timer-end.wav at root)
    full_path = os.path.join(BASE_DIR, filename)
    if os.path.isfile(full_path):
        return send_from_directory(BASE_DIR, filename)
    return 'Not found', 404


# --- Socket.IO events ---
@sio.event
def connect(sid, environ):
    print('a user connected')


@sio.event
def disconnect(sid):
    print('user disconnected')


@sio.event
def join(sid, room):
    sio.enter_room(sid, room)


@sio.event
def action(sid, action_name, user_id=None):
    with actions_lock:
        actions_list[action_name] += 1
    print(f'new action triggered: {action_name} by user: {user_id}')

    def decrement():
        with actions_lock:
            actions_list[action_name] -= 1

    eventlet.spawn_after(1, decrement)


@sio.event
def addTime(sid):
    sio.emit('addTime', room='listeners')


@sio.event
def timerReset(sid, seconds):
    print('timer Reset received: ', seconds)
    sio.emit('timerReset', seconds, room='listeners')


@sio.event
def setReactionStatus(sid, status):
    sio.emit('setReactionStatus', status, room='listeners')


# --- Bot integration ---
def parse_meeting_url(url):
    """Extract meeting ID and password from a Zoom URL.

    Handles formats like:
    - https://zoom.us/j/1234567890?pwd=abc123
    - https://us06web.zoom.us/j/1234567890?pwd=abc123
    """
    match = re.search(r'/j/(\d+)', url)
    if not match:
        return None, None
    meeting_id = int(match.group(1))
    pwd_match = re.search(r'pwd=([^&]+)', url)
    password = pwd_match.group(1) if pwd_match else ''
    return meeting_id, password


@sio.event
def botStart(sid, data):
    """Start the Zoom bot. data can be a meeting URL string or dict with meeting_url and bot_name."""
    global bot_instance, audio_sender_instance

    if bot_instance is not None:
        sio.emit('botStatus', {'status': 'already_running'}, room='cp')
        return

    # Parse input and validate URL first (before checking SDK availability)
    if isinstance(data, str):
        meeting_url = data
        bot_name = os.environ.get('BOT_NAME', 'Reactions')
    else:
        meeting_url = data.get('meeting_url', '')
        bot_name = data.get('bot_name', os.environ.get('BOT_NAME', 'Reactions'))

    meeting_id, meeting_pwd = parse_meeting_url(meeting_url)
    if not meeting_id:
        sio.emit('botStatus', {'status': 'error', 'message': 'Invalid meeting URL'}, room='cp')
        return

    try:
        from bot.meeting import ZoomBot, ZOOM_SDK_AVAILABLE
        from bot.sender import AudioSender
        from bot.mixer import AudioMixer
    except ImportError:
        sio.emit('botStatus', {'status': 'error', 'message': 'Bot modules not available'}, room='cp')
        return

    if not ZOOM_SDK_AVAILABLE:
        sio.emit('botStatus', {'status': 'error', 'message': 'Zoom SDK not available (Linux only)'}, room='cp')
        return

    # Create mixer and sender
    pcm_dir = os.path.join(BASE_DIR, 'pcm')
    mixer = AudioMixer(pcm_dir)
    audio_sender_instance = AudioSender(mixer, get_actions=lambda: actions_list)

    # Create and start bot
    def _bot_status_callback(status_dict):
        sio.emit('botStatus', status_dict, room='cp')

    bot_instance = ZoomBot(
        on_mic_ready=audio_sender_instance.set_zoom_sender,
        on_status=_bot_status_callback,
    )

    sio.emit('botStatus', {'status': 'starting'}, room='cp')
    bot_instance.start(meeting_id, meeting_pwd, bot_name)


@sio.event
def botStop(sid):
    """Stop the Zoom bot."""
    global bot_instance, audio_sender_instance

    if audio_sender_instance:
        audio_sender_instance.stop()
        audio_sender_instance = None

    if bot_instance:
        bot_instance.stop()
        bot_instance = None

    sio.emit('botStatus', {'status': 'stopped'}, room='cp')


# --- Auto-start bot from environment ---
def _auto_start_bot():
    """Auto-start bot if MEETING_URL is set in environment."""
    meeting_url = os.environ.get('MEETING_URL')
    if not meeting_url:
        return

    try:
        from bot.meeting import ZOOM_SDK_AVAILABLE
        if not ZOOM_SDK_AVAILABLE:
            print("[server] MEETING_URL set but Zoom SDK not available (Linux only)")
            return
    except ImportError:
        return

    def delayed_start():
        eventlet.sleep(2)
        botStart('auto', meeting_url)

    eventlet.spawn(delayed_start)


# --- Background task: broadcast actions_count every 1s ---
def broadcast_actions():
    while True:
        sio.emit('actions_count', actions_list, room='listeners')
        sio.sleep(1)


sio.start_background_task(broadcast_actions)

# --- Signal handling ---
def _shutdown(signum, frame):
    """Clean shutdown on SIGINT/SIGTERM."""
    global bot_instance, audio_sender_instance
    print("\n[server] Shutting down...")
    if audio_sender_instance:
        audio_sender_instance.stop()
    if bot_instance:
        bot_instance.stop()
    sys.exit(0)


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


# --- Run ---
def main():
    _auto_start_bot()
    print(f'listening on localhost:{APP_PORT}')
    eventlet.wsgi.server(eventlet.listen(('0.0.0.0', APP_PORT)), app)


if __name__ == '__main__':
    main()
