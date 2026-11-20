"""Spotify routing and worker behavior without touching a real player."""
import asyncio
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from desktop.spotify import (
    PLAYBACK_METHODS, SPOTIFY_KEYS, SpotifyController, change_volume,
    control_playback, is_spotify, windows_control,
)


def media_session(source):
    return SimpleNamespace(source_app_user_model_id=source, **{
        method: AsyncMock(return_value=True) for method in PLAYBACK_METHODS.values()})


def audio_session(name, level=0.5, muted=False):
    volume = Mock()
    volume.GetMasterVolume.return_value = level
    volume.GetMute.return_value = muted
    return SimpleNamespace(Process=SimpleNamespace(name=lambda: name),
                           SimpleAudioVolume=volume)


class SpotifyTests(unittest.TestCase):
    def test_desktop_and_store_ids_only(self):
        for source in ("Spotify.exe", "SPOTIFY.EXE",
                       "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"):
            self.assertTrue(is_spotify(source))
        for source in ("chrome.exe", "Spotify", "not-spotify.exe", "",
                       "SpotifyAB.SpotifyMusic_test!OtherApp"):
            self.assertFalse(is_spotify(source))

    def test_each_playback_command_targets_spotify_even_with_browser_first(self):
        for action, method in PLAYBACK_METHODS.items():
            with self.subTest(action=action):
                browser, spotify = media_session("chrome.exe"), media_session("Spotify.exe")
                manager = SimpleNamespace(get_sessions=lambda: [browser, spotify])
                asyncio.run(control_playback(manager, action))
                getattr(spotify, method).assert_awaited_once_with()
                for other in PLAYBACK_METHODS.values():
                    getattr(browser, other).assert_not_awaited()

    def test_missing_or_rejecting_session_is_an_error(self):
        browser = media_session("chrome.exe")
        with self.assertRaisesRegex(RuntimeError, "Open Spotify"):
            asyncio.run(control_playback(
                SimpleNamespace(get_sessions=lambda: [browser]), "play_pause"))
        browser.try_toggle_play_pause_async.assert_not_awaited()
        spotify = media_session("Spotify.exe")
        spotify.try_skip_next_async.return_value = False
        with self.assertRaisesRegex(RuntimeError, "did not accept"):
            asyncio.run(control_playback(
                SimpleNamespace(get_sessions=lambda: [spotify]), "next"))

    def test_volume_changes_only_spotify_and_clamps(self):
        other = audio_session("browser.exe")
        loud, quiet = audio_session("Spotify.exe", .99), audio_session("spotify.exe", .01)
        sessions = [other, loud, quiet, SimpleNamespace(Process=None)]
        change_volume(sessions, "volume_up")
        loud.SimpleAudioVolume.SetMasterVolume.assert_called_once_with(1.0, None)
        self.assertAlmostEqual(quiet.SimpleAudioVolume.SetMasterVolume.call_args.args[0], .06)
        loud.SimpleAudioVolume.SetMasterVolume.reset_mock()
        quiet.SimpleAudioVolume.SetMasterVolume.reset_mock()
        change_volume(sessions, "volume_down")
        quiet.SimpleAudioVolume.SetMasterVolume.assert_called_once_with(0.0, None)
        self.assertAlmostEqual(loud.SimpleAudioVolume.SetMasterVolume.call_args.args[0], .94)
        other.SimpleAudioVolume.assert_not_called()
        self.assertEqual(other.SimpleAudioVolume.mock_calls, [])

    def test_mute_uses_one_state_for_multiple_spotify_sessions(self):
        sessions = [audio_session("Spotify.exe", muted=True),
                    audio_session("Spotify.exe", muted=False)]
        self.assertEqual(change_volume(sessions, "mute"), "Spotify muted.")
        for s in sessions:
            s.SimpleAudioVolume.SetMute.assert_called_once_with(1, None)
            s.SimpleAudioVolume.GetMute.return_value = True
        self.assertEqual(change_volume(sessions, "mute"), "Spotify unmuted.")
        for s in sessions:
            self.assertEqual(s.SimpleAudioVolume.SetMute.call_args.args, (0, None))

    def test_no_audio_session_or_unknown_action(self):
        with self.assertRaisesRegex(RuntimeError, "audio session"):
            change_volume([audio_session("chrome.exe")], "volume_up")
        with self.assertRaises(ValueError):
            windows_control("unknown")

    def test_worker_runs_off_caller_and_does_not_queue_repeated_commands(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        caller = threading.get_ident()

        def execute(action):
            calls.append((action, threading.get_ident()))
            started.set()
            release.wait(2)
            return "Finished"

        controller = SpotifyController(execute)
        try:
            self.assertTrue(controller.submit(6))
            self.assertTrue(started.wait(1))
            self.assertFalse(controller.submit(6))
            self.assertFalse(controller.submit(0))
            self.assertEqual(controller.poll(), [])
        finally:
            release.set()
            controller._worker.join(2)
        self.assertEqual(calls[0][0], "play_pause")
        self.assertNotEqual(calls[0][1], caller)
        self.assertEqual(controller.poll(), ["Finished"])
        self.assertFalse(controller.busy)
        controller.close()
        self.assertFalse(controller.submit(7))

    def test_worker_errors_reported_and_next_press_can_retry(self):
        for error, message in ((RuntimeError("no player"), "no player"),
                               (TimeoutError(), "timed out"),
                               (ImportError(), "components are missing")):
            controller = SpotifyController(Mock(side_effect=error))
            self.assertTrue(controller.submit(5))
            controller._worker.join(2)
            self.assertIn(message, controller.poll()[0])
            self.assertTrue(controller.submit(5))
            controller._worker.join(2)
            controller.close()

    def test_close_drops_late_results_and_prevents_more_controls(self):
        started, release = threading.Event(), threading.Event()

        def execute(action):
            started.set()
            release.wait(2)
            return "late"

        controller = SpotifyController(execute)
        try:
            controller.submit(11)
            self.assertTrue(started.wait(1))
            controller.close()
        finally:
            release.set()
            controller._worker.join(2)
        self.assertEqual(controller.poll(), [])
        self.assertFalse(controller.submit(11))

    @unittest.skipIf(sys.platform == "win32", "Non-Windows behavior")
    def test_unavailable_platform_does_not_start_worker(self):
        controller = SpotifyController()
        self.assertFalse(controller.supported)
        for key in SPOTIFY_KEYS:
            self.assertFalse(controller.submit(key))

    @unittest.skipUnless(sys.platform == "win32", "Windows native media imports")
    def test_native_components_enumerate_without_sending_any_controls(self):
        # Mirrors installed-package smoke testing; needs neither Spotify nor login.
        messages = []

        def execute(action):
            return windows_control("check")

        controller = SpotifyController(execute)
        try:
            controller.submit(6)
            controller._worker.join(8)
            self.assertFalse(controller.busy)
            messages = controller.poll()
            self.assertEqual(messages, ["Spotify components ready."])
        finally:
            controller.close()


if __name__ == "__main__":
    unittest.main()
