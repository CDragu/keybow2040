"""Spotify-only Windows controls, dispatched away from the Qt thread.

No Web API credentials or global media key injection. WinRT selects Spotify's
desktop media session; Core Audio adjusts only Spotify.exe on the default output.
"""
import asyncio
import os
import queue
import sys
import threading

if sys.platform == "win32":
    # comtypes initializes the first importing thread as STA. Import on the UI
    # thread so subsequent worker imports cannot change their WinRT MTA.
    try:
        import comtypes
    except ImportError:
        pass  # Missing optional components are reported when a control is used.


SPOTIFY_KEYS = {
    5: ("previous", "Spotify previous"),
    6: ("play_pause", "Spotify play/pause"),
    7: ("next", "Spotify next"),
    8: ("volume_down", "Spotify volume −"),
    9: ("volume_up", "Spotify volume +"),
    10: ("mute", "Spotify mute"),
    11: ("open", "Open Spotify"),
}
PLAYBACK_METHODS = {
    "previous": "try_skip_previous_async",
    "play_pause": "try_toggle_play_pause_async",
    "next": "try_skip_next_async",
}


def is_spotify(source):
    source = source.casefold()
    return source == "spotify.exe" or (
        source.startswith("spotifyab.spotifymusic_")
        and source.partition("!")[2] == "spotify")


async def control_playback(manager, action):
    # Never use get_current_session(): a browser/video may be the current player.
    session = next((s for s in manager.get_sessions()
                    if is_spotify(s.source_app_user_model_id)), None)
    if session is None:
        raise RuntimeError("Open Spotify and start a track once, then try again.")
    if not await getattr(session, PLAYBACK_METHODS[action])():
        raise RuntimeError("Spotify did not accept this playback control.")


def change_volume(sessions, action):
    volumes = [s.SimpleAudioVolume for s in sessions
               if s.Process and s.Process.name().casefold() == "spotify.exe"]
    if not volumes:
        raise RuntimeError("Play Spotify on this PC to create an audio session.")
    if action == "mute":
        # Spotify can have several sessions. Toggle them to the same mute state.
        muted = not all(v.GetMute() for v in volumes)
        for volume in volumes:
            volume.SetMute(int(muted), None)
        return "Spotify muted." if muted else "Spotify unmuted."
    delta = 0.05 if action == "volume_up" else -0.05
    for volume in volumes:
        volume.SetMasterVolume(max(0.0, min(1.0, volume.GetMasterVolume() + delta)), None)
    return "Spotify volume +5%." if delta > 0 else "Spotify volume −5%."


def windows_control(action):
    """Runs only on the Spotify worker; COM objects stay on this thread."""
    if action not in {item[0] for item in SPOTIFY_KEYS.values()} | {"check"}:
        raise ValueError("Unknown Spotify action.")
    if action == "open":
        os.startfile("spotify:")
        return "Opened Spotify."
    # These imports are Windows-only. Explicit MTA initialization also allows
    # Core Audio and WinRT to coexist on the worker regardless of import order.
    from winrt.runtime import ApartmentType, init_apartment, uninit_apartment
    init_apartment(ApartmentType.MULTI_THREADED)
    try:
        if action in PLAYBACK_METHODS or action == "check":
            from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

            async def send():
                manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
                if action == "check":
                    list(manager.get_sessions())  # exercise dynamically imported WinRT types
                else:
                    await control_playback(manager, action)

            async def bounded():
                await asyncio.wait_for(send(), timeout=3)

            asyncio.run(bounded())
            if action != "check":
                return "Spotify: " + action.replace("_", "/") + "."
        from pycaw.pycaw import AudioUtilities
        if action == "check":
            # CI may have no audio endpoint. Activate Core Audio without asking
            # for a default speaker or changing playback/volume.
            AudioUtilities.GetDeviceEnumerator()
            return "Spotify components ready."
        return change_volume(AudioUtilities.GetAllSessions(), action)
    finally:
        uninit_apartment()


class SpotifyController:
    """At most one command in flight; no queued toggles after a slow response.

    Results are consumed by the UI's timer. Construction and all methods except
    the worker body are called on the UI thread. Tests inject an executor.
    """
    def __init__(self, execute=None):
        self.supported = execute is not None or sys.platform == "win32"
        self._execute = execute or windows_control
        self._worker = None
        self._closed = threading.Event()
        self._results = queue.Queue()

    @property
    def busy(self):
        return self._worker is not None and self._worker.is_alive()

    def submit(self, index):
        if (index not in SPOTIFY_KEYS or not self.supported
                or self._closed.is_set() or self.busy):
            return False
        action = SPOTIFY_KEYS[index][0]

        def run():
            try:
                message = self._execute(action)
            except TimeoutError:
                message = "Spotify timed out. Try again."
            except ImportError:
                message = "Spotify components are missing. Reinstall the desktop dependencies."
            except Exception as exc:
                message = f"Spotify control failed: {exc}"
            if not self._closed.is_set():
                self._results.put(message)

        self._worker = threading.Thread(target=run, name="spotify-control", daemon=True)
        self._worker.start()
        return True

    def poll(self):
        messages = []
        while True:
            try:
                messages.append(self._results.get_nowait())
            except queue.Empty:
                return messages

    def close(self):
        self._closed.set()
