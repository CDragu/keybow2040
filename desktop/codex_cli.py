"""Locate and launch the separately installed Codex CLI."""
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys


def find_codex():
    if binary := shutil.which("codex"):
        return binary
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        localappdata = os.environ.get("LOCALAPPDATA")
        candidates = []
        if appdata:
            candidates.append(Path(appdata, "npm", "codex.cmd"))
        if localappdata:
            candidates.append(Path(localappdata, "Microsoft", "WinGet", "Links", "codex.exe"))
        return next((str(path) for path in candidates if path.is_file()), None)
    return None


def launch(popen, argv, **kwargs):
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return popen(argv, **kwargs)
    # PyInstaller points the DLL search path at Qt's bundled libraries.
    # External executables should inherit the standard Windows search path.
    set_dll_directory = ctypes.WinDLL("kernel32", use_last_error=True).SetDllDirectoryW
    set_dll_directory.argtypes = (ctypes.c_wchar_p,)
    if not set_dll_directory(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return popen(argv, **kwargs)
    finally:
        set_dll_directory(sys._MEIPASS)


def start_login(binary):
    return launch(subprocess.Popen, [binary, "login"],
                  creationflags=subprocess.CREATE_NEW_CONSOLE)
