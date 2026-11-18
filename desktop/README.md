# Keybow Codex Desktop

Keybow Codex is a Windows desktop app for the original 12-key Keybow on a Pi
Zero WH. It includes a virtual keypad, an offline simulator, and a connection to
the local Codex CLI.

## Install on Windows

1. In [Windows desktop installer runs](https://github.com/CDragu/keybow2040/actions/workflows/windows-installer.yml), choose a successful run for the branch or revision you want. Download and unzip **Keybow-Codex-Windows-x64-unsigned**.
2. Run `KeybowCodex-0.4.0-x64-Setup.exe`. The installer works per user and bundles Python and Qt. Select **Open Keybow Codex when I sign in to Windows** if you want the Start menu app to launch at sign-in. You can change this option by running the installer again.
3. To use real Codex, install the [Codex CLI](https://github.com/openai/codex/releases) for your Windows architecture. For a standalone binary, extract it to a permanent folder and select the executable under **Settings → Choose Codex CLI**. Launch **Settings → Sign in to Codex** and finish sign-in in your browser. The CLI opens its own sign-in window. If you already signed in to the CLI, go directly to **Real Codex (local)**.
4. For physical keys, use **Settings → Set up Pi pairing**. Follow [Pi pairing and Windows Firewall setup](../docs/keybow-pairing.md). The physical listener starts when you click **Enable physical Pi link**.

The CI installer is unsigned. Windows may show an Unknown Publisher or SmartScreen
prompt. Verify the GitHub Actions run and compare the SHA-256 of the downloaded
installer with its accompanying `.sha256` file before running it. The checksum
checks the file against that run. A signed distribution requires a publisher
certificate and an Authenticode signing step.

## Run a supervised turn

1. Choose **Real Codex (local)** and select a project folder you own.
2. Leave **Read-only (recommended)** selected and ask “Explain what this project does without changing files.”
3. When a review appears, read the working directory, reason, and command, diff, or network destination. Press key **0** to approve once, **1** when session approval is explicitly offered, or **2** to decline. The same choices are available on screen.
4. Use key **3** or **Stop turn** to interrupt. Use key **4** or **Acknowledge and reset** after the result. Follow-up prompts in the same folder reuse the thread while the app is running.

Use **Workspace write** to allow Codex to write inside the chosen project under
its sandbox. This mode can make in-sandbox edits without a separate prompt.
Standalone permissions and MCP elicitation requests are rejected by the desktop.

The physical review keys arm after the foreground card renders and the paired Pi
reports its held keys. A disconnect or backgrounded window disarms the keypad.
Reconnecting or returning to the window requires a fresh key-down. Windows lock
declines the real pending request and blocks approvals until unlock. A real
displayed request expires after two minutes. The desktop audit records time,
request identity and kind, source, and decision in `approvals.sqlite3`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Codex CLI was not found | Use **Settings → Choose Codex CLI**, then sign in. For npm installs, the app also checks the usual per-user npm directory. |
| Pi stays disconnected | Confirm that both devices use the same trusted private LAN, the Pi bridge is running, and the private inbound firewall rule matches the executable and pairing port. |
| Pairing says the Windows address is unavailable | Check the address of the active LAN adapter. If DHCP changed the address, reserve it on the router or create new pairing files and update the Pi. |
| Physical approval is inactive | Put the desktop review in the foreground, unlock Windows, and release any held approval keys before pressing one. If the request expired or the turn stopped, wait for a new request. |
| Windows pairing key cannot be read | Use the original Windows user profile. For a different profile or PC, archive the two pairing files and pair again. |
| Preferences fail to load | Preserve the file for recovery. Open **Settings → Open data folder**, rename `settings.json`, and restart to use defaults. |

**Settings → Open data folder** opens `%USERPROFILE%\.keybow2040`. Updates and
uninstall preserve settings, pairing, and audit data. Existing D2 `windows.json`
migrates to per-user Windows protection the first time the physical listener
starts. The Pi's existing file continues to work. Preferences remember the project,
backend, and optional CLI location. Each launch defaults to read-only and keeps
the physical listener disabled.

## Build and test from source

For source development, launch `desktop\run-windows.bat` from a checkout. It
sets up the desktop virtual environment. Choose **Simulator (offline)** to
exercise the scripted approvals and status changes without a Pi or Codex login.

To build an x64 installer with Python 3.12 and Inno Setup 6 installed:

```powershell
python -m pip install -r desktop/packaging/requirements.txt
.\desktop\packaging\build-windows.ps1
```

The build writes the installer and checksum under `dist\windows`. For a signed
release, use the publisher certificate to sign the app executable and configure
[Inno Setup SignTool](https://jrsoftware.org/ishelp/topic_setup_signtool.htm) for
the installer and uninstaller. Generate the checksum after signing.

Run the full offscreen suite on Linux:

```bash
export QT_QPA_PLATFORM=offscreen
python -m unittest discover -s desktop/tests -v
python -m unittest discover -s keybow_net/tests -v
python -m unittest discover -s pi/tests -v
```

Windows CI builds, installs, starts, and uninstalls the package. For physical
acceptance, run a live Codex turn from the Start menu on a clean Windows PC.
Exercise two distinct requests, decline one, approve the other after reviewing
it, and check the audit. Repeat with the Pi unplugged, Windows locked, a held key
across reconnect, and Codex stopped midway through a request.

Reference: [Codex App Server protocol](https://learn.chatgpt.com/docs/app-server).
