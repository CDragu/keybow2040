# Pair the original 12-key Pi Keybow with Windows

This connects the **original Pi Zero WH Keybow** to the Windows desktop via
authenticated local TCP. The Pi reads 12 independent switches and drives
RGB LEDs; it never runs Codex or receives prompts, commands or credentials.

## Security

- A generated 256-bit shared key mutually authenticates each connection.
- Every frame is HMAC-SHA256 signed and sequence-numbered with a new
  per-connection key. Tampering and replay are rejected.
- **Not encrypted:** this is not TLS. Use a trusted LAN, not public Wi-Fi.
- Windows only listens after clicking **Enable physical Pi link**.
- TCP port 8765 must not be accessible from the Internet. Keep both
  pairing JSON files private and outside Git.

## Windows setup

1. In Windows **Settings → Network & internet**, open the active adapter
   and locate its IPv4 address. Use a trusted private network.
2. In Keybow Codex, choose **Settings → Set up Pi pairing**. Enter the
   Windows IPv4 address and TCP port (default 8765). Save the Pi export
   to a private local folder. The listener binds to the selected address.
3. Transfer the Pi export as described below and start the Pi bridge.
   On Windows, click **Enable physical Pi link**.

The desktop stores its credential in `%USERPROFILE%\.keybow2040\windows.json`
with per-user Windows DPAPI protection. The Pi export needs to stay private
until it is copied and deleted. Existing D2 credentials migrate on the first
successful read by the Windows listener.

For a source checkout, the existing command also creates pairing files:

~~~powershell
py -3 -m keybow_net.pairing create --windows-ip 192.168.1.23
~~~

Replace the example IP with your Windows LAN address. The generator refuses
to overwrite an existing pairing. Stop the listener before archiving old
files and creating new ones. Update the credential on both devices together.

## Copy credentials to Pi

First run on the Raspberry Pi (change account/hostname as necessary):

~~~bash
mkdir -p ~/.keybow2040
chmod 700 ~/.keybow2040
~~~

Transfer the exported file over SSH with your usual SFTP client. For the
default export location, the equivalent Windows PowerShell commands are:

~~~powershell
scp "$HOME\.keybow2040\pi-pairing.json" pi@raspberrypi.local:~/.keybow2040/pi.json
Remove-Item "$HOME\.keybow2040\pi-pairing.json"
~~~

Finally, on the Pi:

~~~bash
chmod 600 ~/.keybow2040/pi.json
~~~

Never put credentials in Git, email or shared folders.

## Windows Firewall

On the Windows PC, use **Windows Defender Firewall with Advanced Security**
from Start. Creating an inbound rule requires administrator rights.

1. Choose **Inbound Rules → New Rule → Custom**.
2. Choose **This program path** and use
   `%LOCALAPPDATA%\Programs\Keybow Codex\KeybowCodex.exe`, or the executable
   in your chosen installation directory. For a source install, use the
   Python executable running the desktop.
3. Choose **TCP** and the pairing **local port** (8765 by default). Under
   **Scope**, set the remote address to the Pi’s LAN IPv4 address.
4. Choose **Allow the connection**, select **Private** as the profile,
   and name it **Keybow Pi Link**.

If you uninstall Keybow Codex, remove this rule from **Inbound Rules**.
Keep the listener on your trusted private LAN.

## Pi bridge

Follow the original GPIO/SPI/keybow installation in
[Pi setup](../pi/README.md). Use a checkout containing the Pi bridge.
Then from the repository root:

~~~bash
.venv/bin/python -m pi.bridge --backend real
~~~

For a network-only smoke test without GPIO:

~~~bash
python3 -m pi.bridge --backend mock --duration 30
~~~

Do not run the diagnostic service and bridge service simultaneously (both
would control the same hardware). You can install
[the systemd bridge unit](../pi/keybow-bridge@.service) only after
successful manual testing. To install the optional autostart unit after
stopping the diagnostic service, run:

~~~bash
sudo systemctl disable --now "keybow-diag@$USER.service" 2>/dev/null || true
sudo cp pi/keybow-bridge@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "keybow-bridge@$USER.service"
journalctl -u "keybow-bridge@$USER.service" -f
~~~

The unit assumes the repository is in /home/YOUR_USER/keybow2040 and the
Python venv is .venv, just like the diagnostic service. The original
keybow==0.0.3 library may require
work on newer Pi OS images; this still needs physical verification.

## Windows desktop

Launch desktop/run-windows.bat and click **Enable physical Pi link** under
the virtual keys. It loads the Windows pairing file and waits for the Pi.

The desktop and physical keypad use the same RGB effect function. Multiple
held keys are tracked independently, and each key-down/release is transmitted
without suppressing other keys.

Approvals are deliberately stricter than other keys: the desktop window must
be in the foreground with the review card visible, at which point a fresh
unpredictable arming token is sent to the Pi. A held/queued press cannot
approve anything: release and press again *after* the Pi has received the
token. Windows still validates the current request, allowed decision,
connection epoch and UI state before answering Codex. Near-simultaneous approval
presses (within a short 90ms decision window) form an ambiguous chord and
are **ignored**, rather than accepting whichever key arrived first. Release
all approval keys before trying again. Disconnect/focus loss disarms it.

- Key 0: approve once (only when offered)
- Key 1: approve for session (only when offered)
- Key 2: decline
- Key 3: cancel current turn
- Key 4: acknowledge finished result
- Keys 5-11: reserved; still render ripples / press feedback

## RGB animation table

| Event | Light effect |
| --- | --- |
| Offline | Dim amber breathing |
| Pair / reconnect | Brief green welcome sweep |
| Idle | Dim blue |
| Working | Travelling amber pulse |
| Approval needed | Pulsing red keys 0–3 |
| Key pressed | Cyan ripple, independently layered |
| Multiple keys held | Every held key highlighted |
| Approval accepted | Green sweep |
| Declined/failed | Red flashes and sweep |
| Cancelled | Orange sweep |
| Completed | Green wave followed by solid green |

LED frames run at up to 30 fps; unchanged frames are not pushed over SPI.

Automated tests cover real localhost TCP framing, auth, replay rejection,
overlapping press events, stale approvals, effects, and mocked UI. The
actual Pi hardware and Windows network still need to be tested.

**Hardware target:** original 12-key Pi Zero WH Keybow, not the different
16-key RP2040 Keybow 2040.
