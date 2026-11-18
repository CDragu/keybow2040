"""Scripted fake Codex backend. It never starts Codex or executes commands."""
from desktop.core import ApprovalRequest, Phase, SessionController

SCENARIOS = {
    "command": "Command approval",
    "files": "File change approval",
    "two": "Two approvals",
    "success": "Success (no approval)",
    "failure": "Failure (no approval)",
}


class SimulatedCodex:
    def __init__(self, session: SessionController):
        self.session = session
        self.scenario = "command"
        self.stage = 0

    def start(self, prompt: str, scenario: str = "command") -> bool:
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario}")
        if not self.session.start(prompt):
            return False
        self.scenario = scenario
        self.stage = 0
        return True

    def advance(self) -> bool:
        """Take one deterministic step. Pauses until any approval is resolved."""
        if self.session.phase != Phase.WORKING:
            return False
        if self.scenario == "success":
            return self.session.complete(True, "Simulation complete: test task succeeded.")
        if self.scenario == "failure":
            return self.session.complete(False, "Simulation complete: the task failed.")

        if self.stage == 0:
            if self.scenario == "files":
                result = self._file_request()
            else:
                result = self._command_request()
            if result:
                self.stage += 1
            return result

        if self.scenario == "two" and self.stage == 1:
            result = self._file_request()
            if result:
                self.stage += 1
            return result
        return self.session.complete(
            True, "Simulation complete: all approved steps finished.")

    def _command_request(self):
        return self.session.request_approval(ApprovalRequest(
            request_id=f"demo-{self.session.run_id}-command",
            kind="Command execution",
            description="Run the project's unit tests (preview only).",
            command="python -m unittest discover -s tests -v",
            cwd=self.session.project,
            reason="The simulated assistant wants to check whether changes pass.",
            allowed=("once", "session", "decline"),
        ))

    def _file_request(self):
        return self.session.request_approval(ApprovalRequest(
            request_id=f"demo-{self.session.run_id}-file",
            kind="File changes",
            description="Modify src/parser.py and tests/test_parser.py (preview only).",
            cwd=self.session.project,
            reason="The simulated assistant proposes a parser refactor.",
            allowed=("once", "decline"),
        ))
