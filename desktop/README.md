# Keybow Codex — D0 Windows desktop simulator

A real **PySide6 desktop UI** and **scripted fake Codex backend**. Nothing is
sent to an AI provider and **no commands or file edits are executed**. The Keybow
is rendered as a clickable 3-column × 4-row virtual device. **No physical
Keybow, Pi, network connection, Codex CLI, or API key is required.**

The new code is separate from the existing macOS session switcher and audio
looper.

## Run it on Windows

Install Python 3.12 (3.10+ should work) and make sure the Python Launcher
('py') is available. Clone and switch to the development branch:

    git clone https://github.com/CDragu/keybow2040.git
    cd keybow2040
    git switch feature/windows-d0-simulator
    .\desktop\run-windows.bat

The launcher creates '.venv-desktop', installs PySide6, and starts the UI.
From PowerShell, you may also do this manually:

    py -3 -m venv .venv-desktop
    .\.venv-desktop\Scripts\python.exe -m pip install -r desktop\requirements.txt
    .\.venv-desktop\Scripts\python.exe -m desktop

For other OSes, install 'desktop/requirements.txt' in a venv and run
'python -m desktop' from the repository root. No installer/.exe yet.

## What to test

1. **Command approval:** select the default scenario, simulate turn, review the
   pretend test command/working directory/reason; use **Approve once** on screen
   or virtual key **0**. The status returns to working, then completes.
2. **Approve for session:** virtual key **1** is available for simulated
   command requests only (not for file-change approvals).
3. **Decline:** key **2** stops the run with a failed state.
4. **Cancel:** key **3** or Stop turn aborts active work or a pending approval.
5. **Acknowledge:** key **4** or Acknowledge and reset makes the next prompt
   available after any completed/failed/cancelled turn.
6. **Multiple requests:** choose the Two approvals scenario. Each requires its
   own review; old approval IDs cannot approve the next request.
7. **Failure and no approval:** try each separate scenario. Enable
   Auto-advance for a hands-free script, or disable it and click
   Next simulated event to control each step.

The 12 virtual LEDs use dim blue for ready, pulsing amber for working, red
for approval, green for completion and red for failure. All 12 keys are
shown in the original Pi Keybow physical index order (9–11 top, 0–2 bottom).

The activity log is local and ephemeral; the demo doesn't store prompts on
disk. The system tray icon is shown when the desktop supports a tray. Closing
the window quits the simulator.

## Tests

    .\.venv-desktop\Scripts\python.exe -m unittest discover -s desktop/tests -v

The core and scenario tests don't need Qt. UI tests use the Qt offscreen
platform on Linux CI and exercise the actual widgets and approval buttons.
Physical Windows interactive testing is still needed.

## Architecture

- **core.py**: pure-Python state machine, LED representation, and approval
  gate. A simulated key press must match the current run ID, request ID,
  allowed decision and **already displayed** review card.
- **simulator.py**: deterministic scripted requests and turn results.
- **app.py**: PySide6 controls, timers, virtual device/LED animation, tray.
- **tests/**: state transitions, stale requests, multiple approvals and UI.

Next, D1 will replace the simulator adapter with a Codex App Server backend
and explicit approval lifecycle. D2 will add the secure Pi link. Both should
retain the same approval gate; D0 makes **no claims of real Codex support**.

See [the desktop roadmap](../docs/windows-app-plan.md) for the full plan.
