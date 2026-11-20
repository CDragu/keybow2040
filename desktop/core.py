"""Qt-independent session state and approval gate shared by real and fake Codex."""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from desktop.spotify import SPOTIFY_KEYS


class Phase(str, Enum):
    IDLE = "Idle"
    WORKING = "Working"
    APPROVAL = "Approval needed"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    kind: str
    description: str
    cwd: str
    reason: str
    command: str = ""
    allowed: tuple[str, ...] = ("once", "decline")


@dataclass(frozen=True)
class Led:
    rgb: tuple[int, int, int]
    mode: str = "solid"  # solid, pulse, blink


KEY_LABELS = {
    0: "Approve once", 1: "Approve session", 2: "Decline",
    3: "Cancel turn", 4: "Acknowledge",
    **{index: label for index, (_, label) in SPOTIFY_KEYS.items()},
}
TERMINAL = (Phase.COMPLETED, Phase.FAILED, Phase.CANCELLED)
APPROVAL_CHOICES = {"once", "session", "decline"}


class SessionController:
    """Owns UI state and rejects stale, hidden, or invalid approvals."""

    def __init__(self, project: Optional[str] = None):
        self.project = project or str(Path.cwd())
        self.phase = Phase.IDLE
        self.prompt = ""
        self.run_id = 0
        self.request: Optional[ApprovalRequest] = None
        self.presented: Optional[tuple[int, str]] = None
        self.outcome = ""
        self.output = ""
        self.history: list[str] = ["Keybow desktop ready. Choose Simulator or Real Codex."]
        self._seen: set[str] = set()
        self._observers: list[Callable[[], None]] = []

    def subscribe(self, callback: Callable[[], None]):
        self._observers.append(callback)

    def _emit(self, message: str):
        self.history.append(message)
        self._notify()

    def _notify(self):
        for callback in tuple(self._observers):
            callback()

    def append_output(self, text: str):
        if text:
            self.output += text
            self._notify()

    def set_project(self, path: str):
        if self.phase != Phase.IDLE or not path.strip():
            return False
        self.project = path
        self._emit(f"Selected project: {path}")
        return True

    def start(self, prompt: str, source="simulator"):
        if self.phase != Phase.IDLE or not prompt.strip():
            return False
        self.run_id += 1
        self._seen.clear()
        self.prompt = prompt.strip()
        self.output = ""
        self.phase = Phase.WORKING
        self.request = None
        self.presented = None
        self.outcome = ""
        self._emit(f"Turn #{self.run_id} started ({source}).")
        return True

    def request_approval(self, request: ApprovalRequest):
        if self.phase != Phase.WORKING:
            return False
        if (not request.request_id or request.request_id in self._seen or
                not request.allowed or
                not set(request.allowed).issubset(APPROVAL_CHOICES) or
                "decline" not in request.allowed):
            return False
        self._seen.add(request.request_id)
        self.phase = Phase.APPROVAL
        self.request = request
        self.presented = None
        self._emit(f"Approval requested: {request.kind} [{request.request_id}].")
        return True

    def mark_presented(self, run_id: int, request_id: str):
        """UI calls this only after displaying the review card."""
        if (self.phase != Phase.APPROVAL or not self.request or
                self.run_id != run_id or self.request.request_id != request_id):
            return False
        self.presented = (run_id, request_id)
        self._emit("Approval details visible; choices enabled.")
        return True

    def can_decide(self, decision: str, run_id: int, request_id: str):
        return (
            self.phase == Phase.APPROVAL
            and self.request is not None
            and (run_id, request_id) == self.presented
            and self.run_id == run_id
            and self.request.request_id == request_id
            and decision in self.request.allowed
        )

    def decide(self, decision: str, run_id: int, request_id: str,
               continue_on_decline=False):
        if not self.can_decide(decision, run_id, request_id):
            return False
        self.presented = None
        self.request = None
        if decision == "decline" and not continue_on_decline:
            self.phase = Phase.FAILED
            self.outcome = "Simulated request declined."
        else:
            self.phase = Phase.WORKING
            self.outcome = ""
        self._emit(f"Approval decision: {decision} for [{request_id}].")
        return True

    def clear_approval(self):
        """Invalidate a request resolved/removed by the server."""
        if self.phase != Phase.APPROVAL:
            return False
        self.phase = Phase.WORKING
        self.request = None
        self.presented = None
        self._emit("Approval no longer pending.")
        return True

    def complete(self, success=True, explanation=""):
        if self.phase != Phase.WORKING:
            return False
        self.phase = Phase.COMPLETED if success else Phase.FAILED
        self.outcome = explanation or ("Simulated turn completed." if success
                                       else "Simulated turn failed.")
        self._emit(self.outcome)
        return True

    def cancel(self):
        if self.phase not in (Phase.WORKING, Phase.APPROVAL):
            return False
        self.phase = Phase.CANCELLED
        self.request = None
        self.presented = None
        self.outcome = "Simulated turn cancelled."
        self._emit(self.outcome)
        return True

    def acknowledge(self):
        if self.phase not in TERMINAL:
            return False
        self.phase = Phase.IDLE
        self.request = None
        self.presented = None
        self.outcome = ""
        self._emit("Result acknowledged. Ready for another prompt.")
        return True

    def virtual_key(self, index: int, run_id: int | None = None,
                    request_id: str | None = None):
        """A click is a fresh key-down event, not a held or queued press."""
        if index in (0, 1, 2):
            decision = {0: "once", 1: "session", 2: "decline"}[index]
            if run_id is None or request_id is None:
                return False
            return self.decide(decision, run_id, request_id)
        if index == 3:
            return self.cancel()
        if index == 4:
            return self.acknowledge()
        return False

    def leds(self) -> tuple[Led, ...]:
        if self.phase == Phase.IDLE:
            return (Led((10, 38, 83)),) * 12
        if self.phase == Phase.WORKING:
            return (Led((220, 143, 34), "pulse"),) * 12
        if self.phase == Phase.APPROVAL:
            return tuple(
                Led((230, 55, 65), "pulse") if i in (0, 1, 2, 3)
                else Led((45, 29, 12)) for i in range(12)
            )
        if self.phase == Phase.COMPLETED:
            return (Led((37, 168, 99)),) * 12
        if self.phase == Phase.CANCELLED:
            return (Led((143, 90, 35)),) * 12
        return (Led((196, 57, 62)),) * 12
