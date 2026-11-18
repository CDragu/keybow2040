"""Keybow Codex desktop: offline simulator or real local Codex App Server.

Run from repository root: python -m desktop
"""
import queue
import secrets
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QListWidget, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

from desktop.core import KEY_LABELS, TERMINAL, Phase, SessionController
from desktop.codex_backend import CodexBackend
from desktop.simulator import SCENARIOS, SimulatedCodex
from desktop.pi_server import KeybowServer
from keybow_net.effects import LightEngine


STYLE = """
QWidget { background: #0B1220; color: #E7EFFA; font-family: 'Segoe UI', Arial; font-size: 13px; }
QFrame#card { background: #151F31; border: 1px solid #2B3B53; border-radius: 12px; }
QLabel#brand { font-size: 23px; font-weight: 750; letter-spacing: 1px; }
QLabel#section { font-size: 15px; font-weight: 650; }
QLabel#muted { color: #96A8C1; }
QLabel#status { color: #B3DAFF; font-size: 16px; font-weight: 650; }
QLabel#badge { color: #E6BD69; background: #3B3021; border-radius: 6px; padding: 6px 10px; }
QPlainTextEdit, QListWidget, QComboBox {
    background: #0C1627; border: 1px solid #344660; border-radius: 8px;
    padding: 8px; selection-background-color: #245B76;
}
QPlainTextEdit:focus, QComboBox:focus { border-color: #43BCD2; }
QPushButton {
    background: #263952; border: 1px solid #3C5472;
    border-radius: 8px; padding: 9px 13px; font-weight: 600;
}
QPushButton:hover { background: #355376; }
QPushButton:disabled { color: #657B97; background: #1A283B; border-color: #26384E; }
QPushButton#primary { background: #087D9C; color: white; border-color: #0E9CBA; }
QPushButton#primary:hover { background: #0A99B9; }
QPushButton#danger { background: #542735; color: #FFDCE1; border-color: #88414D; }
QPushButton#danger:hover { background: #71303F; }
QCheckBox { spacing: 7px; color: #B8C9DE; }
QScrollBar:vertical { width: 10px; background: #0C1627; }
QScrollBar::handle:vertical { background: #39516C; border-radius: 5px; }
"""


def card():
    panel = QFrame()
    panel.setObjectName("card")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(19, 17, 19, 17)
    layout.setSpacing(12)
    return panel, layout


def label(text, object_name=None):
    item = QLabel(text)
    if object_name:
        item.setObjectName(object_name)
    item.setWordWrap(True)
    return item


def button(text, style=None):
    item = QPushButton(text)
    if style:
        item.setObjectName(style)
    item.setCursor(Qt.PointingHandCursor)
    return item


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.session = SessionController()
        self.backend = SimulatedCodex(self.session)
        self.real_backend = CodexBackend(self.session)
        self.mode = "simulator"
        self.keybow_server = KeybowServer()
        self._physical_epoch = 0
        self._physical_held = set()
        self._pending_physical = None
        self._ambiguous_chord = False
        self._virtual_held = set()
        self._physical_arm = None
        self._physical_arm_identity = None
        self._cue = None
        self._cue_id = 0
        self._last_link_error = ""
        self.lights = LightEngine(time.monotonic())
        self.session.subscribe(self.render)
        self._shown_key = None
        self._last_history_count = 0
        self._tray = None
        self.setWindowTitle("Keybow Codex • Simulator / Real Codex")
        self.setMinimumSize(1000, 670)
        self.resize(1240, 810)
        self.setStyleSheet(STYLE)
        self._build()
        self._make_tray()
        self.render()
        self.led_timer = QTimer(self)
        self.led_timer.setInterval(80)
        self.led_timer.timeout.connect(self.paint_leds)
        self.led_timer.start()
        self.codex_timer = QTimer(self)
        self.codex_timer.setInterval(40)
        self.codex_timer.timeout.connect(self.real_backend.poll)
        self.codex_timer.start()
        self.link_timer = QTimer(self)
        self.link_timer.setInterval(40)
        self.link_timer.timeout.connect(self._poll_link)
        self.link_timer.start()

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(16)

        header = QHBoxLayout()
        brand = QVBoxLayout()
        brand.addWidget(label("KEYBOW  /  CODEX", "brand"))
        brand.addWidget(label("Windows companion • local Codex or offline simulator", "muted"))
        header.addLayout(brand, 1)
        self.mode_badge = label("SIMULATOR — NO COMMANDS RUN", "badge")
        header.addWidget(self.mode_badge)
        outer.addLayout(header)

        project_card, project_row = card()
        project_row.addWidget(label("ACTIVE PROJECT", "section"))
        picker = QHBoxLayout()
        self.project_label = label("", "muted")
        self.project_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        picker.addWidget(self.project_label, 1)
        self.pick_project = button("Choose folder…")
        self.pick_project.clicked.connect(self.choose_project)
        picker.addWidget(self.pick_project)
        project_row.addLayout(picker)
        outer.addWidget(project_card)

        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, 1)

        left = QVBoxLayout()
        left.setSpacing(16)
        body.addLayout(left, 3)
        right = QVBoxLayout()
        right.setSpacing(16)
        body.addLayout(right, 2)

        prompt_card, prompt_layout = card()
        prompt_layout.addWidget(label("TASK PROMPT", "section"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "Describe a task. Real Codex can read and potentially modify your selected project."
        )
        self.prompt_edit.setPlainText("Refactor the parser and run the unit tests")
        self.prompt_edit.setMaximumHeight(112)
        prompt_layout.addWidget(self.prompt_edit)
        mode_line = QHBoxLayout()
        mode_line.addWidget(label("Backend", "muted"))
        self.mode_picker = QComboBox()
        self.mode_picker.addItem("Simulator (offline)", userData="simulator")
        self.mode_picker.addItem("Real Codex (local)", userData="real")
        mode_line.addWidget(self.mode_picker, 1)
        self.sandbox_picker = QComboBox()
        self.sandbox_picker.addItem("Read-only (recommended)", userData="read-only")
        self.sandbox_picker.addItem("Workspace write", userData="workspace-write")
        mode_line.addWidget(self.sandbox_picker, 1)
        prompt_layout.addLayout(mode_line)
        scenario_line = QHBoxLayout()
        scenario_line.addWidget(label("Scenario", "muted"))
        self.scenario_picker = QComboBox()
        for key, name in SCENARIOS.items():
            self.scenario_picker.addItem(name, userData=key)
        scenario_line.addWidget(self.scenario_picker, 1)
        self.auto_play = QCheckBox("Auto-advance")
        self.auto_play.setChecked(True)
        scenario_line.addWidget(self.auto_play)
        prompt_layout.addLayout(scenario_line)
        action_line = QHBoxLayout()
        self.send_button = button("▶  Simulate turn", "primary")
        self.send_button.clicked.connect(self.send_prompt)
        self.cancel_button = button("Stop turn", "danger")
        self.cancel_button.clicked.connect(self.cancel_turn)
        self.step_button = button("Next simulated event")
        self.step_button.clicked.connect(self.advance)
        action_line.addWidget(self.send_button)
        action_line.addWidget(self.cancel_button)
        action_line.addWidget(self.step_button)
        prompt_layout.addLayout(action_line)
        left.addWidget(prompt_card)

        status_card, status_layout = card()
        status_layout.addWidget(label("SESSION", "section"))
        self.status_label = label("", "status")
        status_layout.addWidget(self.status_label)
        self.status_detail = label("", "muted")
        status_layout.addWidget(self.status_detail)
        self.ack_button = button("Acknowledge and reset")
        self.ack_button.clicked.connect(self.acknowledge)
        status_layout.addWidget(self.ack_button)
        left.addWidget(status_card)

        reply_card, reply_layout = card()
        reply_layout.addWidget(label("CODEX RESPONSE", "section"))
        self.reply_view = QPlainTextEdit()
        self.reply_view.setReadOnly(True)
        self.reply_view.setPlaceholderText("Codex response text appears here. In simulator mode no real response is generated.")
        self.reply_view.setMinimumHeight(105)
        self.reply_view.setMaximumHeight(190)
        reply_layout.addWidget(self.reply_view)
        left.addWidget(reply_card)

        self.approval_card, approval_layout = card()
        approval_layout.addWidget(label("⚠  APPROVAL REQUIRED", "section"))
        self.approval_kind = label("")
        approval_layout.addWidget(self.approval_kind)
        self.approval_description = label("", "muted")
        approval_layout.addWidget(self.approval_description)
        self.approval_command = label("")
        self.approval_command.setTextInteractionFlags(Qt.TextSelectableByMouse)
        approval_layout.addWidget(self.approval_command)
        self.approval_cwd = label("", "muted")
        self.approval_cwd.setTextInteractionFlags(Qt.TextSelectableByMouse)
        approval_layout.addWidget(self.approval_cwd)
        self.approval_reason = label("", "muted")
        approval_layout.addWidget(self.approval_reason)
        choices = QHBoxLayout()
        self.once_button = button("Approve once", "primary")
        self.once_button.clicked.connect(lambda: self.approve("once"))
        self.session_button = button("Approve for session")
        self.session_button.clicked.connect(lambda: self.approve("session"))
        self.decline_button = button("Decline", "danger")
        self.decline_button.clicked.connect(lambda: self.approve("decline"))
        for item in (self.once_button, self.session_button, self.decline_button):
            choices.addWidget(item)
        approval_layout.addLayout(choices)
        left.addWidget(self.approval_card)
        self.approval_card.hide()

        log_card, log_layout = card()
        log_layout.addWidget(label("ACTIVITY LOG", "section"))
        self.history = QListWidget()
        self.history.setMinimumHeight(120)
        log_layout.addWidget(self.history)
        left.addWidget(log_card, 1)

        device_card, device_layout = card()
        device_layout.addWidget(label("VIRTUAL KEYBOW", "section"))
        device_layout.addWidget(
            label("12-key Pi Zero layout • click the simulated physical keys", "muted"))
        keys = QGridLayout()
        keys.setSpacing(9)
        self.virtual_buttons = {}
        arrangement = ((9, 10, 11), (6, 7, 8), (3, 4, 5), (0, 1, 2))
        for row, indices in enumerate(arrangement):
            for col, index in enumerate(indices):
                key_name = KEY_LABELS.get(index, "Reserved")
                key = QPushButton(f"●  {index:02d}\n{key_name}")
                key.setMinimumHeight(82)
                key.setToolTip(f"Virtual Keybow key {index}: {key_name}")
                key.clicked.connect(lambda _checked=False, n=index: self.press_virtual(n))
                key.pressed.connect(lambda n=index: self._virtual_edge(n, True))
                key.released.connect(lambda n=index: self._virtual_edge(n, False))
                keys.addWidget(key, row, col)
                self.virtual_buttons[index] = key
        device_layout.addLayout(keys)
        self.device_status = label("Virtual device: ready • no Pi connected", "muted")
        device_layout.addWidget(self.device_status)
        self.link_button = button("Enable physical Pi link")
        self.link_button.setToolTip("Requires ~/.keybow2040/windows.json created by the pairing utility")
        self.link_button.clicked.connect(self.toggle_pi_link)
        device_layout.addWidget(self.link_button)
        right.addWidget(device_card)

        guide_card, guide_layout = card()
        guide_layout.addWidget(label("HOW TO TEST", "section"))
        self.guide_text = label("", "muted")
        guide_layout.addWidget(self.guide_text)
        right.addWidget(guide_card)
        right.addStretch(1)
        self.mode_picker.currentIndexChanged.connect(self.on_mode_changed)
        self.prompt_edit.textChanged.connect(self.render)

    def on_mode_changed(self):
        if self.session.phase != Phase.IDLE:
            return
        next_mode = self.mode_picker.currentData()
        if self.mode != next_mode and next_mode != "real":
            self.real_backend.close()
        self.mode = next_mode
        self.render()

    def _make_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QColor("#11A5C2"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(2, 2, 28, 28, 7, 7)
        painter.setBrush(QColor("#07111D"))
        for x in (8, 16, 24):
            for y in (8, 16, 24):
                painter.drawEllipse(x - 2, y - 2, 4, 4)
        painter.end()
        menu = QMenu(self)
        show_action = QAction("Show Keybow desktop", self)
        show_action.triggered.connect(self.showNormal)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        self._tray = QSystemTrayIcon(QIcon(pixmap), self)
        self._tray.setToolTip("Keybow Codex Desktop")
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(lambda _: self.showNormal())
        self._tray.show()

    def choose_project(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select project folder", self.session.project)
        if directory:
            self.session.set_project(directory)

    def send_prompt(self):
        if self.mode == "real":
            self.real_backend.start(self.prompt_edit.toPlainText(),
                                    self.sandbox_picker.currentData())
        elif self.backend.start(self.prompt_edit.toPlainText(),
                                self.scenario_picker.currentData()):
            self._schedule_auto()

    def _schedule_auto(self):
        if (self.mode != "simulator" or not self.auto_play.isChecked()
                or self.session.phase != Phase.WORKING):
            return
        run_id = self.session.run_id
        QTimer.singleShot(850, lambda: self._auto_advance(run_id))

    def _auto_advance(self, run_id):
        if self.session.run_id == run_id and self.session.phase == Phase.WORKING:
            self.advance()

    def advance(self):
        if self.mode == "simulator":
            self.backend.advance()

    def _current_token(self):
        return self._shown_key

    def approve(self, choice):
        token = self._current_token()
        if not token:
            return
        changed = (self.real_backend.decide(choice, *token) if self.mode == "real"
                   else self.session.decide(choice, *token))
        if changed:
            self._cue_event("declined" if choice == "decline" else "accepted")
            self._schedule_auto()

    def press_virtual(self, index):
        token = self._current_token()
        if index in (0, 1, 2) and token:
            if self.mode == "real":
                choice = {0: "once", 1: "session", 2: "decline"}[index]
                changed = self.real_backend.decide(choice, *token)
            else:
                changed = self.session.virtual_key(index, *token)
        elif index == 3 and self.mode == "real":
            changed = self.real_backend.cancel()
        else:
            changed = self.session.virtual_key(index)
        if changed:
            if index in (0, 1, 2):
                self._cue_event("declined" if index == 2 else "accepted")
            elif index == 3:
                self._cue_event("cancelled")
            self._schedule_auto()

    def cancel_turn(self):
        self._cue_event("cancelled")
        if self.mode == "real":
            self.real_backend.cancel()
        else:
            self.session.cancel()

    def acknowledge(self):
        self.session.acknowledge()

    def render(self):
        phase = self.session.phase
        self.project_label.setText(self.session.project)
        self.status_label.setText(phase.value)
        detail = self.session.outcome or (
            (f"Codex turn #{self.session.run_id} running locally." if self.mode == "real"
             else f"Simulated turn #{self.session.run_id} — no real Codex process.")
            if phase != Phase.IDLE else "Select a project and submit a prompt.")
        self.mode_badge.setText(
            "REAL CODEX — LOCAL PROJECT ACCESS" if self.mode == "real"
            else "SIMULATOR — NO COMMANDS RUN")
        self.send_button.setText(
            "▶  Send to Codex" if self.mode == "real" else "▶  Simulate turn")
        self.mode_picker.setEnabled(phase == Phase.IDLE)
        self.scenario_picker.setEnabled(self.mode == "simulator" and phase == Phase.IDLE)
        self.auto_play.setEnabled(self.mode == "simulator")
        self.sandbox_picker.setEnabled(self.mode == "real" and phase == Phase.IDLE)
        self.step_button.setVisible(self.mode == "simulator")
        self.guide_text.setText(
            "Real mode runs the installed Codex CLI against the selected folder. "
            "Read-only is the safest starting point. Workspace-write can edit files "
            "within your project under Codex sandbox and approval rules. "
            "Review every approval before clicking a virtual key. "
            "Stop sends a turn interruption."
            if self.mode == "real" else
            "1. Choose a scenario and simulate a turn.\n\n"
            "2. When approval appears, inspect command, folder and reason.\n\n"
            "3. Use the virtual red keys or the approval buttons.\n\n"
            "4. Test decline, cancel, failure and two approvals.\n\n"
            "5. Acknowledge the result to start again.")
        if self.reply_view.toPlainText() != self.session.output:
            self.reply_view.setPlainText(self.session.output)
            sb = self.reply_view.verticalScrollBar()
            sb.setValue(sb.maximum())
        self.status_detail.setText(detail)
        self.pick_project.setEnabled(phase == Phase.IDLE)
        self.prompt_edit.setEnabled(phase == Phase.IDLE)
        self.send_button.setEnabled(phase == Phase.IDLE and
                                    bool(self.prompt_edit.toPlainText().strip()))
        self.cancel_button.setEnabled(phase in (Phase.WORKING, Phase.APPROVAL))
        self.step_button.setEnabled(self.mode == "simulator" and phase == Phase.WORKING)
        self.ack_button.setEnabled(phase in TERMINAL)

        request = self.session.request if phase == Phase.APPROVAL else None
        self.approval_card.setVisible(request is not None)
        if request:
            key = (self.session.run_id, request.request_id)
            self.approval_kind.setText(f"{request.kind}    ·    ID: {request.request_id}")
            self.approval_description.setText(request.description)
            self.approval_command.setText(
                f"Command: {request.command}" if request.command
                else "No shell command — review the proposed file changes.")
            self.approval_cwd.setText(f"Working directory: {request.cwd}")
            self.approval_reason.setText(f"Reason: {request.reason}")
            if self._shown_key != key:
                self._shown_key = key
                # First allow the review card to enter the event loop. This
                # prevents an event queued before it was shown from approving.
                QTimer.singleShot(
                    0, lambda run_id=key[0], request_id=key[1]:
                    self.session.mark_presented(run_id, request_id))
            actionable = self.session.presented == key
            self.once_button.setEnabled(actionable and "once" in request.allowed)
            self.session_button.setEnabled(actionable and "session" in request.allowed)
            self.decline_button.setEnabled(actionable and "decline" in request.allowed)
        else:
            self._shown_key = None

        for i, key in self.virtual_buttons.items():
            if i in (0, 1, 2):
                names = {0: "once", 1: "session", 2: "decline"}
                enabled = bool(
                    request and self.session.presented == self._shown_key
                    and names[i] in request.allowed
                )
            elif i == 3:
                enabled = phase in (Phase.WORKING, Phase.APPROVAL)
            elif i == 4:
                enabled = phase in TERMINAL
            else:
                enabled = False
            key.setEnabled(enabled)

        if self._last_history_count > len(self.session.history):
            self.history.clear()
            self._last_history_count = 0
        for message in self.session.history[self._last_history_count:]:
            self.history.addItem(message)
        self._last_history_count = len(self.session.history)
        self.history.scrollToBottom()
        self._sync_link()
        self.paint_leds()


    # The network worker never touches Qt or Codex. All authenticated key
    # events are dispatched here on the main/UI thread.
    def _phase_name(self):
        return {
            Phase.IDLE: "idle", Phase.WORKING: "working",
            Phase.APPROVAL: "approval", Phase.COMPLETED: "completed",
            Phase.FAILED: "failed", Phase.CANCELLED: "cancelled",
        }[self.session.phase]

    def _cue_event(self, cue):
        self._cue = cue
        self._cue_id += 1
        self.lights.trigger(cue, time.monotonic())
        self._sync_link()

    def _update_visual_holds(self):
        now = time.monotonic()
        both = self._physical_held | self._virtual_held
        for index in range(12):
            self.lights.key(index, index in both, now)

    def _virtual_edge(self, index, pressed):
        if pressed:
            self._virtual_held.add(index)
        else:
            self._virtual_held.discard(index)
        self._update_visual_holds()

    def toggle_pi_link(self):
        if self.keybow_server.running:
            self.keybow_server.close()
            self._physical_epoch = 0
            self._physical_held.clear()
            self._pending_physical = None
            self._ambiguous_chord = False
            self._physical_arm = None
            self._physical_arm_identity = None
            self._update_visual_holds()
            self.render()
            return
        try:
            self.keybow_server.start()
        except (OSError, ValueError, FileNotFoundError) as exc:
            QMessageBox.warning(
                self, "Keybow Pi pairing",
                f"Cannot start Pi link: {exc}\n\n"
                "Create ~/.keybow2040/windows.json with the pairing utility. "
                "See docs/keybow-pairing.md.")
            return
        self._last_link_error = ""
        self.render()

    def _poll_link(self):
        for _ in range(256):
            try:
                event = self.keybow_server.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "connected" and self.keybow_server.running:
                self._physical_epoch = event[1]
                self._physical_held.clear()
                self._pending_physical = None
                self._ambiguous_chord = False
                self._physical_arm = None
                self._physical_arm_identity = None
                self._update_visual_holds()
                self._cue_event("connected")
            elif kind == "disconnected" and event[1] == self._physical_epoch:
                self._physical_epoch = 0
                self._physical_held.clear()
                self._pending_physical = None
                self._ambiguous_chord = False
                self._physical_arm = None
                self._physical_arm_identity = None
                self._update_visual_holds()
            elif kind == "error":
                self._last_link_error = event[1]
            elif kind == "snapshot" and event[1] == self._physical_epoch:
                self._physical_held = set(event[2])
                self._update_visual_holds()
            elif kind == "key" and event[1] == self._physical_epoch:
                _, _, index, pressed, arm = event
                if (index in self._physical_held) == pressed:
                    continue  # duplicate/repeated edge, never another decision
                if pressed:
                    self._physical_held.add(index)
                else:
                    self._physical_held.discard(index)
                self._update_visual_holds()
                if not pressed:
                    if (index in (0, 1, 2) and self._ambiguous_chord
                            and not (self._physical_held & {0, 1, 2})):
                        self._ambiguous_chord = False
                    continue
                if index in (0, 1, 2):
                    identity = self._physical_arm_identity
                    valid = (
                        self.keybow_server.connected and identity is not None
                        and arm is not None and arm == self._physical_arm
                        and identity[:2] == self._shown_key
                        and identity[2] == self._physical_epoch
                        and self.session.presented == self._shown_key
                        and self.isVisible() and not self.isMinimized()
                        and self.isActiveWindow()
                    )
                    if (self._pending_physical is not None or
                            len(self._physical_held & {0, 1, 2}) > 1):
                        # Ambiguous chord: never let arrival order select the
                        # approval. Release all approval keys and try again.
                        self._pending_physical = None
                        self._ambiguous_chord = True
                    elif valid and not self._ambiguous_chord:
                        self._pending_physical = (
                            index, arm, identity, time.monotonic())
                elif index == 3:
                    self._pending_physical = None  # cancel takes precedence
                    self.press_virtual(index)
                elif index == 4:
                    self.press_virtual(index)
        self._commit_physical_pending()
        self._sync_link()
        if hasattr(self, "device_status"):
            if not self.keybow_server.running:
                text = "Physical Pi: disabled • virtual keys work offline"
            elif self._physical_epoch:
                held = ", ".join(str(i) for i in sorted(self._physical_held)) or "none"
                text = f"Physical Pi: connected • held: {held}"
            else:
                text = "Physical Pi: listening for paired Pi"
                if self._last_link_error:
                    text += f" • {self._last_link_error[:90]}"
            self.device_status.setText(text)
            self.link_button.setText(
                "Disable physical Pi link" if self.keybow_server.running
                else "Enable physical Pi link")


    def _commit_physical_pending(self):
        candidate = self._pending_physical
        if candidate is None or time.monotonic() - candidate[3] < 0.09:
            return
        self._pending_physical = None
        index, arm, identity, _started = candidate
        if (not self._ambiguous_chord and
                self.keybow_server.connected and
                identity == self._physical_arm_identity and
                arm == self._physical_arm and
                self.session.presented == self._shown_key and
                self.session.phase == Phase.APPROVAL and
                len(self._physical_held & {0, 1, 2}) <= 1 and
                self.isVisible() and not self.isMinimized() and
                self.isActiveWindow()):
            self.press_virtual(index)

    def _sync_link(self):
        if not self.keybow_server.running:
            return
        # Hardware approval is only armed after a foreground review card is
        # both visible and marked presented. Blur/minimize/disconnect disarms.
        identity = None
        if (self._physical_epoch and self.keybow_server.connected
                and self.session.phase == Phase.APPROVAL
                and self._shown_key is not None
                and self.session.presented == self._shown_key
                and self.isVisible() and not self.isMinimized()
                and self.isActiveWindow()):
            identity = (*self._shown_key, self._physical_epoch)
        if identity != self._physical_arm_identity:
            self._pending_physical = None
            self._ambiguous_chord = False
            self._physical_arm_identity = identity
            self._physical_arm = secrets.token_hex(16) if identity else None
        self.keybow_server.publish(
            self._phase_name(), arm=self._physical_arm,
            cue=self._cue, cue_id=self._cue_id)

    def paint_leds(self):
        now = time.monotonic()
        self.lights.set_phase(self._phase_name(), now)
        for index, color in enumerate(self.lights.frame(now)):
            rgb = "#{:02x}{:02x}{:02x}".format(*color)
            self.virtual_buttons[index].setStyleSheet(
                "QPushButton {"
                f"background-color: {rgb}; border: 1px solid #536781;"
                "border-radius: 11px; color: #FFFFFF; font-weight: 650;"
                "padding: 7px; text-align: center;"
                "}"
                "QPushButton:hover {border: 2px solid #D5F6FF;}"
                "QPushButton:disabled {color: #D4DFEF;}"
            )

    def closeEvent(self, event):
        self.keybow_server.close()
        self.real_backend.close()
        if self._tray:
            self._tray.hide()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Keybow Codex Desktop")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
