# Keybow Codex Desktop — simulated and real Codex modes

A Python/PySide6 Windows desktop app with an animated virtual **original
12-key Keybow**. It supports two modes:

- **Simulator (default)**: fully offline, scripted scenarios and no external
  processes, file changes, or network.
- **Real Codex**: launches your **installed Codex CLI** as a local
  \`codex app-server\` process. You can send prompts to Codex, see streaming
  responses, review command/file approvals, and accept/decline using the
  onscreen controls or virtual keys. **Real Codex may execute commands and
  modify files according to its configured sandbox and approvals.**

The Raspberry Pi is **not required**. Only the virtual 12-key UI is wired up;
physical Pi networking is the later D2 milestone.

## Windows setup

Install Python 3.12+ (with the \`py\` launcher), and install the Codex CLI using
Node.js/npm. Sign in with the CLI:

\`\`\`powershell
npm install -g @openai/codex
codex login
codex --version
\`\`\`

Then clone/update this repo and launch the desktop:

\`\`\`powershell
git clone https://github.com/CDragu/keybow2040.git
cd keybow2040
git switch feature/d1-real-codex-appserver
.\desktop\run-windows.bat
\`\`\`

If you already have a checkout, \`git fetch\` and
\`git switch feature/d1-real-codex-appserver\` rather than cloning again.
After the PR is merged, just \`git switch main\` and \`git pull\`.

The launcher creates \`.venv-desktop\` and installs PySide6. If \`codex\` is
not found on PATH, Real mode will display an error; close/reopen your
terminal after installing npm global tools.

Manual setup:

\`\`\`powershell
py -3 -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -r desktop\requirements.txt
.\.venv-desktop\Scripts\python.exe -m desktop
\`\`\`

No .exe/installer is shipped yet.

## Try a real Codex conversation safely

1. Choose **Backend → Real Codex (local)**.
2. Click **Choose folder…** and choose a project you own.
3. Leave **Read-only (recommended)** selected.
4. Enter **"Explain what this repository does without changing files."**
5. Click **Send to Codex**. Wait for the actual response in **CODEX RESPONSE**.
6. Choose **Acknowledge and reset** before the next turn. Follow-up prompts
   in the same folder reuse the same Codex thread within the running app.

Codex may perform read-only tool/command activity under its sandbox.
For coding tasks you want Codex to edit, explicitly select **Workspace write**
*before* starting the turn. This grants sandboxed write access to the
selected project and may allow edits that do not individually require
approval. \`approvalPolicy\` is always \`on-request\` (never bypass).

When Codex sends a command/file approval request, the **red virtual keys**
light up. Read the command, working directory, reason and proposed file
change details; press virtual key 0 to approve once, key 1 for session **only
when the server offers it**, or key 2 to decline. You can also use the
onscreen buttons. Key 3 cancels the running turn, and key 4 acknowledges the
final outcome. Unsupported request types are rejected, never auto-approved.

A failed, cancelled, or disconnected turn must not leave a pending approval
that can be accidentally accepted after reconnecting. The client binds each
approval to the exact JSON-RPC ID, thread, turn and run generation; and
disables approval until the UI has rendered the review card.

**Limitations:** This is a first Codex integration. The real CLI has not
been run interactively on your Windows computer yet; the protocol code is
validated using a local fake App Server. Some newer approval types such as
standalone permission-grant requests and MCP form elicitation are **rejected
by this first version**, not presented as interactive grant dialogs. Real
Codex mode does not integrate with the ChatGPT desktop UI directly.
Stopping a turn can take time; the next turn stays blocked until the server
finishes interrupting it.

## Simulator scenarios (still available)

In **Simulator (offline)** mode, choose a scenario from the dropdown:
Command approval, File change approval, Two approvals, Success, or Failure.
Enable Auto-advance or click **Next simulated event**. All actions are fake.

## Tests

\`\`\`powershell
.\.venv-desktop\Scripts\python.exe -m unittest discover -s desktop/tests -v
\`\`\`

GitHub Actions runs pure-Python core, Codex protocol and subprocess stdio
tests on **Windows and Linux**; offscreen PySide6 UI tests run on Linux.
Neither CI nor unit tests need an OpenAI login. Hardware/interactive testing
is still required before calling D1 production-ready.

## Internal architecture

- \`core.py\`: shared session state and approval gate (validates request
  identity, UI visibility, and allowed decisions).
- \`simulator.py\`: deterministic offline Codex stand-in.
- \`codex_backend.py\`: stdio JSON-RPC process manager and event parser. Uses
  dedicated stdout/stderr threads; the Qt main loop drains a message queue.
- \`app.py\`: mode selector, project chooser, read-only/workspace-write
  selector, streaming reply panel, virtual keypad, approval review UI.
- \`tests/\`: protocol and UI tests; a fake subprocess App Server confirms
  newline-delimited JSON-RPC round trips.

Reference: [official Codex App Server documentation](https://learn.chatgpt.com/docs/app-server).
D2 will pair the original Raspberry Pi Keybow with this desktop app.
