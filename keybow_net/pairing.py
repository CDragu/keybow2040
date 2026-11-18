"""Create one-time shared-key configuration files outside the Git repository.

Run on Windows:
  python -m keybow_net.pairing create --windows-ip 192.168.1.23
Then securely copy the generated pi-pairing.json to ~/.keybow2040/pi.json
on the Pi and delete the temporary Windows-side pi-pairing.json.
"""
import argparse
import json
import os
from pathlib import Path
import secrets

from keybow_net.protocol import check_key

CONFIG_DIR = Path.home() / ".keybow2040"
WINDOWS_CONFIG = CONFIG_DIR / "windows.json"
PI_CONFIG = CONFIG_DIR / "pi.json"


def write_config(path, data, *, overwrite=False):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if os.name != "nt":
        os.chmod(path, 0o600)
    return path


def load_config(path, *, server=False):
    with Path(path).expanduser().open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("Config must be a JSON object.")
    secret = check_key(data.get("key"))
    host = data.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("Configuration must specify host.")
    port = data.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Configuration has an invalid port.")
    return host, port, secret


def create_pairing(windows_ip, windows_path=WINDOWS_CONFIG,
                   pi_path=None, port=8765, *, bind_host="0.0.0.0"):
    if not windows_ip or not windows_ip.strip():
        raise ValueError("Provide the Windows LAN IP to which the Pi will connect.")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Port must be 1-65535.")
    windows_path = Path(windows_path).expanduser()
    pi_path = Path(pi_path or windows_path.parent / "pi-pairing.json").expanduser()
    if windows_path.resolve() == pi_path.resolve():
        raise ValueError("The Windows and Pi configuration paths must differ.")
    if windows_path.exists() or pi_path.exists():
        raise FileExistsError("Pairing already exists. Move/remove the existing files explicitly before replacing it.")
    secret = secrets.token_hex(32)
    write_config(windows_path, {"host": bind_host, "port": port, "key": secret})
    try:
        write_config(pi_path, {"host": windows_ip.strip(), "port": port, "key": secret})
    except BaseException:
        windows_path.unlink(missing_ok=True)
        raise
    return windows_path, pi_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("create", help="generate local Windows and Pi config files")
    cmd.add_argument("--windows-ip", required=True, help="Windows PC address reachable from Pi")
    cmd.add_argument("--port", type=int, default=8765)
    cmd.add_argument("--windows-config", default=str(WINDOWS_CONFIG))
    cmd.add_argument("--pi-output", default=None)
    args = parser.parse_args(argv)
    if args.command == "create":
        a, b = create_pairing(args.windows_ip, args.windows_config, args.pi_output,
                              args.port)
        print(f"Windows config saved: {a}")
        print(f"Pi config to copy securely: {b}")
        print("Copy Pi config to ~/.keybow2040/pi.json on the Pi, then DELETE the temporary Pi config on Windows.")
        print("Never commit or share either file. The key is NOT printed.")
        

if __name__ == "__main__":
    main()
