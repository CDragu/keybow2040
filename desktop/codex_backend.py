"""Qt-free Codex App Server (stdio JSON-RPC) backend for the Keybow desktop.

Only the local Codex CLI is launched. The main Qt thread owns SessionController;
reader threads only put messages in a queue, drained by the GUI timer.
"""
from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import threading
import time

from desktop.core import ApprovalRequest, Phase

APPROVAL_METHODS = {
    "item/commandExecution/requestApproval": "Command execution",
    "item/fileChange/requestApproval": "File changes",
}
TO_WIRE = {"once": "accept", "session": "acceptForSession", "decline": "decline"}
FROM_WIRE = {value: key for key, value in TO_WIRE.items()}
MAX_LINE = 2_000_000


APPROVAL_TIMEOUT = 120
MAX_PENDING_REQUESTS = 16


@dataclass(frozen=True)
class ApprovalBinding:
    token: str
    generation: int
    thread_id: str
    turn_id: str
    item_id: str
    rpc_id: str | int
    kind: str
    expires_at: float


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
        except (OSError, UnicodeError, ValueError) as exc:
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


def _text(value):
    return value if isinstance(value, str) and value.strip() else ""


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

    def __init__(self, session, command=None, process_factory=JsonRpcProcess,
                 audit=None, clock=time.monotonic):
        self.session = session
        self.audit = audit
        self.clock = clock
        self.session_locked = False
        self.command = command
        self.process_factory = process_factory
        self.transport = None
        self.callbacks = {}
        self.thread_id = None
        self.thread_project = None
        self.turn_id = None
        self.generation = 0
        self.active_approval = None
        self.seen_request_ids = set()
        self.pending_early = []
        self.items = {}
        self.sandbox = "read-only"
        self.last_error = ""
        self.inflight = False
        self.finished_turns = set()

    def start(self, prompt, sandbox="read-only"):
        if self.session.phase != Phase.IDLE or not prompt.strip():
            return False
        if self.inflight or self.callbacks:
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
        self.seen_request_ids.clear()
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
        pending = self.active_approval
        if pending and self.clock() >= pending.expires_at:
            self._invalidate("expired", reply="decline")

    def _handle(self, msg):
        if msg.get("_closed") or msg.get("_transport_error"):
            self._fail(msg.get("_transport_error", "Codex App Server disconnected."))
            return
        if "id" in msg and "method" in msg:
            self._server_request(msg)
        elif "id" in msg:
            self._response(msg)
        elif "method" in msg:
            params = msg.get("params") or {}
            if isinstance(params, dict):
                self._notification(msg["method"], params)

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
            rpc_id = params.get("requestId")
            pending = self.active_approval
            if (pending and thread == pending.thread_id
                    and type(rpc_id) is type(pending.rpc_id)
                    and rpc_id == pending.rpc_id):
                self._invalidate("resolved")
            if thread == self.thread_id and _id(rpc_id):
                self.pending_early = [(msg, deadline) for msg, deadline in self.pending_early
                                      if (type(msg["id"]), msg["id"]) != (type(rpc_id), rpc_id)]
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
            if (not self.inflight or not finished_id or finished_id in self.finished_turns or
                    (self.turn_id and finished_id != self.turn_id)):
                return
            self.finished_turns.add(finished_id)
            status = turn.get("status", "failed")
            self.inflight = False
            self._invalidate("turn ended")
            self.pending_early.clear()
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
        params = msg.get("params")
        if not _id(rpc_id):
            return  # malformed ID cannot be used for a response
        request_key = (type(rpc_id), rpc_id)
        if request_key in self.seen_request_ids:
            # An ambiguous duplicate must not receive the active decision.
            if (self.active_approval and request_key == (
                    type(self.active_approval.rpc_id), self.active_approval.rpc_id)
                    or any((type(queued["id"]), queued["id"]) == request_key
                           for queued, _ in self.pending_early)):
                self._fail("Codex sent a duplicate pending request ID.")
            return
        self.seen_request_ids.add(request_key)
        if not isinstance(params, dict):
            self.transport.reject(rpc_id, message="Malformed request parameters")
            return
        if method not in APPROVAL_METHODS:
            self.transport.reject(rpc_id, message="Unsupported Codex request")
            self.session._emit("Unsupported Codex request safely rejected.")
            return
        if (self.session_locked or not self.inflight or not self.thread_id
                or params.get("threadId") != self.thread_id
                or not _id(params.get("turnId")) or not _id(params.get("itemId"))
                or self.session.phase not in (Phase.WORKING, Phase.APPROVAL)):
            self.transport.respond(rpc_id, {"decision": "decline"})
            return
        deadline = self.clock() + APPROVAL_TIMEOUT
        if not self.turn_id and len(self.pending_early) < MAX_PENDING_REQUESTS:
            self.pending_early.append((msg, deadline))
            return
        self._show_request(msg, deadline)

    def _drain_early(self):
        waiting, self.pending_early = self.pending_early, []
        for msg, deadline in waiting:
            self._show_request(msg, deadline)

    def _show_request(self, msg, deadline):
        rpc_id = msg["id"]
        params = msg["params"]
        if (self.session_locked or self.clock() >= deadline
                or self.session.phase != Phase.WORKING or self.active_approval
                or params.get("threadId") != self.thread_id or not self.turn_id
                or _id(params.get("turnId")) != self.turn_id):
            self.transport.respond(rpc_id, {"decision": "decline"})
            return
        item_id = _id(params["itemId"])
        method = msg["method"]
        item = self.items.get(item_id, {})
        network = params.get("networkApprovalContext")
        command = ""
        cwd = _text(params.get("cwd"))
        if not cwd and item.get("type") == "commandExecution":
            cwd = _text(item.get("cwd"))
        valid = False
        kind = APPROVAL_METHODS[method]
        if network is not None:
            kind = "Network access"
            host = _text(network.get("host")) if isinstance(network, dict) else ""
            protocol = _text(network.get("protocol")) if isinstance(network, dict) else ""
            port = network.get("port") if isinstance(network, dict) else None
            valid = (method == "item/commandExecution/requestApproval" and host
                     and protocol and (port is None or type(port) is int and 0 < port <= 65535))
            details = (f"Network access: {protocol} to {host}"
                       + (f" on port {port}" if port is not None else "")
                       if valid else "Network destination is unavailable.")
        elif method == "item/fileChange/requestApproval":
            changes = item.get("changes")
            valid = (item.get("type") == "fileChange" and isinstance(changes, list)
                     and bool(changes) and all(isinstance(change, dict)
                     and _text(change.get("path")) and isinstance(change.get("diff"), str)
                     for change in changes))
            details = ("\n\n".join(
                f"{change.get('kind', 'edit')}: {change['path']}\n{change['diff']}"
                for change in changes) if valid else "File change details are unavailable.")
            if params.get("grantRoot"):
                details += f"\nRequested grant root: {params['grantRoot']}"
        else:
            command = _text(params.get("command"))
            if not command and item.get("type") == "commandExecution":
                command = _text(item.get("command"))
            valid = bool(command and cwd)
            permissions = params.get("additionalPermissions")
            details = (json.dumps(permissions, ensure_ascii=False) if permissions
                       else "Command execution request.")
            if params.get("commandActions"):
                details += "\n" + json.dumps(params["commandActions"], ensure_ascii=False)
        allowed = _allowed(params, method)
        if not valid:
            allowed = ("decline",)
            details += "\nApproval is disabled because the action preview is incomplete."
        # repr preserves the distinction between JSON integer and string IDs.
        token = f"{self.generation}:{rpc_id!r}"
        binding = ApprovalBinding(token, self.generation, self.thread_id, self.turn_id,
                                  item_id, rpc_id, kind, deadline)
        if not self._audit(binding, "requested", "server"):
            self.transport.respond(rpc_id, {"decision": "decline"})
            return
        self.active_approval = binding
        request = ApprovalRequest(token, kind, details, cwd or "Unavailable",
                                  _text(params.get("reason")) or "No reason provided.",
                                  command, allowed)
        if not self.session.request_approval(request):
            self._invalidate("not displayed", reply="decline")

    def decide(self, decision, run_id, request_token, source="desktop", guard=None):
        # Consume queued lifecycle events before accepting any UI/Pi input.
        self.poll()
        pending = self.active_approval
        if (not pending or self.session_locked or pending.token != request_token
                or pending.generation != self.generation
                or pending.turn_id != self.turn_id or pending.thread_id != self.thread_id
                or not self.transport.events.empty()
                or self.clock() >= pending.expires_at
                or not self.session.can_decide(decision, run_id, request_token)):
            return False
        if not self._audit(pending, "decision attempted", source, decision):
            self._invalidate("audit unavailable", reply="decline")
            return False
        if (not self.transport.events.empty() or self.clock() >= pending.expires_at
                or guard is not None and not guard()):
            return False
        try:
            self.transport.respond(pending.rpc_id, {"decision": TO_WIRE[decision]})
        except (CodexUnavailable, OSError) as exc:
            self._fail(str(exc))
            return False
        self.active_approval = None
        self._audit(pending, "decision sent", source, decision)
        self.session.decide(decision, run_id, request_token, continue_on_decline=True)
        return True

    def _audit(self, pending, event, source, decision=None):
        if self.audit is None:
            return True
        try:
            self.audit.record(pending, event, source, decision)
        except (OSError, sqlite3.Error):
            self.session._emit("Approval audit could not be written.")
            return False
        return True

    def _invalidate(self, reason, reply=None, source="system"):
        pending, self.active_approval = self.active_approval, None
        if not pending:
            return
        self._audit(pending, reason, source)
        self.session.clear_approval()
        if reply and self.transport:
            try:
                self.transport.respond(pending.rpc_id, {"decision": reply})
            except (CodexUnavailable, OSError) as exc:
                self._fail(str(exc))
                return
            self._audit(pending, "decision sent", source, reply)

    def set_session_locked(self, locked):
        self.session_locked = locked
        if locked:
            waiting, self.pending_early = self.pending_early, []
            self._invalidate("Windows session locked", reply="decline")
            for msg, _ in waiting:
                if self.transport:
                    try:
                        self.transport.respond(msg["id"], {"decision": "decline"})
                    except (CodexUnavailable, OSError) as exc:
                        self._fail(str(exc))
                        break

    def cancel(self, source="desktop"):
        self.poll()
        if self.session.phase not in (Phase.WORKING, Phase.APPROVAL):
            return False
        waiting, self.pending_early = self.pending_early, []
        self._invalidate("turn cancelled", reply="cancel", source=source)
        self.session.cancel()
        if self.transport:
            try:
                for msg, _ in waiting:
                    self.transport.respond(msg["id"], {"decision": "cancel"})
                if self.thread_id and self.turn_id and self.inflight:
                    self._send("turn/interrupt", {"threadId": self.thread_id,
                                                   "turnId": self.turn_id}, "turn/interrupt")
            except (CodexUnavailable, OSError):
                self.close()
        return True

    def _fail(self, message):
        self.last_error = message
        self._invalidate("server disconnected")
        if not self.session.complete(False, message):
            self.session._emit(message)
        self.close()

    def close(self):
        self._invalidate("server closed")
        if self.transport:
            self.transport.close()
            self.transport = None
        self.callbacks.clear()
        self.thread_id = None
        self.thread_project = None
        self.turn_id = None
        self.inflight = False
        self.pending_early.clear()
        self.finished_turns.clear()
