"""Qt-free Codex App Server (stdio JSON-RPC) backend for the Keybow desktop.

Only the local Codex CLI is launched. The main Qt thread owns SessionController;
reader threads only put messages in a queue, drained by the GUI timer.
"""
from collections import deque
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading

from desktop.core import ApprovalRequest, Phase

APPROVAL_METHODS = {
    "item/commandExecution/requestApproval": "Command execution",
    "item/fileChange/requestApproval": "File changes",
}
TO_WIRE = {"once": "accept", "session": "acceptForSession", "decline": "decline"}
FROM_WIRE = {value: key for key, value in TO_WIRE.items()}
MAX_LINE = 2_000_000


class CodexUnavailable(RuntimeError):
    pass


class JsonRpcProcess:
    """One stdout reader, a separately drained stderr, and serialized writes."""

    def __init__(self, command=None, popen=subprocess.Popen):
        self.command = command
        self._popen = popen
        self.process = None
        self.events = queue.Queue()
        self.stderr_tail = deque(maxlen=20)
        self._write_lock = threading.Lock()
        self._next_id = 0
        self._closing = False

    def start(self):
        if self.process is not None:
            return
        command = self.command
        if command is None:
            binary = shutil.which("codex")
            if not binary:
                raise CodexUnavailable(
                    "Codex CLI not found on PATH. Install @openai/codex and run 'codex login'.")
            command = [binary, "app-server"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self.process = self._popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=flags)
        except (FileNotFoundError, OSError) as exc:
            raise CodexUnavailable(f"Unable to start Codex App Server: {exc}") from exc
        proc = self.process
        threading.Thread(target=self._read_stdout, args=(proc,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True).start()

    def _read_stdout(self, proc):
        try:
            for line in proc.stdout:
                if len(line) > MAX_LINE:
                    self.events.put({"_transport_error": "Codex sent an oversized protocol message."})
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self.events.put({"_transport_error": "Codex sent invalid JSON."})
                    break
                if not isinstance(msg, dict):
                    self.events.put({"_transport_error": "Codex sent a non-object message."})
                    break
                self.events.put(msg)
        except (OSError, UnicodeError) as exc:
            if not self._closing:
                self.events.put({"_transport_error": f"Codex stdout read failed: {exc}"})
        finally:
            if not self._closing:
                self.events.put({"_closed": True})

    def _read_stderr(self, proc):
        try:
            for line in proc.stderr:
                self.stderr_tail.append(line.rstrip()[:1000])
        except (OSError, UnicodeError):
            pass

    def _write(self, message):
        if self.process is None or self.process.poll() is not None:
            raise CodexUnavailable("Codex App Server is not running.")
        wire = json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n"
        with self._write_lock:
            try:
                self.process.stdin.write(wire)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise CodexUnavailable("Lost the Codex App Server connection.") from exc

    def request(self, method, params):
        self._next_id += 1
        ident = self._next_id
        self._write({"id": ident, "method": method, "params": params})
        return ident

    def notify(self, method):
        self._write({"method": method})

    def respond(self, request_id, result):
        # request_id MUST be the original server-supplied int or string.
        self._write({"id": request_id, "result": result})

    def reject(self, request_id, code=-32601, message="Unsupported request"):
        self._write({"id": request_id, "error": {"code": code, "message": message}})

    def close(self):
        self._closing = True
        proc = self.process
        if proc is None:
            return
        self.process = None
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError, AttributeError):
                pass


def _id(value):
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _allowed(params, method):
    """Never offer a permission not described by the server."""
    explicit = params.get("availableDecisions")
    choices = ("once", "decline")  # conservative fallback
    if explicit is not None and not isinstance(explicit, list):
        return ("decline",)
    if isinstance(explicit, list):
        wire_choices = set()
        for item in explicit:
            if isinstance(item, str):
                wire_choices.add(item)
            elif isinstance(item, dict):
                wire_choices.add(item.get("type", ""))
        choices = tuple(x for x in ("once", "session", "decline")
                        if TO_WIRE[x] in wire_choices)
    if params.get("kind") == "humanCommand":
        choices = tuple(x for x in choices if x != "session")
    # Decline is universally available for these two standard request families.
    if "decline" not in choices:
        choices = (*choices, "decline")
    return choices


class CodexBackend:
    """Translates App Server events into the existing SessionController.

    Call .poll() regularly from Qt's event loop; NEVER call it from a worker.
    """

    def __init__(self, session, command=None, process_factory=JsonRpcProcess):
        self.session = session
        self.command = command
        self.process_factory = process_factory
        self.transport = None
        self.callbacks = {}
        self.thread_id = None
        self.thread_project = None
        self.turn_id = None
        self.generation = 0
        self.active_approval = None  # (token, original rpc ID, thread, turn, item)
        self.pending_early = []
        self.items = {}
        self.sandbox = "read-only"
        self.last_error = ""
        self.inflight = False
        self.finished_turns = set()

    def start(self, prompt, sandbox="read-only"):
        if self.session.phase != Phase.IDLE or not prompt.strip():
            return False
        if self.inflight or any(tag in ('turn/start', 'turn/interrupt')
                                for tag in self.callbacks.values()):
            self.session._emit('Codex is still finishing the previous turn. Try again after it stops.')
            return False
        project = str(Path(self.session.project).resolve())
        if not Path(project).is_dir():
            self.session._emit("Choose an existing project directory before using real Codex.")
            return False
        if sandbox not in ("read-only", "workspace-write"):
            return False
        if not self.session.start(prompt, source="codex"):
            return False
        self.sandbox = sandbox
        self.turn_id = None
        self.inflight = False
        self.active_approval = None
        self.pending_early.clear()
        self.items.clear()
        self.last_error = ""
        try:
            if self.transport is None:
                self.generation += 1
                self.transport = self.process_factory(command=self.command)
                self.transport.start()
                self._send("initialize", {
                    "clientInfo": {
                        "name": "keybow_codex_desktop",
                        "title": "Keybow Codex Desktop",
                        "version": "0.2.0",
                    },
                }, "initialize")
            elif self.thread_id and self.thread_project == project:
                self._start_turn()
            else:
                self._start_thread(project)
        except (CodexUnavailable, OSError) as exc:
            self._fail(str(exc))
        return True

    def _send(self, method, params, tag):
        ident = self.transport.request(method, params)
        self.callbacks[ident] = tag
        return ident

    def _start_thread(self, project):
        self.thread_id = None
        self.thread_project = None
        self._send("thread/start", {
            "cwd": project,
            "approvalPolicy": "on-request",
            "sandbox": self.sandbox,
        }, "thread/start")

    def _start_turn(self):
        if self.session.phase != Phase.WORKING:
            return
        self.inflight = True
        self._send("turn/start", {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": self.session.prompt}],
            "cwd": str(Path(self.session.project).resolve()),
            "approvalPolicy": "on-request",
            "sandboxPolicy": {"type": "readOnly"} if self.sandbox == "read-only"
                else {"type": "workspaceWrite", "writableRoots":
                      [str(Path(self.session.project).resolve())],
                      "networkAccess": False},
        }, "turn/start")

    def poll(self, max_messages=100):
        if self.transport is None:
            return
        for _ in range(max_messages):
            if self.transport is None:
                break
            try:
                msg = self.transport.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle(msg)
            except (CodexUnavailable, OSError, ValueError, KeyError, TypeError) as exc:
                self._fail(f"Codex protocol error: {exc}")
                break

    def _handle(self, msg):
        if msg.get("_closed") or msg.get("_transport_error"):
            self._fail(msg.get("_transport_error", "Codex App Server disconnected."))
            return
        if "id" in msg and "method" in msg:
            self._server_request(msg)
        elif "id" in msg:
            self._response(msg)
        elif "method" in msg:
            self._notification(msg["method"], msg.get("params") or {})

    def _response(self, msg):
        tag = self.callbacks.pop(msg["id"], None)
        if tag is None:
            return
        if "error" in msg:
            error = msg["error"]
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            self._fail(f"{tag}: {message}")
            return
        result = msg.get("result", {})
        if tag == "initialize":
            self.transport.notify("initialized")
            if self.session.phase == Phase.WORKING:
                self._start_thread(str(Path(self.session.project).resolve()))
        elif tag == "thread/start":
            thread = result.get("thread") or {}
            self.thread_id = _id(thread.get("id"))
            if not self.thread_id:
                self._fail("thread/start returned no thread ID.")
                return
            self.thread_project = str(Path(self.session.project).resolve())
            self.session._emit(f"Connected to Codex thread {self.thread_id[:12]}…")
            self._start_turn()
        elif tag == "turn/start":
            turn = result.get("turn") or {}
            returned_id = _id(turn.get("id"))
            if not returned_id:
                self._fail("turn/start returned no turn ID.")
                return
            if self.turn_id and self.turn_id != returned_id:
                self._fail("Turn ID mismatch; approvals disabled.")
                return
            self.turn_id = returned_id
            if self.session.phase == Phase.CANCELLED and self.inflight:
                self._send("turn/interrupt", {"threadId": self.thread_id,
                                               "turnId": self.turn_id}, "turn/interrupt")
            else:
                self._drain_early()
        elif tag == "turn/interrupt":
            pass

    def _notification(self, method, params):
        thread = params.get("threadId")
        if method == "serverRequest/resolved":
            if self.active_approval and thread == self.active_approval[2]:
                if _id(params.get("requestId")) == _id(self.active_approval[1]):
                    self.active_approval = None
                    self.session.clear_approval()
            return
        if method == "error" and thread == self.thread_id:
            err = params.get("error") or {}
            message = err.get("message", "Codex reported an error.") if isinstance(err, dict) else str(err)
            if not params.get("willRetry"):
                self.session._emit(f"Codex error: {message}")
            return
        if thread != self.thread_id:
            return
        if method == "turn/started":
            turn_id = _id((params.get("turn") or {}).get("id"))
            if (turn_id and turn_id not in self.finished_turns and
                    self.turn_id is None and self.session.phase == Phase.WORKING):
                self.turn_id = turn_id
                self._drain_early()
            return
        turn_id = _id(params.get("turnId"))
        if method == "turn/completed":
            turn = params.get("turn") or {}
            finished_id = _id(turn.get("id"))
            if (not finished_id or finished_id in self.finished_turns or
                    (self.turn_id and finished_id != self.turn_id)):
                return
            self.finished_turns.add(finished_id)
            status = turn.get("status", "failed")
            self.inflight = False
            self.active_approval = None
            self.pending_early.clear()
            self.session.clear_approval()
            if status == "interrupted":
                self.session.cancel()
            elif self.session.phase == Phase.WORKING:
                error = turn.get("error") or {}
                explanation = error.get("message", "") if isinstance(error, dict) else ""
                self.session.complete(
                    status == "completed",
                    "Codex turn completed." if status == "completed"
                    else f"Codex turn failed: {explanation or status}")
            return
        if self.session.phase not in (Phase.WORKING, Phase.APPROVAL):
            return
        if (method.startswith("item/") and
                (not self.turn_id or turn_id != self.turn_id)):
            return
        if method in ("item/started", "item/completed"):
            item = params.get("item") or {}
            if item.get("id"):
                self.items[item["id"]] = item
            if method == "item/completed" and item.get("type") == "agentMessage":
                final = item.get("text") or ""
                if final and not self.session.output.strip():
                    self.session.append_output(final)
            if method == "item/started" and item.get("type") in ("commandExecution", "fileChange"):
                self.session._emit(f"Codex item: {item['type']} ({item.get('status', 'working')})")
        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                self.session.append_output(delta)

    def _server_request(self, msg):
        rpc_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if method not in APPROVAL_METHODS:
            self.transport.reject(rpc_id, message=f"Unsupported request: {method}")
            self.session._emit(f"Unsupported Codex request safely rejected: {method}")
            return
        if (not self.thread_id or params.get("threadId") != self.thread_id
                or not _id(params.get("turnId")) or not _id(params.get("itemId"))
                or self.session.phase not in (Phase.WORKING, Phase.APPROVAL)):
            self.transport.respond(rpc_id, {"decision": "decline"})
            return
        if not self.turn_id:
            self.pending_early.append(msg)
            return
        self._show_request(msg)

    def _drain_early(self):
        waiting, self.pending_early = self.pending_early, []
        for message in waiting:
            self._show_request(message)

    def _show_request(self, msg):
        rpc_id = msg["id"]
        params = msg.get("params") or {}
        if (self.session.phase != Phase.WORKING or self.active_approval
                or params.get("threadId") != self.thread_id
                or _id(params.get("turnId")) != self.turn_id):
            self.transport.respond(rpc_id, {"decision": "decline"})
            return
        item_id = _id(params.get("itemId"))
        method = msg["method"]
        item = self.items.get(item_id) or {}
        command = str(params.get("command") or item.get("command") or "")
        cwd = str(params.get("cwd") or item.get("cwd") or self.session.project)
        reason = str(params.get("reason") or "No reason provided.")
        details = ""
        network = params.get("networkApprovalContext")
        if isinstance(network, dict):
            details = (f"Network access: {network.get('protocol', '?')}://"
                       f"{network.get('host', '?')}"
                       + (f":{network['port']}" if network.get("port") else ""))
            command = ""  # not a general shell-command approval
        elif method == "item/fileChange/requestApproval":
            changes = item.get("changes") or []
            if changes:
                details = "\n".join(
                    f"{c.get('kind', 'edit')}: {c.get('path', '?')}\n"
                    + str(c.get("diff") or "")[:3000]
                    for c in changes[:20] if isinstance(c, dict))
            else:
                details = "File change details are unavailable."
            if params.get("grantRoot"):
                details += f"\nRequested grant root: {params['grantRoot']}"
        else:
            details = (str(params.get("additionalPermissions") or "")
                       if params.get("additionalPermissions") else
                       "Execute this command within the selected project.")
        allowed = _allowed(params, method)
        if not (command or (network and details) or
                (method == "item/fileChange/requestApproval" and
                 item.get("changes"))):
            allowed = ("decline",)
            details += "\nApproval is disabled because the action preview is unavailable."
        token = f"{self.generation}:{rpc_id}"
        request = ApprovalRequest(
            request_id=token,
            kind=APPROVAL_METHODS[method] if not network else "Network access",
            description=details,
            cwd=cwd, reason=reason, command=command, allowed=allowed,
        )
        if not self.session.request_approval(request):
            self.transport.respond(rpc_id, {"decision": "decline"})
            return
        self.active_approval = (token, rpc_id, self.thread_id, self.turn_id, item_id)

    def decide(self, decision, run_id, request_token):
        pending = self.active_approval
        if (not pending or pending[0] != request_token or
                self.turn_id != pending[3] or self.thread_id != pending[2] or
                not self.session.can_decide(decision, run_id, request_token)):
            return False
        # Always reply to the exact server request ID. Never retry an approval
        # response against a new connection; fail closed on write errors.
        try:
            self.transport.respond(pending[1], {"decision": TO_WIRE[decision]})
        except (CodexUnavailable, OSError) as exc:
            self._fail(str(exc))
            return False
        self.active_approval = None
        self.session.decide(decision, run_id, request_token, continue_on_decline=True)
        return True

    def cancel(self):
        if self.session.phase not in (Phase.WORKING, Phase.APPROVAL):
            return False
        if self.active_approval and self.transport:
            try:
                self.transport.respond(self.active_approval[1], {"decision": "cancel"})
            except (CodexUnavailable, OSError):
                pass
        self.active_approval = None
        self.pending_early.clear()
        self.session.cancel()
        if self.transport and self.thread_id and self.turn_id:
            try:
                self._send("turn/interrupt", {"threadId": self.thread_id,
                                               "turnId": self.turn_id}, "turn/interrupt")
            except (CodexUnavailable, OSError):
                self.close()
        return True

    def _fail(self, message):
        self.last_error = message
        self.inflight = False
        self.active_approval = None
        self.pending_early.clear()
        self.callbacks.clear()
        if self.session.phase == Phase.APPROVAL:
            self.session.clear_approval()
        if self.session.phase == Phase.WORKING:
            self.session.complete(False, message)
        else:
            self.session._emit(message)
        self.close()

    def close(self):
        if self.transport:
            self.transport.close()
            self.transport = None
        self.callbacks.clear()
        self.thread_id = None
        self.thread_project = None
        self.turn_id = None
        self.active_approval = None
