"""Windows session lock notifications and native foreground verification."""
import ctypes
from ctypes import wintypes
import sys

WM_WTSSESSION_CHANGE = 0x02B1
SESSION_LOCKED = {2, 4, 6, 7}  # console/remote disconnect, logoff, lock
SESSION_UNLOCKED = {8}


class WindowsSession:
    def __init__(self, hwnd):
        self.hwnd = int(hwnd)
        self.registered = False
        if sys.platform == "win32":
            self.api = ctypes.WinDLL("wtsapi32", use_last_error=True)
            self.api.WTSRegisterSessionNotification.argtypes = (
                wintypes.HWND, wintypes.DWORD)
            self.api.WTSUnRegisterSessionNotification.argtypes = (wintypes.HWND,)
            self.foreground = ctypes.WinDLL("user32").GetForegroundWindow
            self.foreground.restype = wintypes.HWND
            self.registered = bool(self.api.WTSRegisterSessionNotification(self.hwnd, 0))

    def is_foreground(self):
        return sys.platform != "win32" or (
            self.registered and self.foreground() == self.hwnd)

    def close(self):
        if self.registered:
            self.api.WTSUnRegisterSessionNotification(self.hwnd)
            self.registered = False
