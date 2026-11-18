"""Versioned desktop preferences and per-user Windows pairing protection."""
import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from keybow_net.pairing import CONFIG_DIR, WINDOWS_CONFIG, create_pairing, load_config
from keybow_net.protocol import check_key


SETTINGS_FILE = CONFIG_DIR / "settings.json"
SETTINGS_VERSION = 1
PAIRING_VERSION = 1


def _read_json(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return value


def _replace_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=f".{path.name}-", delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class DesktopSettings:
    project: str = ""
    backend: str = "simulator"
    codex_path: str = ""

    @classmethod
    def load(cls, path=SETTINGS_FILE):
        try:
            data = _read_json(path)
        except FileNotFoundError:
            return cls()
        version = data.get("version", 0)
        if type(version) is not int or version not in (0, SETTINGS_VERSION):
            raise ValueError("These desktop preferences require a different app version.")
        settings = cls(data.get("project", ""), data.get("backend", "simulator"),
                       data.get("codex_path", ""))
        if (not isinstance(settings.project, str) or not isinstance(settings.codex_path, str)
                or settings.backend not in ("simulator", "real")):
            raise ValueError("The desktop preferences contain invalid values.")
        if version == 0:
            settings.save(path)
        return settings

    def save(self, path=SETTINGS_FILE):
        _replace_json(path, {"version": SETTINGS_VERSION, "project": self.project,
                             "backend": self.backend, "codex_path": self.codex_path})


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD),
                ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, *, decrypt=False):
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = (ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(_Blob))
    operation.restype = wintypes.BOOL
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = _Blob(len(data), buffer), _Blob()
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    free = ctypes.WinDLL("kernel32").LocalFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        free(result.data)


def load_windows_pairing(path=WINDOWS_CONFIG):
    if sys.platform != "win32":
        return load_config(path, server=True)

    path = Path(path).expanduser()
    data = _read_json(path)
    host, port = data.get("host"), data.get("port")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("Configuration must specify a host.")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Configuration has an invalid port.")
    version = data.get("version", 0)
    if type(version) is int and version == 0:
        key = check_key(data.get("key"))
        protected = base64.b64encode(_dpapi(key)).decode("ascii")
        _replace_json(path, {"version": PAIRING_VERSION, "host": host,
                             "port": port, "key_dpapi": protected})
    elif type(version) is int and version == PAIRING_VERSION:
        try:
            protected = base64.b64decode(data["key_dpapi"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("The protected Windows pairing key is malformed.") from exc
        key = _dpapi(protected, decrypt=True)
        if len(key) != 32:
            raise ValueError("The protected Windows pairing key has an invalid length.")
    else:
        raise ValueError("Unsupported Windows pairing configuration version.")
    return host, port, key


def pair_device(windows_ip, pi_path, port, windows_path=WINDOWS_CONFIG):
    windows_path, pi_path = create_pairing(
        windows_ip, windows_path, pi_path, port, bind_host=windows_ip)
    try:
        load_windows_pairing(windows_path)
    except (OSError, ValueError):
        windows_path.unlink(missing_ok=True)
        pi_path.unlink(missing_ok=True)
        raise
    return pi_path
