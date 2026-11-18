# Windows Keybow Codex Controller — desktop application plan

Status: **D0 offline Windows simulator implemented** ([run instructions](../desktop/README.md)). D1 real Codex integration and D2 Pi networking are not yet implemented. Hardware diagnostic lives at
[`pi/`](../pi/README.md). The new target is the original **12-key Keybow with
Pi Zero WH**, replacing the 16-key CircuitPython assumptions in
[`keybow-codex-mvp.txt`](../keybow-codex-mvp.txt).

## Goal and v1 boundaries

A lightweight Windows desktop/tray application runs Codex App Server locally,
accepts prompts, displays live status and explicit approval requests, and
uses a paired Pi Keybow for dedicated tactile controls and RGB feedback.

**In scope:** one project directory, one Codex thread, one active turn, visible
command/file-change approvals, LED state, reconnect, audit log, safe cancellation.

**Out of scope:** controlling an already-running ChatGPT/Codex GUI, automatic
approval, multiple parallel sessions, cloud relay, audio looper/synth, arbitrary
keyboard automation. The Keybow Pi does not run Codex or hold credentials.

## Proposed technology

- **Python 3.12+ / PySide6** desktop UI (tray icon, dialog, status panel).
- **Codex CLI App Server** as a locally spawned stdio subprocess; JSONL RPC
  read/written from a worker, never blocking the UI thread.
- **Pi bridge** over a paired authenticated local-network channel. Exact
  framing and transport are decided in milestone 2, before implementation;
  do not expose an unauthenticated approval API on a public interface.
- **SQLite** for minimal local metadata and an append-only approval audit log;
  Windows DPAPI or Credential Manager for pairing secrets.
- **pytest / pytest-qt** plus a fake App Server transcript and mock Keybow.

## User interface

```text
+-----------------------------------------------------------+
| Keybow Codex                          [Pi: connected]     |
| Project: C:\work\example                 [Change...]    |
| Thread: one active                    [Settings]          |
|-----------------------------------------------------------|
| Prompt:                                                   |
| [ Refactor the parser and add unit tests ...           ]  |
|                                      [Send] [Stop turn]   |
|-----------------------------------------------------------|
| Status: Working ...                 Key LEDs: amber pulse  |
|                                                           |
| REQUESTING APPROVAL                                       |
| Command:  python -m pytest tests                          |
| Directory: C:\work\example                              |
| Reason:   Need to run the updated tests                   |
|                                                           |
| [Approve once] [Approve session*] [Decline] [Cancel]      |
| *Shown only when the server advertises this decision.     |
|                                                           |
| Activity: 14:02 prompt sent · 14:03 command requested      |
+-----------------------------------------------------------+
```

Only one approval is selected/active on the device at a time. The desktop
must show the exact command, `cwd`, reason, relevant scope/permissions, and
file-change summary before enabling a hardware approval. If details are
missing or a network-specific request lacks a meaningful command preview,
show an accurate specialised review card; don't invent a shell command.

### Proposed key layout (physical orientation, GPIO left)

```text
  [09] Reserved       [10] Reserved        [11] Reserved
  [06] Reserved       [07] Reserved        [08] Reserved
  [03] Cancel         [04] Acknowledge     [05] Reserved
  [00] Approve once   [01] Approve session [02] Decline
```

Physical mapping follows Pimoroni's bottom-left key 0 convention. When no
approval is pending, approval keys are inert (never shortcut actions into the
focused Windows app). A visible high-risk action requires explicit human
review; ordinary unrelated key presses must never trigger a decision.

| Codex state | LED plan | Desktop state |
| --- | --- | --- |
| Not paired / offline | dim amber heartbeat | Disconnected |
| Ready | dim blue | Idle |
| Turn running | pulsing amber | Working |
| Approval pending | pulsing red on keys 0–3 | Request card in foreground |
| Turn completed | solid green | Completed until acknowledged |
| Failed / declined | solid red | Details and retry |

## Architecture and trust boundary

```text
[12-key Pi Zero WH]
  GPIO/SPI -> input/LED service
         | authenticated paired link (event ID + generation)
         v
[Windows Keybow desktop app]
  Qt UI <- state store / approval gate -> Pi link worker
          |
          +-> Codex RPC client -> codex app-server (local stdio)
          +-> SQLite audit log / config
```

The only component authorised to respond to App Server approval requests is
the Windows approval gate. The Pi sends `key_down` / `key_up`, never an
`approve` RPC. The gate checks the active request identity and what the user
can actually see, then responds to the **original JSON-RPC request ID**.

## Codex App Server integration (current protocol)

1. Spawn `codex app-server` with default `stdio://` transport.
2. Send `initialize` with `clientInfo`, then `initialized`.
3. Start a new thread using `thread/start` (with selected working directory and
   an approval policy that permits user review). Avoid `approvalPolicy: never`.
4. Submit user input via `turn/start` and reflect `turn/started`, item events,
   `turn/completed`, and error notifications in the UI.
5. Render `item/commandExecution/requestApproval` and
   `item/fileChange/requestApproval`. Handle other permission/elicitation
   request types safely in the desktop UI; don't interpret them as commands.
6. Reply to approvals only with a decision allowed by the request (for example
   `accept`, `acceptForSession`, `decline`, `cancel`). Track
   `serverRequest/resolved` and clear the LED prompt immediately.
7. On app-server restart, mark outstanding requests stale; never replay an old
   decision across a new process or thread.

Reference: https://learn.chatgpt.com/docs/app-server (protocol evolves; generate
schema from the installed CLI and pin the tested version).

## Critical approval safety

- Every request gets an immutable binding: `(connection_generation, threadId,
  turnId, itemId, JSON_RPC_request_id)`; missing identity denies hardware approval.
- The user must see command/cwd/reason or specific requested permissions in a
  foreground review card before the Pi can accept a key.
- Hardware approvals are **fresh key-down edges** after the card becomes
  actionable, not a held key or queued press from before.
- Approval requires paired connection, selected active request, and an exact
  allowed decision. If app-server emits a different request, the old binding
  is invalidated even when the same key is pressed.
- On Pi disconnect, Windows lock, App Server crash/restart, turn interruption,
  resolved request, or stale timeout: disable hardware approval and clear it.
- Never send approval details or Codex credentials to the Pi; only finite
  status/LED colour commands and a short-lived request generation identifier.
- Log request metadata, decision, source, and time without secrets or full
  prompts by default. No arbitrary remote code execution endpoint.

## Milestones and acceptance tests

### D0 — Desktop shell, offline

- PySide6 tray app, project chooser, prompt editor, state cards, preferences.
- Mock Codex and Keybow backends with deterministic scripted UI scenarios.
- **Done when:** an offline tester can replay idle -> working -> approval -> done
  with no real hardware or network.

### D1 — Real Codex on Windows

- Codex CLI discovery, authentication guidance, app-server stdio lifecycle.
- `initialize`/thread/turn, streaming status, explicit command/file approval
  pop-ups; error states and request ID routing.
- **Done when:** a read-only turn and a supervised approval work with the
  desktop app alone, including a declined request.

### D2 — Pi link

- Define versioned, bounded wire schema: key event, status, LED RGB/mode,
  heartbeat, ACK, reconnect generation. Pair using one-time code/secret.
- Fail closed if auth/reconnect/clock/sequence checks fail; keep Codex local.
- **Done when:** physical 12-key diagnostics are replaced by remote status
  lighting and presses, and unplug/Wi-Fi loss never approves anything.

### D3 — Integration and safety

- Approval gate, visible-prompt-only decisions, session approval only when
  explicitly available, audit log, Windows lock behaviour.
- **Done when:** multiple sequential requests, stale presses, missed release,
  disconnect during approval, process restart and cancellation cannot lead to
  the wrong request being approved.

### D4 — Packaging and polish

- Installer, startup option, Windows firewall setup, signed/explained builds,
  configuration migration and troubleshooting guide.
- **Done when:** clean Windows install can pair the Keybow and complete a
  supervised Codex task without a developer console.

## Decisions before starting D2

- Confirm the actual 12-key Pi Zero WH and installed Pi OS version.
- Confirm Windows-to-Pi LAN connectivity. Consider USB Ethernet gadget if
  normal Wi-Fi isolation prevents reachability.
- Confirm approval semantics and test available decision fields for the
  installed Codex CLI version.
- Choose secure, supportable Pi-link transport before creating a firewall rule.
