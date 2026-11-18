# Original 12-key Keybow — Pi diagnostic

This is a **new, separate** Raspberry Pi Zero WH experiment. The existing
`firmware/code.py` targets the 16-key RP2040 Keybow 2040 and is unchanged.

This first milestone checks all **12 switches** and their **12 RGB LEDs**.
This is the standalone **diagnostic**. For the new authenticated Pi–Windows bridge and shared RGB effects, see [pi/bridge.py](bridge.py) and [D2 pairing instructions](../docs/keybow-pairing.md).

## Hardware and OS

- Original Pimoroni Keybow (3 columns x 4 rows), Raspberry Pi Zero WH.
- Raspberry Pi OS Lite **32-bit** on microSD, with SSH enabled if desired.
- A suitable Pi power supply; Wi-Fi is optional for this diagnostic.
- This program **cannot run on the legacy `Old-Pi-OS/` image** because that
  image is a custom Lua/firmware setup, not a standard Python/systemd OS.

The code wraps the official [Pimoroni `keybow-python` library](https://github.com/pimoroni/keybow-python):
BCM GPIO key interrupts and APA102 LEDs over SPI. The library is old; check
compatibility on the actual Pi OS release and report failures before relying
on unattended startup. Nothing here requires USB HID gadget configuration.

## Install on a fresh Raspberry Pi OS Lite image

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip python3-rpi.gpio python3-spidev
sudo raspi-config nonint do_spi 0
sudo usermod -aG gpio,spi "$USER"  # log out and back in for group changes

git clone https://github.com/CDragu/keybow2040.git
cd keybow2040
git switch main
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install keybow==0.0.3
```

**Note:** Python/RPi.GPIO and `keybow==0.0.3` need to be verified on the
actual OS image. If the platform lacks usable GPIO edge interrupts, the
next step is a maintained GPIO backend, not changes to Codex permissions.

## Run manually

```bash
# No Raspberry Pi needed: exercises all 12 keys with a fake device
.venv/bin/python -m unittest discover -s pi/tests -v
.venv/bin/python -m pi.service --backend mock --demo --duration 0

# On the original hardware: red, green, blue LED sweep then key diagnostics
.venv/bin/python -m pi.service --backend real
```

When running on hardware, each key starts dim in a column colour; pressing it
turns it bright green, releasing it returns to its dim colour. Every press
and release is logged as `key NN PRESSED` or `key NN RELEASED`. Stop with Ctrl+C
and all LEDs turn off. `--skip-led-test` skips the three-colour startup sweep.
`--duration 30` stops automatically after 30 seconds.

With the GPIO header to the left and the USB cable to the right, indices are:

```text
  09  10  11
  06  07  08
  03  04  05
  00  01  02
```

### Expected checks

1. Red, green, then blue illumination across all 12 keys at startup.
2. Every key 00–11 produces exactly one press and release per action.
3. No other key changes state when you push one switch.
4. Ctrl+C clears all the lights.
5. If a key fails, check the Pi GPIO/SPI permissions and hardware before proceeding.

## Optional service on boot

Only enable unattended startup after manual testing succeeds:

```bash
sudo cp pi/keybow-diag@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "keybow-diag@$USER.service"
journalctl -u "keybow-diag@$USER.service" -f
```

The unit assumes the repository is at `/home/<user>/keybow2040` and its venv at
`.venv`. It runs as that user (with the GPIO/SPI group access configured above).
Stop with `sudo systemctl disable --now "keybow-diag@$USER.service"`.

## What follows

**D2 bridge implemented (hardware validation pending).** Run the new bridge only after the 12-key diagnostic succeeds. The Pi sends physical key events but makes **no local Codex approval decisions**. See [D2 pairing](../docs/keybow-pairing.md) and [the Windows roadmap](../docs/windows-app-plan.md).
