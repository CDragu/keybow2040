"""Keybow Codex desktop: offline simulator or real local Codex App Server.

Run from repository root: python -m desktop
"""
import queue
import secrets
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QListWidget, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

from desktop.audit import ApprovalAudit
from desktop.windows_session import (
    SESSION_LOCKED, SESSION_UNLOCKED, WM_WTSSESSION_CHANGE, WindowsSession,
)
from desktop.core import KEY_LABELS, TERMINAL, Phase, SessionController
from desktop.codex_backend import CodexBackend
from desktop.simulator import SCENARIOS, SimulatedCodex
from desktop.pi_server import KeybowServer
from keybow_net.effects import LightEngine


APPROVAL_KEYS = {0: "once", 1: "session", 2: "decline"}

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
    item.setTextFormat(Qt.PlainText)
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
    def __init__(self, *, audit=None):
        super().__init__()
        self._window_session = None
        self._session_locked = False
        self._approval_paint_pending = None
        self._pressed_approvals = {}
        self.session = SessionController()
        self.backend = SimulatedCodex(self.session)
        self.real_backend = CodexBackend(self.session, audit=audit)
        self.mode = "simulator"
        self.keybow_server = KeybowServer()
        self._physical_epoch = 0
        self._physical_snapshot = False
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
        self._window_session = WindowsSession(self.winId())
        if sys.platform == "win32" and not self._window_session.registered:
            self.session._emit("Windows session monitoring unavailable. Approval controls are disabled.")
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
        self.cancel_button.clicked.connect(lambda: self.cancel_turn())
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
        self.approval_card.installEventFilter(self)
        approval_layout.addWidget(label("⚠  APPROVAL REQUIRED", "section"))
        self.approval_kind = label("")
        approval_layout.addWidget(self.approval_kind)
        self.approval_description = QPlainTextEdit()
        self.approval_description.setReadOnly(True)
        self.approval_description.setFixedHeight(92)
        approval_layout.addWidget(self.approval_description)
        self.approval_command = label("")
        self.approval_command.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.approval_command.setWordWrap(True)
        approval_layout.addWidget(self.approval_command)
        self.approval_cwd = label("", "muted")
        self.approval_cwd.setTextInteractionFlags(Qt.TextSelectableByMouse)
        approval_layout.addWidget(self.approval_cwd)
        self.approval_reason = label("", "muted")
        approval_layout.addWidget(self.approval_reason)
        choices = QHBoxLayout()
        self.once_button = button("Approve once", "primary")
        self._bind_decision(self.once_button, "once")
        self.session_button = button("Approve for session")
        self._bind_decision(self.session_button, "session")
        self.decline_button = button("Decline", "danger")
        self._bind_decision(self.decline_button, "decline")
        for item in (self.once_button, self.session_button, self.decline_button):
            choices.addWidget(item)
        approval_layout.addLayout(choices)
        left.insertWidget(0, self.approval_card)
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
                if index in APPROVAL_KEYS:
                    self._bind_decision(key, APPROVAL_KEYS[index], "virtual key")
                else:
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

    def _bind_decision(self, widget, choice, source="desktop"):
        widget.pressed.connect(lambda: self._pressed_approvals.__setitem__(
            widget, self._current_token()))
        widget.clicked.connect(lambda: self.approve(
            choice, self._pressed_approvals.pop(widget, None), source))

    def _review_visible(self):
        return (not self._session_locked and self.isVisible() and not self.isMinimized()
                and self.isActiveWindow() and self.approval_card.isVisible()
                and not self.approval_card.visibleRegion().isEmpty()
                and self._window_session is not None and self._window_session.is_foreground())

    def _mark_painted(self, key):
        if self._approval_paint_pending == key:
            self._approval_paint_pending = None
        if (key == self._shown_key and self.session.presented != key
                and self._review_visible()):
            self.session.mark_presented(*key)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "approval_card", None) and event.type() == QEvent.Paint:
            key = self._shown_key
            if key and self.session.presented != key and self._approval_paint_pending != key:
                self._approval_paint_pending = key
                QTimer.singleShot(0, self, lambda: self._mark_painted(key))
        return super().eventFilter(watched, event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "approval_card") and event.type() in (
                QEvent.ActivationChange, QEvent.WindowStateChange):
            if not self._review_visible():
                self._forget_presentation()
            self.approval_card.update()

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, "approval_card"):
            self._forget_presentation()

    def _forget_presentation(self):
        self._pressed_approvals.clear()
        self.session.presented = None
        self._pending_physical = None
        self._sync_link()

    def nativeEvent(self, event_type, message):
        if sys.platform == "win32":
            from ctypes.wintypes import MSG
            native = MSG.from_address(int(message))
            if native.message == WM_WTSSESSION_CHANGE and hasattr(self, "approval_card"):
                if native.wParam in SESSION_LOCKED:
                    self._set_session_locked(True)
                elif native.wParam in SESSION_UNLOCKED:
                    self._set_session_locked(False)
        return super().nativeEvent(event_type, message)

    def _set_session_locked(self, locked):
        self._session_locked = locked
        if locked:
            self._forget_presentation()
        self.real_backend.set_session_locked(locked)
        self.approval_card.update()
        self.render()

    def approve(self, choice, token, source="desktop", input_guard=None):
        def still_visible():
            return (token == self._shown_key and self._review_visible()
                    and (input_guard is None or input_guard()))

        if not token or not still_visible():
            return
        changed = (self.real_backend.decide(choice, *token, source=source, guard=still_visible)
                   if self.mode == "real" else self.session.decide(choice, *token))
        if changed:
            self._cue_event("declined" if choice == "decline" else "accepted")
            self._schedule_auto()

    def press_virtual(self, index, source="virtual key"):
        if index in APPROVAL_KEYS:
            self.approve(APPROVAL_KEYS[index], self._current_token(), source)
        elif index == 3:
            self.cancel_turn(source)
        elif index == 4:
            self.acknowledge()

    def cancel_turn(self, source="desktop"):
        if self.mode == "real":
            changed = self.real_backend.cancel(source=source)
        else:
            changed = self.session.cancel()
        if changed:
            self._cue_event("cancelled")

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
            if self.approval_description.toPlainText() != request.description:
                self.approval_description.setPlainText(request.description)
            self.approval_command.setText(f"Command: {request.command}" if request.command else "")
            self.approval_command.setVisible(bool(request.command))
            self.approval_cwd.setText(f"Working directory: {request.cwd}")
            self.approval_reason.setText(f"Reason: {request.reason}")
            if self._shown_key != key:
                self._shown_key = key
                self._pressed_approvals.clear()
                self.approval_card.update()
            actionable = self.session.presented == key and self._review_visible()
            self.once_button.setEnabled(actionable and "once" in request.allowed)
            self.session_button.setEnabled(actionable and "session" in request.allowed)
            self.decline_button.setEnabled(actionable and "decline" in request.allowed)
        else:
            self._shown_key = None
            self._pressed_approvals.clear()

        for i, key in self.virtual_buttons.items():
            if i in (0, 1, 2):
                names = {0: "once", 1: "session", 2: "decline"}
                enabled = bool(
                    request and actionable
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
            self._physical_snapshot = False
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
                self._physical_snapshot = False
                self._physical_held.clear()
                self._pending_physical = None
                self._ambiguous_chord = False
                self._physical_arm = None
                self._physical_arm_identity = None
                self._update_visual_holds()
                self._cue_event("connected")
            elif kind == "disconnected" and event[1] == self._physical_epoch:
                self._physical_epoch = 0
                self._physical_snapshot = False
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
                self._physical_snapshot = True
                self._pending_physical = None
                self._update_visual_holds()
            elif kind == "key" and self._physical_snapshot and event[1] == self._physical_epoch:
                _, _, index, pressed, arm, *received = event
                fresh = not received or time.monotonic() - received[0] <= 0.75
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
                        fresh and self._live_pi() and identity is not None
                        and arm is not None and arm == self._physical_arm
                        and identity[:2] == self._shown_key
                        and identity[2] == self._physical_epoch
                        and self.session.presented == self._shown_key
                        and self._review_visible()
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
                elif index == 3 and fresh and self._live_pi() and not self._session_locked:
                    self._pending_physical = None  # cancel takes precedence
                    self.press_virtual(index, source="Pi")
                elif index == 4 and fresh and self._live_pi() and not self._session_locked:
                    self.press_virtual(index, source="Pi")
        if self.keybow_server.events.empty():
            self._commit_physical_pending()
        elif self._pending_physical is not None:
            self._pending_physical = None
        if self._shown_key and self.session.presented and not self._review_visible():
            self._forget_presentation()
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


    def _live_pi(self):
        return (self._physical_snapshot and self.keybow_server.connected
                and self.keybow_server.connection_epoch == self._physical_epoch)

    def _commit_physical_pending(self):
        candidate = self._pending_physical
        if candidate is None or time.monotonic() - candidate[3] < 0.09:
            return
        self._pending_physical = None
        index, arm, identity, started = candidate

        def still_armed():
            return (not self._ambiguous_chord and time.monotonic() - started <= 0.75
                    and self._live_pi() and self.keybow_server.events.empty()
                    and identity == self._physical_arm_identity and arm == self._physical_arm
                    and self.session.presented == self._shown_key
                    and len(self._physical_held & APPROVAL_KEYS.keys()) <= 1)

        self.approve(APPROVAL_KEYS[index], identity[:2], source="Pi", input_guard=still_armed)

    def _sync_link(self):
        if not self.keybow_server.running:
            return
        # Hardware approval is only armed after a foreground review card is
        # both visible and marked presented. Blur/minimize/disconnect disarms.
        identity = None
        if (self._live_pi()
                and self.session.phase == Phase.APPROVAL
                and self._shown_key is not None
                and self.session.presented == self._shown_key
                and self._review_visible()):
            identity = (*self._shown_key, self._physical_epoch)
        if identity != self._physical_arm_identity:
            self._pending_physical = None
            self._ambiguous_chord = False
            self._physical_arm_identity = identity
            self._physical_arm = secrets.token_hex(16) if identity else None
        phase = self._phase_name()
        if phase == "approval" and self._physical_arm is None:
            phase = "working"
        self.keybow_server.publish(
            phase, arm=self._physical_arm,
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
        if self._window_session:
            self._window_session.close()
        if self._tray:
            self._tray.hide()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Keybow Codex Desktop")
    window = MainWindow(audit=ApprovalAudit())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
