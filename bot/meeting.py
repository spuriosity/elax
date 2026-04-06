"""Zoom Meeting SDK integration for audio injection."""

import os
import threading

# zoom_meeting_sdk is Linux-only. Import conditionally.
try:
    import zoom_meeting_sdk as zoom
    import gi
    gi.require_version('GLib', '2.0')
    from gi.repository import GLib
    ZOOM_SDK_AVAILABLE = True
except ImportError:
    ZOOM_SDK_AVAILABLE = False

import jwt
from datetime import datetime, timedelta


def generate_jwt(client_id: str, client_secret: str) -> str:
    """Generate a JWT for Zoom Meeting SDK authentication."""
    iat = datetime.utcnow()
    exp = iat + timedelta(hours=24)
    return jwt.encode({
        "iat": iat,
        "exp": exp,
        "appKey": client_id,
        "tokenExp": int(exp.timestamp())
    }, client_secret, algorithm="HS256")


class ZoomBot:
    """Manages the Zoom Meeting SDK lifecycle: auth, join, VoIP, virtual mic."""

    def __init__(self, on_mic_ready=None, on_status=None):
        """
        Args:
            on_mic_ready: Callback invoked with (audio_sender) when the virtual
                          microphone is ready to accept PCM data.
            on_status: Optional callback invoked with a status dict at key
                       lifecycle points (e.g. {'status': 'in_meeting'}).
        """
        if not ZOOM_SDK_AVAILABLE:
            raise RuntimeError("zoom-meeting-sdk is not installed (Linux only)")

        self.on_mic_ready = on_mic_ready
        self.on_status = on_status
        self.audio_sender = None
        self.meeting_service = None
        self.auth_service = None
        self.setting_service = None
        self._glib_loop = None
        self._glib_thread = None

        # Store callback references to prevent GC
        self._auth_event = None
        self._meeting_event = None
        self._reminder_event = None
        self._audio_ctrl_event = None
        self._rec_event = None
        self._mic_event = None

    def _emit_status(self, status, message=None):
        """Emit a status update via the on_status callback if set."""
        if not self.on_status:
            return
        payload = {'status': status}
        if message:
            payload['message'] = message
        try:
            self.on_status(payload)
        except Exception as e:
            print(f"[bot] Error in on_status callback: {e}")

    def start(self, meeting_id: int, meeting_pwd: str, bot_name: str = "Reactions"):
        """Initialize SDK, authenticate, and join the meeting.

        Starts the GLib main loop in a background thread.
        """
        # Init SDK
        init_param = zoom.InitParam()
        init_param.strWebDomain = "https://zoom.us"
        init_param.strSupportUrl = "https://zoom.us"
        init_param.enableGenerateDump = True
        init_param.emLanguageID = zoom.SDK_LANGUAGE_ID.LANGUAGE_English
        init_param.enableLogByDefault = True
        result = zoom.InitSDK(init_param)
        if result != zoom.SDKERR_SUCCESS:
            raise RuntimeError(f"InitSDK failed: {result}")

        self.meeting_service = zoom.CreateMeetingService()
        self.setting_service = zoom.CreateSettingService()
        self.auth_service = zoom.CreateAuthService()

        # Meeting status callback
        self._meeting_event = zoom.MeetingServiceEventCallbacks(
            onMeetingStatusChangedCallback=self._on_meeting_status
        )
        self.meeting_service.SetEvent(self._meeting_event)

        # Auth callback
        self._meeting_id = meeting_id
        self._meeting_pwd = meeting_pwd
        self._bot_name = bot_name

        self._auth_event = zoom.AuthServiceEventCallbacks(
            onAuthenticationReturnCallback=self._on_auth
        )
        self.auth_service.SetEvent(self._auth_event)

        # Authenticate
        self._emit_status('authenticating')
        ctx = zoom.AuthContext()
        ctx.jwt_token = generate_jwt(
            os.environ['ZOOM_APP_CLIENT_ID'],
            os.environ['ZOOM_APP_CLIENT_SECRET']
        )
        self.auth_service.SDKAuth(ctx)

        # Start GLib loop in background thread
        self._glib_loop = GLib.MainLoop()
        self._glib_thread = threading.Thread(target=self._glib_loop.run, daemon=True)
        self._glib_thread.start()
        print(f"[bot] Zoom SDK initialized, authenticating...")

    def _on_auth(self, result):
        if result != zoom.AUTHRET_SUCCESS:
            print(f"[bot] Auth failed: {result}")
            self._emit_status('error', f'Authentication failed: {result}')
            return
        print("[bot] Authenticated, joining meeting...")
        self._emit_status('authenticated')
        self._emit_status('joining')
        self._join_meeting()

    def _join_meeting(self):
        join_param = zoom.JoinParam()
        join_param.userType = zoom.SDKUserType.SDK_UT_WITHOUT_LOGIN
        p = join_param.param
        p.meetingNumber = self._meeting_id
        p.userName = self._bot_name
        p.psw = self._meeting_pwd
        p.isVideoOff = True
        p.isAudioOff = False
        p.isMyVoiceInMix = False
        p.isAudioRawDataStereo = False
        sample_rate = int(os.environ.get('AUDIO_SAMPLE_RATE', 32000))
        if sample_rate == 48000:
            p.eAudioRawdataSamplingRate = zoom.AudioRawdataSamplingRate.AudioRawdataSamplingRate_48K
        else:
            p.eAudioRawdataSamplingRate = zoom.AudioRawdataSamplingRate.AudioRawdataSamplingRate_32K
        self.meeting_service.Join(join_param)
        self.setting_service.GetAudioSettings().EnableAutoJoinAudio(True)

    def _on_meeting_status(self, status, iResult):
        if status == zoom.MEETING_STATUS_INMEETING:
            print("[bot] In meeting, setting up audio pipeline...")
            self._emit_status('in_meeting')
            self._setup_audio()
        elif status == zoom.MEETING_STATUS_ENDED:
            print("[bot] Meeting ended")
            self._emit_status('ended')
            self.stop()
        elif status == zoom.MEETING_STATUS_FAILED:
            print(f"[bot] Meeting join failed: {iResult}")
            self._emit_status('error', f'Meeting join failed: {iResult}')
            self.stop()
        elif status == zoom.MEETING_STATUS_RECONNECTING:
            print("[bot] Reconnecting to meeting...")

    def _setup_audio(self):
        # Auto-accept consent dialogs
        self._reminder_event = zoom.MeetingReminderEventCallbacks(
            onReminderNotifyCallback=lambda content, handler: handler.Accept() if handler else None
        )
        self.meeting_service.GetMeetingReminderController().SetEvent(self._reminder_event)

        # Join VoIP (required for SDK > 6.3.5)
        audio_ctrl = self.meeting_service.GetMeetingAudioController()
        self._audio_ctrl_event = zoom.MeetingAudioCtrlEventCallbacks()
        audio_ctrl.SetEvent(self._audio_ctrl_event)
        audio_ctrl.JoinVoip()

        # Start raw recording after a short delay
        GLib.timeout_add_seconds(1, self._try_start_recording)

    def _try_start_recording(self):
        rec_ctrl = self.meeting_service.GetMeetingRecordingController()
        self._rec_event = zoom.MeetingRecordingCtrlEventCallbacks(
            onRecordPrivilegeChangedCallback=lambda can_rec: (
                GLib.timeout_add_seconds(1, self._try_start_recording) if can_rec else None
            )
        )
        rec_ctrl.SetEvent(self._rec_event)

        if rec_ctrl.CanStartRawRecording() != zoom.SDKERR_SUCCESS:
            print("[bot] Requesting recording privilege...")
            rec_ctrl.RequestLocalRecordingPrivilege()
            return False

        rec_ctrl.StartRawRecording()
        print("[bot] Raw recording started, setting up virtual mic...")

        # Set up virtual microphone
        audio_helper = zoom.GetAudioRawdataHelper()
        self._mic_event = zoom.ZoomSDKVirtualAudioMicEventCallbacks(
            onMicInitializeCallback=self._on_mic_init,
            onMicStartSendCallback=self._on_mic_start
        )
        audio_helper.setExternalAudioSource(self._mic_event)
        return False  # don't repeat GLib timeout

    def _on_mic_init(self, sender):
        self.audio_sender = sender
        print("[bot] Virtual mic initialized")

    def _on_mic_start(self):
        print("[bot] Virtual mic ready to send audio")
        self._emit_status('mic_ready')
        if self.on_mic_ready:
            self.on_mic_ready(self.audio_sender)

    def send_audio(self, pcm_data: bytes):
        """Send raw PCM audio data (s16le, 32kHz, mono) to the meeting."""
        if self.audio_sender:
            return self.audio_sender.send(pcm_data, 32000, zoom.ZoomSDKAudioChannel_Mono)
        return None

    def stop(self):
        """Leave meeting and clean up SDK resources."""
        try:
            if self.meeting_service:
                self.meeting_service.Leave(zoom.LEAVE_MEETING)
        except Exception as e:
            print(f"[bot] Error leaving meeting: {e}")
        try:
            if self._glib_loop:
                self._glib_loop.quit()
            zoom.DestroyMeetingService(self.meeting_service)
            zoom.DestroySettingService(self.setting_service)
            zoom.DestroyAuthService(self.auth_service)
            zoom.CleanUPSDK()
        except Exception as e:
            print(f"[bot] Error during cleanup: {e}")
        print("[bot] Stopped")
