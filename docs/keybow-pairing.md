# D2 — Windows / original 12-key Pi Keybow pairing

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

From PowerShell, in the checkout (use main after the PR is merged):

~~~powershell
git fetch
git switch feature/d2-pi-windows-link-led-effects
py -3 -m keybow_net.pairing create --windows-ip 192.168.1.23
~~~

Replace 192.168.1.23 with your actual Windows LAN IPv4 address (ipconfig).

This creates two files without printing the shared secret:

- Windows listener: $HOME\.keybow2040\windows.json
- Temporary Pi credential: $HOME\.keybow2040\pi-pairing.json

The generator refuses to overwrite an existing pairing. Treat rotating a
shared key as a deliberate re-pairing of both machines.

## Copy credentials to Pi

First run on the Raspberry Pi (change account/hostname as necessary):

~~~bash
mkdir -p ~/.keybow2040
chmod 700 ~/.keybow2040
~~~

From Windows PowerShell:

~~~powershell
scp "$HOME\.keybow2040\pi-pairing.json" pi@raspberrypi.local:~/.keybow2040/pi.json
Remove-Item "$HOME\.keybow2040\pi-pairing.json"
~~~

Finally, on the Pi:

~~~bash
chmod 600 ~/.keybow2040/pi.json
~~~

Never put credentials in Git, email or shared folders.

## Windows firewall

If the Pi cannot connect, open elevated PowerShell and permit only Private
network inbound TCP. Prefer specifying the Pi's IP with -RemoteAddress.

~~~powershell
New-NetFirewallRule -DisplayName "Keybow Pi Link" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private
~~~

To remove: Remove-NetFirewallRule -DisplayName "Keybow Pi Link".
Do not use a Public firewall rule or router port forwarding.

## Pi bridge

Follow the original GPIO/SPI/keybow installation in
[Pi setup](../pi/README.md). Pull the D2 branch on the Pi too.
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
