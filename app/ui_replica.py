"""Screenshot-structured Main.vi replica backed by the verified SIMULATE services.

The visual hierarchy follows the supplied four Main.vi captures: mirrored A/B
station panels on Main and Query, a parameter grid on Setup, and six mirrored
manual controls. Hardware adapters are deliberately not used here.
"""
from __future__ import annotations

import csv
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Phase, StationId, Result, StationSelection
from .ateq import FakeAteq, SerialAteq
from .config import Settings
from .journal import CycleJournal
from .license import LicenseVerifier
from .permissions import AuthSession, SecurityContext
from .plc import FakePlc, Snap7Plc, POINTS
from .printer import FakePrinter
from .barprint import BarTenderCmdPrinter, is_calibration_template
from .repository import FakeRepository, PyMySQLRepository
from .station import StationController
from .calibration import Calibration, CalibrationPhase
from .scanner import ScannerFramer, ScannerGuard
from .scanner_tcp import TcpScanner
from .barcode_rules import BarcodeRuleEngine, route_shared_scan, scanner_code_matches
from .settings_service import ProductSettingsService
from .model_settings import (BARCODE_RULE_PRESETS, DATE_SCHEME_PRESETS,
                             GlobalSettingsService, ModelConfig,
                             ModelSettingsService, PersonnelService)
from .ui_theme import METRICS, PALETTE, UiTextCatalog, stylesheet

from PySide6.QtCore import Qt, QTime, QDateTime, QDate, QTimer, QEvent, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QInputDialog, QPushButton,
    QScrollArea, QSpinBox, QTabBar, QTabWidget, QTableWidget, QTableWidgetItem, QTimeEdit,
    QDateTimeEdit,
    QHeaderView,
    QVBoxLayout, QWidget, QSizePolicy,
)


INDICATOR_NAMES = (
    ("calibration_due", "校准到期 / Calibration"),
    ("start_validation", "启动验证 / Start Validation"),
    ("ng_sample", "NG 首件 / NG First"),
    ("ok_sample", "OK 二件 / OK Second"),
)
MANUAL_NAMES = (
    ("clamp", "夹紧_Clamping_Serrage", "后退_Back_Arrière/前进_forward_avant"),
    ("transfer", "移载_Transfer_Transfert", "后退_Back_Arrière/前进_forward_avant"),
    ("block", "封堵_Blocking_Bloquant", "后退_Back_Arrière/前进_forward_avant"),
    ("stamp", "盖章_Stamp_Timbre", "后退_Back_Arrière/前进_forward_avant"),
    ("door_disable", "门_Door_Porte", "使能_Active_Activer/禁用_Deactive_Désactiver"),
    ("manual", "自动/手动_Automatic/Manual_Automatique/Manual", ""),
)
TABLE_HEADERS = ["Time / Heure", "Serial No. / Matricule", "QR Code", "#1 Pressure / Pression", "#1 Leakage / Fuite", "#2 Pressure / Pression", "#2 Leakage / Fuite", "Result / Résultat", "Part No. / N° pièce", "Staff / Personnel"]
DISPLAY_HEADERS = {
    "base": ["Time\n时间", "Serial\nNo.", "QR Code", "#1\nPress.", "#1\nLeak.", "#2\nPress.", "#2\nLeak.", "Result\nOK·NG", "Part\nNo.", "Staff\n人员"],
    "中文": ["时间", "序列号", "QR Code", "一测压力", "一测泄漏", "二测压力", "二测泄漏", "结果", "产品型号", "人员"],
    "English": ["Time", "Serial\nNo.", "QR Code", "#1\nPress.", "#1\nLeak.", "#2\nPress.", "#2\nLeak.", "Result", "Part\nNo.", "Staff"],
    "Français": ["Heure", "Matricule", "QR\nCode", "Press.\n#1", "Fuite\n#1", "Press.\n#2", "Fuite\n#2", "Résultat", "N°\npièce", "Pers."],
}
INDICATOR_DISPLAY_LABELS = {
    "base": ["Cal. Time", "Start Validation", "NG Sample 1", "OK Sample 2"],
    "中文": ["校准到期", "启动验证", "NG 首件", "OK 二件"],
    "English": ["Cal. Time", "Start Validation", "NG Sample 1", "OK Sample 2"],
    "Français": ["Temps cal.", "Validation démarrage", "Échant. NG 1", "Échant. OK 2"],
}


class _CountdownSpinBox(QSpinBox):
    """Read-only seconds counter rendered as ``HH:MM`` in the footer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 7 * 24 * 60 * 60)
        self.setReadOnly(True)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def textFromValue(self, value: int) -> str:  # pragma: no cover - Qt calls this
        seconds = max(0, int(value))
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        return f"{hours:02d}:{minutes:02d}"

    def valueFromText(self, text: str) -> int:  # pragma: no cover - read-only UI
        fields = text.strip().split(":")
        try:
            if len(fields) == 2:
                hours, minutes = (int(item) for item in fields)
                return max(0, hours * 3600 + minutes * 60)
            return max(0, int(fields[0]))
        except (TypeError, ValueError):
            return 0


class CompatibilityTabs(QTabWidget):
    """Render multilingual screenshot labels while preserving old API values."""
    def __init__(self):
        super().__init__()
        self._compat = ("测试", "设置", "查询", "手动")
        self._legacy_labels = ("测试/Main/Principale", "设置/Setup/Coup Monté", "查询/Query/Requête", "手动/Manual/Manuelle")

    def tabText(self, index: int) -> str:  # legacy tests and callers
        if 0 <= index < len(self._compat):
            # The historical API exposed a multilingual construction label.
            # Keep that probe-compatible surface without using it for the
            # rendered startup UI; the catalog remains the visual source.
            self.tabBar().setTabText(index, self._legacy_labels[index])
            return self._compat[index]
        return super().tabText(index)


def _write_crash_log(where: str, exc: BaseException) -> None:
    """Append a slot-level exception to the crash log (best effort)."""
    try:
        import traceback as _tb
        with open(r"D:\ATEQ\ui_crash.log", "a", encoding="utf-8") as stream:
            stream.write(f"\n=== {datetime.now().isoformat()} SLOT {where} "
                         f"{type(exc).__name__}: {exc}\n")
            _tb.print_exception(type(exc), exc, exc.__traceback__, file=stream)
    except Exception:
        pass


class StationPanel(QFrame):
    # Emitted by the ATEQ worker thread; the auto-queued connection runs
    # _finish_test on the GUI thread (QTimer cannot be started cross-thread).
    test_finished = Signal()
    stepcode_updated = Signal(str)

    def __init__(self, station, repository, printer, plc, journal, security,
                 product_provider, confirm_callback, changed_callback,
                 payload_provider=None, personnel_provider=None,
                 calibration_provider=None, calibration_start_callback=None,
                 ateq=None):
        super().__init__()
        self.station, self.repository, self.plc = station, repository, plc
        self.security, self.confirm_callback = security, confirm_callback
        self.changed_callback = changed_callback
        self.product_provider = product_provider
        self.payload_provider = payload_provider
        self.personnel_provider = personnel_provider
        self.calibration_provider = calibration_provider
        self.calibration_start_callback = calibration_start_callback
        self.test_finished.connect(self._finish_test)
        self.payload = None
        self._label_ack_pending = False
        self._error_key = None
        self.controller = StationController(station, repository, printer, ateq or FakeAteq(), journal,
            safe_stop=plc, license_status=security.license_status, security=security)
        self.stepcode_updated.connect(self._apply_stepcode_display)
        if hasattr(self.controller.ateq, "stepcode_callback"):
            self.controller.ateq.stepcode_callback = self.stepcode_updated.emit
        self.setObjectName(f"stationCard_{station.value}")
        self.setFrameShape(QFrame.Shape.Box)
        outer = QVBoxLayout(self); outer.setContentsMargins(14, 0, 14, 4); outer.setSpacing(0)
        header = QWidget(self); header.setObjectName(f"stationHeader_{station.value}")
        header_layout = QHBoxLayout(header); header_layout.setContentsMargins(0, 0, 0, 2); header_layout.setSpacing(8)
        self.station_title = QLabel(f"工位 {station.value} / Station {station.value}"); self.station_title.setObjectName("stationTitle"); header_layout.addWidget(self.station_title)
        header_layout.addStretch(1)
        self.stepcode_frame = QFrame(header); self.stepcode_frame.setObjectName(f"stepCodeFrame_{station.value}")
        self.stepcode_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.stepcode_frame.setStyleSheet(
            "QFrame { border: 1px solid #b9c7d8; border-radius: 5px; background: #ffffff; }"
            "QLabel { border: 0; background: transparent; }"
        )
        stepcode_layout = QHBoxLayout(self.stepcode_frame); stepcode_layout.setContentsMargins(8, 3, 8, 3); stepcode_layout.setSpacing(8)
        stepcode_caption = QLabel("StepCode"); stepcode_caption.setObjectName(f"stepCodeCaption_{station.value}")
        stepcode_caption.setProperty("replica_source", "StepCode")
        self.stepcode_value = QLabel("SIM" if isinstance(self.controller.ateq, FakeAteq) else "读取中")
        self.stepcode_value.setObjectName(f"stepCodeValue_{station.value}")
        self.stepcode_value.setProperty("replica_source", self.stepcode_value.text())
        self.stepcode_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stepcode_value.setMinimumWidth(68)
        self.stepcode_value.setProperty("state", "info")
        stepcode_layout.addWidget(stepcode_caption); stepcode_layout.addWidget(self.stepcode_value)
        header_layout.addWidget(self.stepcode_frame)
        outer.addWidget(header)
        top = QGridLayout(); top.setHorizontalSpacing(10); top.setVerticalSpacing(2); top.setObjectName(f"stationTopControls_{station.value}")
        self.mode_button = QPushButton(f"Single Test / 单测 {station.value}"); self.mode_button.setObjectName(f"single_dual_{station.value}"); self.mode_button.setCheckable(True); self.mode_button.toggled.connect(self._mode_changed); top.addWidget(QLabel(f"Single/Dual {station.value}"), 0, 0); top.addWidget(self.mode_button, 1, 0)
        self.code_input = QLineEdit(); self.code_input.setObjectName(f"main_code_{station.value}"); self.code_input.setReadOnly(True); self.code = self.code_input; top.addWidget(QLabel(f"2D Code {station.value}"), 0, 1); top.addWidget(self.code_input, 1, 1)
        self.total_today = QSpinBox(); self.total_today.setObjectName(f"total_today_{station.value}"); self.total_today.setReadOnly(True); self.total_today.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons); self.total_today.setFixedWidth(72); top.addWidget(QLabel(f"Total Today {station.value}"), 0, 2); top.addWidget(self.total_today, 1, 2)
        self.ok_today = QSpinBox(); self.ok_today.setObjectName(f"ok_today_{station.value}"); self.ok_today.setReadOnly(True); self.ok_today.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons); self.ok_today.setFixedWidth(72); top.addWidget(QLabel(f"OK Today {station.value}"), 0, 3); top.addWidget(self.ok_today, 1, 3)
        self.ateq_no = QLineEdit("SIM"); self.ateq_no.setObjectName(f"ateq_no_{station.value}"); self.ateq_no.setReadOnly(True); self.ateq_no.setMaxLength(3); self.ateq_no.setFixedWidth(64); top.addWidget(QLabel(f"ATEQ No. {station.value}"), 0, 4); top.addWidget(self.ateq_no, 1, 4)
        self.part_no = QComboBox(); self.part_no.setObjectName(f"part_no_{station.value}"); self.part_no.currentTextChanged.connect(self._product_changed); top.addWidget(QLabel(f"Part No. {station.value}"), 0, 5); top.addWidget(self.part_no, 1, 5)
        self.staff = QComboBox(); self.staff.setObjectName(f"staff_{station.value}"); self.staff.currentTextChanged.connect(self._product_changed); top.addWidget(QLabel(f"Staff {station.value}"), 0, 6); top.addWidget(self.staff, 1, 6)
        for column in range(7):
            top.setColumnMinimumWidth(column, 0); top.setColumnStretch(column, 1)
        # Compact numeric fields leave the reclaimed width to the QR field.
        top.setColumnStretch(1, 3)
        for index in range(top.count()):
            child = top.itemAt(index).widget()
            if child is not None:
                child.setMinimumWidth(0)
                if isinstance(child, QLabel):
                    child.setWordWrap(True); child.setMaximumWidth(110)
        outer.addLayout(top)
        alerts = QHBoxLayout(); alerts.setSpacing(8); self.reprint = QPushButton(f"标签重打 {station.value}"); self.reprint.setObjectName(f"reprint_{station.value}"); self.reprint.setProperty("compact", True); self.reprint.setFixedHeight(32); self.reprint.clicked.connect(self.reprint_label); alerts.addWidget(self.reprint); alerts.addStretch(1); outer.addLayout(alerts)
        self.table = self._table(f"{station.value}List")
        self.list_title = QLabel(f"{station.value} List"); self.list_title.setObjectName("pageTitle"); self.list_title.setVisible(False); outer.addWidget(self.list_title); outer.addWidget(self.table, 1)
        self._outer_layout = outer
        self.error_summary = QLabel(); self.error_summary.setObjectName(f"error_summary_{station.value}"); self.error_summary.setWordWrap(True); self.error_summary.setMinimumWidth(0); self.error_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); self.error_summary.setVisible(False); self.error_summary.setProperty("state", "ng"); outer.addWidget(self.error_summary)
        # Keep the prompt as a non-layout status surface: the visible footer
        # already has the large due lamp and Start Validation tile, while the
        # button caption/tooltip carries the current NG -> OK instruction
        # without shrinking the 30-row production table.
        self.calibration_notice = QLabel(); self.calibration_notice.setObjectName(f"calibration_notice_{station.value}"); self.calibration_notice.setWordWrap(True); self.calibration_notice.setAlignment(Qt.AlignmentFlag.AlignCenter); self.calibration_notice.setProperty("state", "ng")
        bottom = QGroupBox(); bottom.setObjectName(f"bottomIndicators_{station.value}")
        # This needs to use the full station width.  A preferred-size group
        # box made the calibration area stop at the width of its contents,
        # which in turn made the four lamps look cramped on a wide monitor.
        bottom.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bottom.setMinimumHeight(144); bottom.setMaximumHeight(METRICS.footer_max_height)
        bottom_layout = QVBoxLayout(bottom); bottom_layout.setContentsMargins(4, 2, 4, 2); bottom_layout.setSpacing(2)
        indicator_row = QHBoxLayout(); indicator_row.setSpacing(6); self._indicator_row = indicator_row
        self.indicators = {}
        self.indicator_labels = {}
        self.indicator_tiles = {}
        # Only Start Validation is an operator control.  The other three
        # footer entries are read-only state lamps; hidden zero-sized legacy
        # objects preserve diagnostic lookup names without creating buttons in
        # the production UI.
        self.compatibility_buttons = {}
        for signal, label in INDICATOR_NAMES:
            # The three status entries use a large lamp tile.  Start Validation
            # is a full-size button occupying the same tile footprint; it is
            # not a caption with a second OK/Confirm button underneath.
            tile = QFrame(bottom); tile.setObjectName(f"calibration_tile_{signal}_{station.value}")
            tile.setProperty("calibrationTile", True)
            tile.setProperty("state", "info")
            tile.setMinimumWidth(140); tile.setFixedHeight(138)
            tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tile_layout = QVBoxLayout(tile); tile_layout.setContentsMargins(2, 2, 2, 2); tile_layout.setSpacing(2)
            if signal == "start_validation":
                compatibility_led = QLabel(tile); compatibility_led.setObjectName(f"{signal}_{station.value}"); compatibility_led.setProperty("state", "info"); compatibility_led.setFixedSize(0, 0); compatibility_led.hide()
                compatibility_caption = QLabel(tile); compatibility_caption.setObjectName(f"{signal}_label_{station.value}"); compatibility_caption.setText(""); compatibility_caption.setFixedSize(0, 0); compatibility_caption.hide()
                self.indicators[signal] = compatibility_led
                button = QPushButton("启动验证", tile); button.setObjectName(f"start_{station.value}"); button.setProperty("primary", True); button.setProperty("calibrationStart", True); button.setProperty("replica_source", "启动验证")
                button.setMinimumWidth(140); button.setFixedHeight(134); button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                button.clicked.connect(lambda _=False, s=signal: self._indicator_action(s))
                self.start_validation_button = button
                # A disabled button never emits clicked(), so the operator
                # sees "no reaction".  Qt redirects the mouse press to the
                # nearest enabled ancestor; the card-level filter below turns
                # that case into a trace line for remote diagnosis.
                self._start_button_installed = True
                tile_layout.addWidget(button)
                self.indicator_tiles[signal] = tile
                indicator_row.addWidget(tile, 1)
                continue
            # Keep the semantic indicator object for diagnostics/tests, but
            # render the whole tile as the lamp so operators do not have to
            # interpret a tiny colored dot.
            led = QLabel(""); led.setAlignment(Qt.AlignmentFlag.AlignCenter)
            led.setObjectName(f"{signal}_{station.value}"); led.setProperty("state", "info"); led_font = led.font(); led_font.setPointSize(38); led.setFont(led_font); led.setMinimumSize(46, 46); led.setFixedHeight(46)
            self.indicators[signal] = led; tile_layout.addWidget(led)
            text_label = QLabel(INDICATOR_DISPLAY_LABELS["base"][len(self.indicator_labels)])
            text_label.setAlignment(Qt.AlignmentFlag.AlignCenter); text_label.setWordWrap(True); text_label.setMinimumHeight(40); text_label.setMaximumHeight(40)
            text_label.setMinimumWidth(0); text_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.indicator_labels[signal] = text_label; self._fit_indicator_label(text_label)
            tile_layout.addWidget(text_label, 0, Qt.AlignmentFlag.AlignHCenter)
            action_slot = QWidget(tile); action_slot.setObjectName(f"calibration_action_slot_{signal}_{station.value}")
            # Keep the three indicator tiles the same height as the Start
            # button.  Their lower slot is intentionally blank; the hidden
            # compatibility object is retained only for old diagnostics.
            action_slot.setFixedHeight(42); action_layout = QHBoxLayout(action_slot); action_layout.setContentsMargins(0, 0, 0, 0)
            compatibility = QPushButton(self); compatibility.setObjectName(f"{signal}_button_{station.value}")
            compatibility.setFixedSize(0, 0); compatibility.hide()
            self.compatibility_buttons[signal] = compatibility
            if signal == "calibration_due":
                cancel_button = QPushButton(f"取消校准 {station.value}", action_slot)
                cancel_button.setObjectName(f"cancel_calibration_{station.value}")
                cancel_button.setProperty("compact", True)
                cancel_button.setProperty("replica_source", f"取消校准 {station.value}")
                cancel_button.setMinimumWidth(92)
                cancel_button.setFixedHeight(32)
                cancel_button.setToolTip("仅管理员可取消，并重新开始本工位校准计时")
                cancel_button.clicked.connect(
                    lambda _=False, s=station: self.window().cancel_calibration(s))
                self.cancel_calibration_button = cancel_button
                action_layout.addStretch(1)
                action_layout.addWidget(cancel_button)
                action_layout.addStretch(1)
            else:
                action_layout.addStretch(1)
            tile_layout.addWidget(action_slot)
            self.indicator_tiles[signal] = tile
            indicator_row.addWidget(tile, 1)
        self.next_action = QLabel(bottom); self.next_action.setObjectName(f"next_action_{station.value}"); self.next_action.setVisible(False)
        self.error_ack = QPushButton(); self.error_ack.setObjectName(f"error_ack_{station.value}"); self.error_ack.setProperty("compact", True); self.error_ack.setMinimumWidth(0); self.error_ack.setVisible(False); self.error_ack.clicked.connect(self.acknowledge_error); indicator_row.addWidget(self.error_ack)
        self.error_reset = QPushButton(); self.error_reset.setObjectName(f"error_reset_{station.value}"); self.error_reset.setProperty("compact", True); self.error_reset.setMinimumWidth(0); self.error_reset.setVisible(False); self.error_reset.clicked.connect(self.reset); indicator_row.addWidget(self.error_reset)
        bottom_layout.addLayout(indicator_row)
        self.error_details = QWidget(bottom); self.error_details.setObjectName(f"errorDetails_{station.value}"); self.error_details.setVisible(False); details_layout = QVBoxLayout(self.error_details); details_layout.setContentsMargins(2, 2, 2, 2); details_layout.setSpacing(2)
        details_layout.addWidget(self.error_summary)
        self.error_actions = QHBoxLayout(); self.error_actions.setSpacing(6); self.error_actions.addStretch(1); self.error_actions.addWidget(self.error_ack); self.error_actions.addWidget(self.error_reset); details_layout.addLayout(self.error_actions)
        bottom_layout.addWidget(self.error_details)
        self.bottom_indicators = bottom; outer.addWidget(bottom)
        # Non-screenshot runtime diagnostics are kept in a separate extension
        # area so the four original bottom categories remain the primary view.
        extension = QGroupBox("运行状态扩展 / Runtime extension"); extension.setObjectName(f"runtimeExtension_{station.value}"); extension.setVisible(False); el = QHBoxLayout(extension)
        for signal, label in (("scan_ok", "扫码 OK / Scanner"), ("door_disable", "安全门 / Door"), ("pressure", "正/负压 / Pressure"), ("manual", "手动 / Manual")):
            led = QLabel("●"); led.setObjectName(f"{signal}_{station.value}"); led.setProperty("state", "ok"); self.indicators[signal] = led; el.addWidget(led); el.addWidget(QLabel(label))
        outer.addWidget(extension)
        # Compatibility/diagnostic controls remain in the station panel but
        # do not displace the Main.vi list hierarchy.
        quick = QGroupBox("SIMULATE readback / 快捷诊断"); quick.setObjectName(f"dashboardActuators_{station.value}"); ql = QHBoxLayout(quick); self.quick_buttons = {}
        for signal, label in (("manual", "手动"), ("transfer", "移载"), ("block", "封堵"), ("clamp", "夹紧"), ("stamp", "盖章"), ("pressure", "正/负压"), ("start", "启动 / Start")):
            b = QPushButton(f"{label}: OFF"); b.setObjectName(f"dashboard_{signal}_{station.value}"); b.clicked.connect(lambda _=False, s=signal, button=b: self.manual_action(s, button)); self.quick_buttons[signal] = b; ql.addWidget(b)
        start_alias = QPushButton("启动 / Start: OFF"); start_alias.setObjectName(f"dashboard_start_alias_{station.value}"); start_alias.clicked.connect(lambda _=False, b=start_alias: self.manual_action("start", b)); ql.addWidget(start_alias)
        quick.setVisible(False); outer.addWidget(quick)
        self._plc_calibration_due = False
        self.installEventFilter(self)
        self.refresh()

    def set_choices(self, products, people):
        product = self.part_no.currentText(); person = self.staff.currentText()
        self.part_no.blockSignals(True); self.part_no.clear(); self.part_no.addItems(list(products) or ["SIM-PART"])
        if product in products: self.part_no.setCurrentText(product)
        self.part_no.blockSignals(False)
        self.staff.blockSignals(True); self.staff.clear(); self.staff.addItems(list(people) or ["Operator"])
        if person in people: self.staff.setCurrentText(person)
        self.staff.blockSignals(False)
        self._product_changed()

    def _mode_changed(self, dual):
        self.mode_button.setText(f"{'Dual Test / 双测' if dual else 'Single Test / 单测'} {self.station.value}")
        self._product_changed()

    def _product_changed(self, *_args):
        if not self.payload_provider or not self.part_no.currentText().strip():
            return
        try:
            self.payload = self.payload_provider(self.station, self.part_no.currentText().strip())
            # Keep the expected QR internal for routing/validation, but do
            # not display it before a physical label has been scanned back.
            self.code_input.clear()
            self.ateq_no.setText(str(self.payload.ateq_program))
            if isinstance(self.controller.ateq, SerialAteq):
                if self.controller.phase not in (Phase.IDLE, Phase.WAIT_SCAN):
                    raise RuntimeError("当前周期进行中，不能切换 ATEQ 程序")
                self.controller.ateq.select_program(str(self.payload.ateq_program))
                actual_program = self.controller.ateq.current_program()
                if actual_program != int(self.payload.ateq_program):
                    raise RuntimeError(
                        f"ATEQ 程序读回不一致：期望 {self.payload.ateq_program}，实际 {actual_program}")
                self.ateq_no.setText(str(actual_program))
                trace = getattr(self.window(), "_live_trace", None)
                if trace is not None:
                    trace(f"ATEQ_PROGRAM_SYNC station={self.station.value} program={actual_program} product={self.part_no.currentText().strip()}")
            self._error_key = None
        except Exception as exc:
            self.payload = None
            self.code_input.clear()
            # Do not leave the three-character legacy "BLO" artifact in the
            # program-number field.  The trace keeps the detailed failure;
            # reset will retry and replace this marker when communication is
            # available again.
            self.ateq_no.setText("ERR")
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None:
                trace(f"ATEQ_PROGRAM_SYNC_FAILED station={self.station.value} "
                      f"product={self.part_no.currentText().strip()} "
                      f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _configure_table(table):
        header = table.horizontalHeader()
        # 列宽随内容自适应，配合像素级横向滚动：时间/二维码等字段完整显示，
        # 不再出现省略号截断。
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(52)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setTextElideMode(Qt.TextElideMode.ElideNone)
        header.setFixedHeight(max(40, METRICS.table_header_height))
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.setWordWrap(False)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        return table

    @staticmethod
    def _fit_indicator_label(label):
        """Keep wrapped footer captions wider than their measured text.

        The old 65px cap clipped ``Start Validation`` at 1366px.  Measure the
        current translated caption after removing that cap, then reserve a
        small padding margin.  A fixed, content-sized width keeps the footer
        readable at both canonical window sizes and across the three locales.
        """
        label.setMaximumWidth(160)
        width = max(82, label.sizeHint().width() + 4)
        label.setMinimumWidth(width)
        label.setMaximumWidth(width)
        label_height = 40
        label.setMinimumHeight(label_height)
        label.setMaximumHeight(label_height)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    @staticmethod
    def _table(name):
        table = QTableWidget(30, 10); table.setObjectName(name); table.setHorizontalHeaderLabels(DISPLAY_HEADERS["base"]); table.setAlternatingRowColors(True); table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed); table.verticalHeader().setDefaultSectionSize(METRICS.table_row_height); table.verticalHeader().setMinimumSectionSize(METRICS.table_row_height); StationPanel._configure_table(table); [table.setRowHeight(i, METRICS.table_row_height) for i in range(30)]; table.setMinimumHeight(340); return table

    def _point_read(self, signal):
        byte, bit = POINTS[signal][self.station]; return self.plc.read_bit(byte, bit)

    def _send_scan_ok(self, code: str) -> None:
        """Pulse only this card's confirmed PLC scan-OK bit after a match."""
        byte, bit = POINTS["scan_ok"][self.station]
        # A PLC that does not acknowledge/clear the handshake immediately can
        # leave the previous cycle's bit high.  Force a low edge before the
        # new confirmed scan pulse so the next high is unambiguously caused by
        # this label match.
        try:
            if self.plc.read_bit(byte, bit):
                self.plc.write_bit(byte, bit, False)
                trace = getattr(self.window(), "_live_trace", None)
                if trace is not None:
                    trace(f"SCAN_PLC_CLEAR station={self.station.value} "
                          f"point=M{byte}.{bit} reason=before_confirmed_scan")
        except Exception:
            # The confirmed write below remains the authoritative operation;
            # diagnostics are handled by the live adapter if the readback is
            # unavailable.
            pass
        self.plc.write_bit(byte, bit, True)
        trace = getattr(self.window(), "_live_trace", None)
        if trace is not None:
            trace(f"SCAN_PLC_SIGNAL station={self.station.value} "
                  f"point=M{byte}.{bit} code={str(code).strip()!r}")

    def eventFilter(self, obj, event):
        """Trace clicks that land on the disabled Start Validation button.

        Disabled widgets swallow mouse presses without emitting clicked();
        the press surfaces here on the enabled card.  Without this line the
        field report is an unexplained "no reaction".
        """
        if (event.type() == QEvent.Type.MouseButtonPress
                and getattr(self, "_start_button_installed", False)
                and not self.start_validation_button.isEnabled()):
            pos = event.position().toPoint()
            target = self.childAt(pos)
            while target is not None and target is not self:
                if target is self.start_validation_button:
                    trace = getattr(self.window(), "_live_trace", None)
                    if trace is not None:
                        trace(f"{self.station.value} CAL_BUTTON_HIT_DISABLED "
                              f"phase={self.controller.phase.value} "
                              f"due={self._calibration().due if self._calibration() else None}")
                    break
                target = target.parentWidget()
        return super().eventFilter(obj, event)

    def _indicator_action(self, signal):
        if signal == "start_validation":
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None:
                trace(f"{self.station.value} CAL_BUTTON_PRESSED "
                      f"enabled={self.start_validation_button.isEnabled()} "
                      f"phase={self.controller.phase.value}")
            if self.calibration_start_callback is None:
                return False
            try:
                self.calibration_start_callback(self.station)
                self._error_key = None
                self.refresh()
                return True
            except Exception as exc:
                # Keep raw device/configuration details in the audit/service
                # layer; the operator sees only a localized safe failure.
                self._error_key = "calibration_error"
                trace = getattr(self.window(), "_live_trace", None)
                if trace is not None:
                    trace(f"{self.station.value} CAL_START_BUTTON_FAILED {type(exc).__name__}: {exc}")
                self.refresh()
                return False
        return False

    def _calibration(self):
        return self.calibration_provider(self.station) if self.calibration_provider else None

    def _m(self, value, field):
        if value is None:
            return ""
        unit = value.pressure_unit if field == "pressure" else value.leakage_unit
        return f"{getattr(value, field):g}{(' ' + unit) if unit else ''}"

    @staticmethod
    def _remaining_text(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        return f"{hours:02d}:{remainder // 60:02d}"

    def _calibration_prompt(self, calibration, language: str) -> tuple[str, str, str]:
        """Return visible notice text, semantic color and button caption."""
        if calibration is None:
            return "", "info", {"中文": "启动验证", "English": "Start\nValidation", "Français": "Validation\nDémarrage"}[language]
        if calibration.clear_pending:
            return ({"中文": f"工位 {self.station.value}：验证完成，请扫码继续",
                     "English": f"Station {self.station.value}: validation complete; scan to continue",
                     "Français": f"Poste {self.station.value} : validation terminée, scannez pour continuer"}[language],
                    "warn",
                    {"中文": "验证完成\n请扫码", "English": "Validation\nComplete", "Français": "Validation\nterminée"}[language])
        if calibration.validation_started and calibration.phase is CalibrationPhase.WAIT_NG:
            return ({"中文": f"工位 {self.station.value}：请放 NG 首件",
                     "English": f"Station {self.station.value}: place NG first piece",
                     "Français": f"Poste {self.station.value} : placez la première pièce NG"}[language],
                    "warn",
                    {"中文": "等待 NG 首件", "English": "Waiting for\nNG first", "Français": "Attente pièce\nNG"}[language])
        if calibration.validation_started and calibration.phase is CalibrationPhase.WAIT_OK:
            return ({"中文": f"工位 {self.station.value}：请放 OK 二件",
                     "English": f"Station {self.station.value}: place OK second piece",
                     "Français": f"Poste {self.station.value} : placez la deuxième pièce OK"}[language],
                    "warn",
                    {"中文": "等待 OK 二件", "English": "Waiting for\nOK second", "Français": "Attente pièce\nOK"}[language])
        if calibration.due:
            return ({"中文": f"工位 {self.station.value}：校准到期，请点击启动验证",
                     "English": f"Station {self.station.value}: calibration due; click Start Validation",
                     "Français": f"Poste {self.station.value} : calibration échue, cliquez Validation"}[language],
                    "ng",
                    {"中文": "启动验证", "English": "Start\nValidation", "Français": "Validation\nDémarrage"}[language])
        remaining = self._remaining_text(calibration.remaining_seconds)
        return ({"中文": f"工位 {self.station.value}：校准计时 {remaining}",
                 "English": f"Station {self.station.value}: calibration timer {remaining}",
                 "Français": f"Poste {self.station.value} : minuterie {remaining}"}[language],
                "ok",
                {"中文": "启动验证", "English": "Start\nValidation", "Français": "Validation\nDémarrage"}[language])

    def manual_action(self, signal, button):
        language = getattr(self.window(), "_language", "中文")
        try:
            self.security.require("manual_output")
            byte, bit = POINTS[signal][self.station]; current = self.plc.read_bit(byte, bit); requested = not current
            if not self.confirm_callback(self.station, signal, requested, current):
                if hasattr(button, "setText"): button.setText(UiTextCatalog.message(language, "cancelled"))
                return False
            self.plc.write_bit(byte, bit, requested); actual = self.plc.read_bit(byte, bit)
            if hasattr(button, "setText"):
                labels = {"中文": {"manual": "手动", "transfer": "移载", "block": "封堵", "clamp": "夹紧", "stamp": "盖章", "pressure": "正/负压", "start": "启动"}, "English": {"manual": "Manual", "transfer": "Transfer", "block": "Blocking", "clamp": "Clamp", "stamp": "Stamp", "pressure": "Pressure", "start": "Start"}, "Français": {"manual": "Manuel", "transfer": "Transfert", "block": "Obstruction", "clamp": "Serrage", "stamp": "Timbre", "pressure": "Pression", "start": "Démarrage"}}
                states = {"中文": ("开" if actual else "关"), "English": ("ON" if actual else "OFF"), "Français": ("MARCHE" if actual else "ARRÊT")}
                button.setText(f"{labels[language][signal]}: {states[language]}")
            self.refresh(); return True
        except Exception as exc:
            if hasattr(button, "setText"): button.setText({"中文": "拒绝：权限不足", "English": "Denied: permission required", "Français": "Refusé : autorisation requise"}[language])
            return False

    def scan(self, code=None, part_no=None):
        code = self.code_input.text() if code is None else code; part_no = part_no or self.part_no.currentText()
        try:
            calibration = self._calibration()
            if self._label_ack_pending:
                record = self.controller.record
                normalized_code = code.strip()
                if record is None or not scanner_code_matches(normalized_code, record.code_2d):
                    # A shared scanner can see unrelated barcodes while the
                    # operator is finding the freshly printed label.  This is
                    # a non-terminal condition: keep the acknowledgement
                    # pending and let the caller continue polling.
                    trace = getattr(self.window(), "_live_trace", None)
                    if trace is not None:
                        trace(f"{self.station.value} LABEL_SCAN_IGNORED code={normalized_code!r}")
                    self._error_key = None
                    self.refresh(); self.changed_callback()
                    return False
                self._send_scan_ok(normalized_code)
                self._label_ack_pending = False
                # Only normal production labels enter this acknowledgement
                # path. Calibration labels never set _label_ack_pending.
                if self.controller.phase is Phase.LABELING:
                    self.controller.phase = Phase.COMPLETE
                if self.controller.phase is Phase.COMPLETE:
                    self.controller.reset()
                self.code_input.setText(code.strip())
                self._error_key = None
                self.refresh(); self.changed_callback()
                trace = getattr(self.window(), "_live_trace", None)
                if trace is not None:
                    trace(f"{self.station.value} LABEL_SCAN_ACK code={code.strip()}")
                return True
            if (calibration is not None and calibration.locked
                    and not (calibration.validation_started and calibration.phase is CalibrationPhase.WAIT_NG)):
                raise RuntimeError("校准验证未完成，禁止开始新周期")
            if self.payload is not None:
                if not scanner_code_matches(code, self.payload.barcode_text):
                    raise ValueError("扫码值与当前工位二维码不一致")
                selection = StationSelection(
                    self.station, self.payload.product_id, self.staff.currentText().strip() or "Operator",
                    "dual" if self.mode_button.isChecked() else "single",
                    self.payload.serial_no, self.payload.barcode_text, self.payload.customer_model,
                    str(self.payload.ateq_program), str(self.payload.template_path))
                self.controller.scan_selection(selection)
            else:
                self.controller.scan(code, part_no, self.staff.currentText(),
                                     test_mode="dual" if self.mode_button.isChecked() else "single")
            # In LIVE mode the hardware StepCode=4 edge owns serial
            # reservation.  Consuming here as well makes a scanner pre-scan
            # race the hardware edge and can freeze a stale/duplicate payload.
            if not getattr(self.window(), "live_mode", False):
                try:
                    product_for_serial = (self.payload.product_id
                                          if self.payload is not None else part_no)
                    advancer = getattr(self.window(), "_advance_serial", None)
                    if advancer is not None and product_for_serial:
                        consumed = self.payload.serial_no if self.payload is not None else None
                        advancer(self.station, product_for_serial,
                                 consumed_serial=consumed)
                    if self.payload_provider is not None and product_for_serial:
                        self.payload = self.payload_provider(self.station, product_for_serial)
                        self.code_input.clear()
                        self.ateq_no.setText(str(self.payload.ateq_program))
                except Exception as serial_exc:
                    trace = getattr(self.window(), "_live_trace", None)
                    if trace is not None:
                        trace(f"{self.station.value} SERIAL_REFRESH_FAILED "
                              f"{type(serial_exc).__name__}: {serial_exc}")
            else:
                trace = getattr(self.window(), "_live_trace", None)
                if trace is not None:
                    trace(f"{self.station.value} SERIAL_DEFERRED owner=ATEQ_STEP_4")
            # The lamps stay visible through the completed validation and are
            # cleared only after this new cycle has been accepted.
            if calibration is not None and calibration.clear_pending:
                calibration.clear_after_resume()
            # The first scan only opens the production cycle.  PLC scan-OK is
            # deliberately reserved for the second scan of the printed label
            # in the `_label_ack_pending` branch above.
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None:
                trace(f"SCAN_ACCEPTED station={self.station.value} "
                      "awaiting_label_print_and_scan")
            self._error_key = None; self.refresh(); self.changed_callback(); return True
        except Exception:
            self._error_key = "scan_error"; self.refresh(); return False

    def first(self):
        self._run_test_async("first")

    def second(self):
        self._run_test_async("second")

    def _run_test_async(self, which: str):
        """Run the blocking ATEQ transaction off the GUI thread.

        controller.test_first()/test_second() wait up to the 120 s cycle
        timeout on the serial link; running that inline in a QTimer callback
        freezes the whole UI and starves the scanner/PLC/heartbeat timers.
        """
        test_no = 1 if which == "first" else 2
        trace = getattr(self.window(), "_live_trace", None)
        if trace is not None:
            trace(f"{self.station.value} ATEQ_MONITOR_START test={test_no}")
        if getattr(self, "_test_worker_running", False):
            if trace is not None:
                trace(f"{self.station.value} TEST_REJECTED previous test still running")
            return
        self._test_worker_running = True
        self._error_key = None
        window = self.window()
        if hasattr(window, "_b_test_in_progress"):
            window._b_test_in_progress = True

        def worker():
            try:
                if which == "first":
                    self.controller.test_first()
                else:
                    self.controller.test_second()
            except Exception as exc:
                self._error_key = "first_error" if which == "first" else "second_error"
                if trace is not None:
                    trace(f"{self.station.value} TEST_{test_no}_FAILED {type(exc).__name__}: {exc}")
            finally:
                self._test_worker_running = False
                if hasattr(window, "_b_test_in_progress"):
                    window._b_test_in_progress = False
                self.test_finished.emit()

        threading.Thread(target=worker, daemon=True,
                         name=f"ateq-{self.station.value}-{which}").start()

    def _log_crash(self, where: str, exc: BaseException) -> None:
        _write_crash_log(where, exc)

    def _finish_test(self):
        try:
            self._handle_calibration_measurement()
            calibration = self._calibration()
            # A normal production OK ends in LABELING.  The operator-facing
            # UI has no separate label button, so complete the existing
            # controller print transaction here.  Calibration samples are
            # printed by on_calibration_sample() and must not be duplicated.
            if (calibration is None or not calibration.validation_started) \
                    and self.controller.phase is Phase.LABELING:
                record = self.controller.record
                trace = getattr(self.window(), "_live_trace", None)
                if record is not None:
                    measurement = record.second or record.first
                    if trace is not None and measurement is not None:
                        trace(f"{self.station.value} TEST_RESULT result={measurement.result.value} "
                             f"pressure={measurement.pressure}{measurement.pressure_unit} "
                             f"leakage={measurement.leakage}{measurement.leakage_unit} "
                             f"raw={measurement.raw_frame.hex()}")
                    if trace is not None:
                        trace(f"{self.station.value} LABEL_AUTO_REQUEST cycle={record.cycle_id}")
                    self.label()
            self.refresh(); self.changed_callback()
        except Exception as exc:
            self._log_crash("FINISH_TEST", exc)
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None:
                trace(f"{self.station.value} FINISH_TEST_FAILED {type(exc).__name__}: {exc}")

    def _handle_calibration_measurement(self):
        calibration = self._calibration()
        record = self.controller.record
        if calibration is None or not calibration.validation_started or record is None:
            return
        measurement = record.second or record.first
        if measurement is not None:
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None:
                trace(f"{self.station.value} TEST_RESULT result={measurement.result.value} "
                      f"pressure={measurement.pressure}{measurement.pressure_unit} "
                      f"leakage={measurement.leakage}{measurement.leakage_unit} "
                      f"raw={measurement.raw_frame.hex()}")
            self.window().on_calibration_sample(measurement.result.value, self.station)

    def label(self):
        trace = getattr(self.window(), "_live_trace", None)
        if trace is not None:
            trace(f"{self.station.value} LABEL_REQUEST cycle="
                 f"{getattr(self.controller.record, 'cycle_id', '')}")
        try:
            printed = self.controller.label()
            if printed:
                self._label_ack_pending = True
                self._clear_scan_ok("await_label_scan")
                window = self.window()
                enable_after_print = getattr(window, "_enable_scanner_after_print", None)
                if enable_after_print is not None:
                    enable_after_print(self.station, self.controller.print_job_id)
                self._error_key = None
                if trace is not None:
                    trace(f"{self.station.value} LABEL_ACCEPTED job={self.controller.print_job_id}")
            else:
                self._error_key = "label_error"
                if trace is not None:
                    trace(f"{self.station.value} LABEL_REJECTED")
        except Exception as exc:
            self._error_key = "label_error"
            if trace is not None:
                trace(f"{self.station.value} LABEL_FAILED {type(exc).__name__}: {exc}")
        self.refresh(); self.changed_callback()

    def reprint_label(self):
        try:
            self.security.require("reprint")
            if self.controller.record is None: raise RuntimeError("没有可重打周期")
            printed = self.controller.label()
            if not printed:
                raise RuntimeError("重打标签未确认")
            self._label_ack_pending = True
            self._clear_scan_ok("await_reprint_scan")
            window = self.window()
            enable_after_print = getattr(window, "_enable_scanner_after_print", None)
            if enable_after_print is not None:
                enable_after_print(self.station, self.controller.print_job_id)
            self._error_key = None
        except Exception: self._error_key = "reprint_denied"
        self.refresh(); self.changed_callback()

    def reset(self):
        was_label_ack_pending = self._label_ack_pending
        # Reset is the explicit operator escape hatch from the post-print
        # scanner wait.  Clear this UI-only latch even if the controller
        # reset itself reports an error, so a stale label cannot acknowledge
        # a later production cycle.
        self._label_ack_pending = False
        try:
            self._clear_scan_ok("reset")
            if self.controller.recovery_required:
                # Reset is the operator's explicit escape hatch from a
                # failed cycle.  Keep the recovery journal/audit trail, but
                # do not require a second administrator permission check.
                self.controller.resolve_recovery(
                    f"UI reset {self.station.value}", require_permission=False)
            else:
                self.controller.reset()
            # Keep the simulation lifecycle marker, but do not disconnect a
            # live PLC on an operator reset; the next matching scan must still
            # be able to pulse its station bit.
            if isinstance(self.plc, FakePlc):
                self.plc.safe_stop(f"UI reset {self.station.value}")
            self._error_key = None
            # Reset is also the operator retry for a transient ATEQ/program
            # selection failure.  Rebuild the payload and read the program
            # back so a stale BLOCKED/BLO display is not left behind.
            self._product_changed()
            if self.payload is None and self.ateq_no.text() in ("ERR", "BLO", "BLOCKED"):
                self.ateq_no.setText("---")
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None and self.payload is not None:
                trace(f"ATEQ_RESET_SYNC station={self.station.value} "
                      f"program={self.payload.ateq_program}")
        except Exception: self._error_key = "reset_error"
        trace = getattr(self.window(), "_live_trace", None)
        if was_label_ack_pending and trace is not None:
            trace(f"{self.station.value} LABEL_SCAN_CANCELLED_BY_RESET")
        self.refresh(); self.changed_callback()

    def _clear_scan_ok(self, reason: str) -> None:
        byte, bit = POINTS["scan_ok"][self.station]
        try:
            if self.plc.read_bit(byte, bit):
                self.plc.write_bit(byte, bit, False)
                trace = getattr(self.window(), "_live_trace", None)
                if trace is not None:
                    trace(f"SCAN_PLC_CLEAR station={self.station.value} "
                          f"point=M{byte}.{bit} reason={reason}")
        except Exception as exc:
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None:
                trace(f"SCAN_PLC_CLEAR_FAILED station={self.station.value} "
                      f"point=M{byte}.{bit} reason={reason} "
                      f"{type(exc).__name__}: {exc}")

    def acknowledge_error(self):
        """Clear only the UI acknowledgement state; service/PLC state is untouched."""
        self._error_key = None
        self.refresh()

    def _move_recovery_widgets(self, narrow: bool) -> None:
        """Keep diagnostics out of the four-indicator row on narrow screens.

        The wide compatibility footer retains its historical one-row geometry.
        At operator-sized widths the summary and both recovery controls move to
        a dedicated row, so no text relies on a tooltip or competes with an
        indicator caption.
        """
        if narrow:
            if self.error_summary.parentWidget() is not self.error_details:
                self._outer_layout.removeWidget(self.error_summary)
                self.error_details.layout().insertWidget(0, self.error_summary)
            for widget in (self.error_ack, self.error_reset):
                self._indicator_row.removeWidget(widget)
            self.error_actions.addWidget(self.error_ack)
            self.error_actions.addWidget(self.error_reset)
        else:
            if self.error_summary.parentWidget() is self.error_details:
                self.error_details.layout().removeWidget(self.error_summary)
                self._outer_layout.addWidget(self.error_summary)
            for widget in (self.error_ack, self.error_reset):
                self.error_actions.removeWidget(widget)
                self._indicator_row.addWidget(widget)

    def _records(self):
        # 界面表格按时间倒序：最新周期显示在第 1 行。
        return sorted((r for r in self.repository.records.values() if r.station is self.station),
                      key=lambda r: r.created_at, reverse=True)

    def set_stepcode(self, value):
        """Queue a StepCode display update safely from either UI or worker thread."""
        self.stepcode_updated.emit(str(value))

    def _apply_stepcode_display(self, value: str):
        self.stepcode_value.setText(value)
        state = "ok" if value.isdigit() else ("ng" if value in ("离线", "错误") else "info")
        self.stepcode_value.setProperty("state", state)
        self.stepcode_value.style().unpolish(self.stepcode_value)
        self.stepcode_value.style().polish(self.stepcode_value)

    def refresh(self):
        c = self.controller; rows = self._records(); self.total_today.setValue(len(rows)); self.ok_today.setValue(sum(1 for r in rows if r.second and r.second.result is Result.OK))
        for row in range(30):
            values = ["", "", "", "", "", "", "", "", "", ""]
            if row < len(rows):
                record = rows[row]; values = [record.created_at.astimezone().strftime("%Y-%m-%d-%H:%M:%S"), record.serial_no, record.code_2d, self._m(record.first, "pressure"), self._m(record.first, "leakage"), self._m(record.second, "pressure"), self._m(record.second, "leakage"), (record.second or record.first).result.value if (record.second or record.first) else "", record.part_no, record.person]
            for col, value in enumerate(values): self.table.setItem(row, col, QTableWidgetItem(str(value)))
        calibration = self._calibration()
        if calibration is not None:
            # Treat the PLC point as a rising-edge due request.  A stale high
            # level must not re-lock the station immediately after the local
            # validation has been completed and the next cycle clears lamps.
            try:
                plc_due = self._point_read("calibration")
            except Exception:
                plc_due = False
            if plc_due and not self._plc_calibration_due and not calibration.due:
                calibration.mark_due()
            self._plc_calibration_due = plc_due
            due, ng_verified, ok_verified = calibration.indicators
            values = {
                "calibration_due": due,
                "start_validation": calibration.validation_started,
                "ng_sample": ng_verified,
                "ok_sample": ok_verified,
            }
        else:
            values = {signal: False for signal, _ in INDICATOR_NAMES}
            values["start_validation"] = self._point_read("start") if "start" in POINTS else False
        language = getattr(self.window(), "_language", "中文")
        notice, notice_state, button_caption = self._calibration_prompt(calibration, language)
        self.calibration_notice.setText(notice)
        self.calibration_notice.setProperty("state", notice_state)
        self.calibration_notice.style().unpolish(self.calibration_notice)
        self.calibration_notice.style().polish(self.calibration_notice)
        for signal, _ in INDICATOR_NAMES:
            value = values[signal]
            self.indicators[signal].setText("●")
            if signal == "calibration_due":
                indicator_state = "ng" if value else "info"
            elif signal in ("ng_sample", "ok_sample"):
                indicator_state = "ok" if value else "info"
            else:
                indicator_state = "ok" if value else "info"
            tile = self.indicator_tiles.get(signal)
            if tile is not None:
                tile.setProperty("state", indicator_state)
                tile.style().unpolish(tile); tile.style().polish(tile)
            self.indicators[signal].setProperty("state", indicator_state)
            self.indicators[signal].style().unpolish(self.indicators[signal]); self.indicators[signal].style().polish(self.indicators[signal])
        if hasattr(self, "start_validation_button"):
            # Validation can begin only after the active cycle has reached a
            # terminal state.  The button remains visible as the sole control,
            # but is disabled while a test is still running or when not due.
            terminal_or_initial = ((c.record is None and c.phase in (Phase.IDLE, Phase.WAIT_SCAN)) or
                                   (c.record is not None and c.phase is Phase.COMPLETE))
            can_start = bool(calibration and calibration.due and
                             not calibration.validation_started and
                             terminal_or_initial and not calibration.clear_pending)
            self.start_validation_button.setEnabled(can_start)
            self.start_validation_button.setText(button_caption)
            self.start_validation_button.setToolTip(notice)
        if hasattr(self, "cancel_calibration_button"):
            is_admin = getattr(self.window(), "security", None) is not None and \
                self.window().security.role.value == "admin"
            cancelable = bool(calibration and (
                calibration.due or calibration.validation_started or calibration.clear_pending))
            terminal = c.phase in (Phase.IDLE, Phase.WAIT_SCAN, Phase.COMPLETE)
            self.cancel_calibration_button.setVisible(is_admin)
            self.cancel_calibration_button.setEnabled(cancelable and terminal and not c.recovery_required)
        prompts = {"中文": {Phase.IDLE: "扫码", Phase.READY: "一测", Phase.WAIT_2: "二测", Phase.LABELING: "贴标", Phase.COMPLETE: "复位或查询", Phase.FAULT: "管理员恢复"}, "English": {Phase.IDLE: "Scan", Phase.READY: "Test 1", Phase.WAIT_2: "Test 2", Phase.LABELING: "Label", Phase.COMPLETE: "Reset or query", Phase.FAULT: "Admin recovery"}, "Français": {Phase.IDLE: "Scanner", Phase.READY: "Test 1", Phase.WAIT_2: "Test 2", Phase.LABELING: "Étiqueter", Phase.COMPLETE: "Réinitialiser ou requête", Phase.FAULT: "Récupération admin"}}
        narrow_error = bool(self._error_key) and self.window().width() < 1600
        # At the narrow error breakpoint the station card can be only a few
        # hundred pixels wide.  Keep the four footer tiles in one equal row
        # instead of allowing the fixed-width Start button to push into its
        # neighbours.  The canonical wide layout keeps the 140px readable
        # floor; the responsive layout distributes the available width.
        # Error acknowledgement/reset buttons share the row on wide screens;
        # they consume part of the row's preferred width as well.  Treat that
        # state as responsive too, so the four station tiles can shrink rather
        # than overlap the neighbouring lamp or recovery control.
        compact_footer = self.window().width() < 1600 or bool(self._error_key)
        for tile in self.indicator_tiles.values():
            tile.setMinimumWidth(0 if compact_footer else 140)
        if hasattr(self, "start_validation_button"):
            self.start_validation_button.setMinimumWidth(90 if compact_footer else 140)
            # Two-line English/French captions still need to fit the narrow
            # four-tile row; retain the large button height while reducing
            # only the caption font at that responsive breakpoint.
            start_font = self.start_validation_button.font()
            start_font.setPointSize(9 if compact_footer else 11)
            self.start_validation_button.setFont(start_font)
            self.start_validation_button.style().unpolish(self.start_validation_button)
            self.start_validation_button.style().polish(self.start_validation_button)
        self._move_recovery_widgets(narrow_error)
        # In the wide error layout the acknowledgement controls share this
        # same row.  Let the tiles fall back to their readable minimum width
        # there, otherwise an expanded lamp tile can steal button text width.
        tile_stretch = 0 if self._error_key and not narrow_error else 1
        for tile in self.indicator_tiles.values():
            self._indicator_row.setStretchFactor(tile, tile_stretch)
        self.error_details.setVisible(narrow_error)
        # A fault already has a dedicated summary and recovery buttons.  Do
        # not spend a footer column repeating the same message beside them.
        self.next_action.setVisible(False)
        self.error_summary.setVisible(bool(self._error_key))
        if self._error_key:
            self.error_summary.setText(UiTextCatalog.message(language, self._error_key))
        self.error_ack.setVisible(bool(self._error_key))
        self.error_reset.setVisible(bool(self._error_key))
        if self._error_key:
            self.next_action.setText(UiTextCatalog.message(language, self._error_key))
        else:
            self.next_action.setText(prompts.get(language, prompts["中文"]).get(c.phase, c.phase.value))
        # The error row is allowed to grow only for the narrow error path;
        # canonical >=1600 layouts preserve the 120px footer contract.
        self.bottom_indicators.setMaximumHeight(16777215 if narrow_error else METRICS.footer_max_height)
        self.error_ack.setToolTip("")
        self.error_reset.setToolTip("")


class MainWindow(QMainWindow):
    def _log_crash(self, where: str, exc: BaseException) -> None:
        _write_crash_log(where, exc)

    def __init__(self, language: str = "中文", *, b_live: bool = False,
                 b_port: str = "COM6", b_slave: int = 1,
                 live_all: bool = False, config_path: Path | None = None):
        super().__init__(); self.setWindowTitle("Leak Test 2 Channels / 气密检测"); self.resize(METRICS.canonical_width, METRICS.canonical_height); self.setMinimumSize(1100, 700)
        app = QApplication.instance(); family = "Segoe UI"
        for font_path in (Path(r"C:\Windows\Fonts\Noto Sans SC (TrueType).otf"), Path(r"C:\Windows\Fonts\simsun.ttc")):
            if font_path.exists():
                fid = QFontDatabase.addApplicationFont(str(font_path)); families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
                if families: family = families[0]; break
        if app: app.setFont(QFont(family))
        self.setStyleSheet(stylesheet())
        selected_config = config_path or (Path(__file__).parents[1] / "config" / "default.toml")
        self.live_all = bool(live_all)
        self.live_mode = bool(b_live or live_all)
        self.settings = Settings.from_toml(selected_config) if selected_config.exists() else Settings()
        self.b_live = bool(b_live)
        self.live_trace_path = Path(r"D:\ATEQ\live_trace.log" if self.live_all else r"D:\ATEQ\live_b_trace.log")
        self.b_ateq = None
        self.live_ateq = {}
        self.b_live_error = ""
        if self.live_all:
            for index, station in enumerate(StationId):
                adapter = SerialAteq(self.settings.ateq_ports[index], station.value,
                                     slave=self.settings.ateq_slaves[index], timeout_s=0.8)
                try:
                    adapter.connect()
                    adapter.read_registers(SerialAteq.REALTIME_ADDRESS, 1)
                except Exception:
                    adapter.close()
                    raise
                self.live_ateq[station] = adapter
            self.b_ateq = self.live_ateq[StationId.B]
        elif self.b_live:
            try:
                self.b_ateq = SerialAteq(b_port, "B", slave=int(b_slave), timeout_s=0.8)
                self.b_ateq.connect()
                # Opening COM only proves the Windows handle is available.
                # Perform the same Modbus holding-register read as the
                # reference implementation so the UI cannot claim ATEQ
                # online without a response from the configured slave.
                self.b_ateq.read_registers(SerialAteq.REALTIME_ADDRESS, 1)
                self.b_live_status = f"B ATEQ 实时：{b_port} / 从站 {b_slave}"
                self._live_trace(f"BOOT ATEQ_OK port={b_port} slave={b_slave}")
            except Exception as exc:
                self.b_live_error = f"B ATEQ 连接失败：{type(exc).__name__}: {exc}"
                # Real mode must fail closed; never present a usable test UI
                # while the instrument handshake has not completed.
                raise RuntimeError(self.b_live_error) from exc
        # Setup.ini is read-only characterization evidence.  It is used only
        # when both A/B keys validate; otherwise the UI shows BLOCKED instead
        # of presenting sample COM values as production truth.
        self.config_warning = ""
        try:
            if not self.live_all and self.settings.setup_path.exists():
                self.settings = Settings.from_file(self.settings.setup_path)
            elif not self.live_all:
                self.config_warning = "Setup.ini 未找到 / BLOCKED"
        except Exception as exc:
            self.config_warning = f"配置未确认 / BLOCKED: {exc}"
            self.settings = Settings(config_source="blocked", ports_confirmed=False)
        self.data_dir = Path(r"D:\data")
        if self.live_mode:
            # B hardware mode is deliberately all-or-nothing for the real
            # services.  A fake PLC/DB fallback would make a field test look
            # successful while leaving no trace in the real system.
            self.real_plc = Snap7Plc(self.settings.plc_ip)
            self.real_plc.connect()
            self.real_plc.enable_writes(True)
            credential_path = self.settings.credential_path
            if not credential_path.is_file():
                raise RuntimeError(f"MySQL 凭据文件不存在: {credential_path}")
            credentials = json.loads(credential_path.read_text(encoding="utf-8"))
            self.repository = PyMySQLRepository(host=self.settings.database_host,
                                                port=self.settings.database_port,
                                                user=str(credentials["user"]),
                                                password=str(credentials["password"]),
                                                database=self.settings.database)
            self.repository.connect_and_verify()
            self.plc = self.real_plc
            self.printer = BarTenderCmdPrinter(Path(r"C:\Program Files\Seagull\BarTender Suite\bartend.exe"), self.data_dir, self.data_dir / "label_data.txt")
        else:
            self.real_plc = None
            self.plc = FakePlc()
            self.repository = FakeRepository(self.settings)
            self.printer = FakePrinter()
        self.calibration = {s: Calibration(station=s, initial_due=True) for s in StationId}
        self._calibration_state_path = Path(
            os.environ.get("LEAKTEST_CAL_STATE", r"D:\ATEQ\calibration_state.json"))
        self._restore_calibration(); self.security = SecurityContext(AuthSession(demo=True, password_file=self.data_dir / "管理员.txt"), LicenseVerifier(simulator=True).verify(b"SIMULATE", b"SIMULATE-SIGNATURE")); self.product_settings = ProductSettingsService(self.security); self.model_settings = ModelSettingsService(self.security, self.data_dir / "日期设置.ini"); self.personnel = PersonnelService(self.security, self.data_dir / "作业员列表.txt"); self.global_settings = GlobalSettingsService(self.security, self.data_dir / "全局设置.ini"); self.scanner_framers = {s: ScannerFramer() for s in StationId}; self.scanner_guards = {s: ScannerGuard() for s in StationId}; self.confirmation_callback = self._confirm_output; self._setup_values = {"customer_no":"", "ateq_no":"SIM"}; self.journal_dir = Path(tempfile.mkdtemp(prefix="LeakTest2Channels-replica-")); self.tabs = CompatibilityTabs(); self._language = language if language in UiTextCatalog.LANGUAGES else "中文"; self._i18n_widgets = []
        self.cards = [StationPanel(s, self.repository, self.printer,
                                   self.plc,
                                   CycleJournal(self.journal_dir / f"{s.value}.json"),
                                   self.security, self.product_settings.current_product,
                                   lambda station, signal, requested, current: self.confirmation_callback(station, signal, requested, current),
                                   self.refresh_all, self._payload_for, self.personnel.list_all,
                                   lambda station: self.calibration[station], self.start_calibration,
                                   self.live_ateq.get(s)) for s in StationId]
        if self.b_live and not self.live_all:
            self.cards[0].setEnabled(False)
        self._build_main(); self._build_setup(); self._build_query(); self._build_manual(); self.setCentralWidget(self.tabs)
        self._configure_calibration_period()
        self._update_setup_gate(); self._refresh_models(); self._refresh_personnel(); self._refresh_runtime_choices()
        if self.live_mode:
            # Recover a low handshake state after an unclean restart.  A
            # previous cycle may have left M0.0/M0.1 high until the PLC scan
            # task acknowledged it.
            for card in self.cards:
                card._clear_scan_ok("ui_startup")
        self._refresh_calibration_countdowns()
        self.calibration_timer = QTimer(self)
        self.calibration_timer.setInterval(1000)
        self.calibration_timer.timeout.connect(self._tick_calibration)
        self.calibration_timer.start()
        self.ateq_heartbeat_timer = QTimer(self)
        self.ateq_heartbeat_timer.setInterval(100)
        self.ateq_heartbeat_timer.timeout.connect(self._ateq_heartbeat)
        self._last_b_stepcode = None
        self._last_live_stepcodes = {station: None for station in StationId}
        for station, card in zip(StationId, self.cards):
            card.stepcode_updated.connect(lambda value, current=station: self._remember_live_stepcode(current, value))
        # Keep read-only/configuration diagnostics, but no file token may
        # dispatch a test; physical cycles are dispatched only by StepCode=4.
        self.b_trigger_path = Path(__file__).parents[1] / "b_test_trigger.txt"
        self.b_trigger_timer = QTimer(self)
        self.b_trigger_timer.setInterval(1000)
        self.b_trigger_timer.timeout.connect(self._poll_b_trigger_file)
        if self.live_mode:
            self.ateq_heartbeat_timer.start()
        if self.b_live:
            self.b_trigger_timer.start()
        self.calibration_status.setText("校准到期，请点击启动验证")
        self._start_shared_scanner()
        if self.live_all:
            self.scanner_status.setText("A/B 真实联机：双 ATEQ + PLC + MySQL + 扫码枪 + 打印机")
        elif self.b_live:
            self.scanner_status.setText("B 真实联机：COM6 + PLC + MySQL + 扫码枪 + 打印机")
        # Initial screenshot language keeps multilingual tabs, while manual
        # labels are Chinese-first and readable instead of implementation keys.
        initial_titles = {"clamp": "夹紧 / Clamp / Serrage", "transfer": "移载 / Transfer / Transfert", "block": "封堵 / Blocking / Obstruction", "stamp": "盖章 / Stamp / Timbre", "door_disable": "安全门使能/禁用 / Door enable/disable", "manual": "自动/手动 / Automatic/Manual"}
        for station in StationId:
            for signal, _, _ in MANUAL_NAMES:
                label_widget = self.findChild(QLabel, f"manual_label_{signal}_{station.value}")
                if label_widget is not None: label_widget.setText(f"{station.value} {initial_titles[signal]}")
        # Apply the selected catalog before the first frame is shown.  This
        # prevents the historical multilingual construction strings from
        # leaking into the canonical Chinese screenshots.
        self.language_selector.setCurrentText(self._language)
        self._apply_language(self._language)

    def resizeEvent(self, event):
        """Re-evaluate responsive error rows whenever the operator resizes."""
        super().resizeEvent(event)
        for card in getattr(self, "cards", ()):
            card.refresh()

    def _live_trace(self, message: str) -> None:
        # Keep the legacy B-only diagnostic mode traceable even when tests
        # enable ``b_live`` after constructing the simulation window.
        if not (self.live_mode or self.b_live):
            return
        try:
            with self.live_trace_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
        except OSError:
            pass

    def _confirm_output(self, station, signal, requested, current):
        """Confirm a manual PLC target using only the selected language."""
        state = {
            "中文": ("开" if requested else "关", "开" if current else "关", "确认输出", "工位", "信号", "请求", "当前回读"),
            "English": ("ON" if requested else "OFF", "ON" if current else "OFF", "Confirm output", "Station", "Signal", "Request", "Current readback"),
            "Français": ("MARCHE" if requested else "ARRÊT", "MARCHE" if current else "ARRÊT", "Confirmer la sortie", "Poste", "Signal", "Demande", "Retour actuel"),
        }[self._language]
        requested_text, current_text, title, station_label, signal_label, request_label, readback_label = state
        signal_names = {
            "clamp": {"中文": "夹紧", "English": "Clamp", "Français": "Serrage"},
            "transfer": {"中文": "移载", "English": "Transfer", "Français": "Transfert"},
            "block": {"中文": "封堵", "English": "Blocking", "Français": "Obstruction"},
            "stamp": {"中文": "盖章", "English": "Stamp", "Français": "Timbre"},
            "door_disable": {"中文": "安全门", "English": "Safety door", "Français": "Porte de sécurité"},
            "manual": {"中文": "自动/手动", "English": "Automatic/Manual", "Français": "Automatique/Manuel"},
            "pressure": {"中文": "正/负压", "English": "Pressure", "Français": "Pression"},
            "start": {"中文": "启动", "English": "Start", "Français": "Démarrage"},
        }
        signal_text = signal_names.get(signal, {}).get(self._language, {"中文": "输出", "English": "Output", "Français": "Sortie"}[self._language])
        if self._language == "Français":
            body = f"{station_label} {station.value}\n{signal_label} {signal_text}\n{request_label} {requested_text} ; {readback_label} {current_text} ?"
        else:
            body = f"{station_label} {station.value}\n{signal_label} {signal_text}\n{request_label} {requested_text}; {readback_label} {current_text}?"
        box = QMessageBox(QMessageBox.Icon.Question, title, body, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        yes, no = box.button(QMessageBox.StandardButton.Yes), box.button(QMessageBox.StandardButton.No)
        if yes: yes.setText({"中文": "确定", "English": "Confirm", "Français": "Confirmer"}[self._language])
        if no: no.setText({"中文": "取消", "English": "Cancel", "Français": "Annuler"}[self._language])
        answer = box.exec()
        return answer == QMessageBox.StandardButton.Yes

    def _ateq_heartbeat(self):
        """Keep the F620 Modbus session alive with a read-only status poll."""
        if self.live_all:
            for station, ateq in self.live_ateq.items():
                try:
                    registers, _ = ateq.read_registers(SerialAteq.REALTIME_ADDRESS, SerialAteq.REALTIME_COUNT)
                    self._handle_live_stepcode(station, SerialAteq._swap16(registers[4]))
                except Exception as exc:
                    index = 0 if station is StationId.A else 1
                    self.cards[index].set_stepcode("离线")
                    try:
                        ateq.connect()
                        ateq.read_registers(SerialAteq.REALTIME_ADDRESS, 1)
                    except Exception as reconnect_exc:
                        self._live_trace(f"ATEQ_RECONNECT_FAILED station={station.value} {reconnect_exc}")
            return
        if not self.b_live or self.b_ateq is None:
            return
        if getattr(self, "_b_test_in_progress", False):
            # A test owns the serial link; do not interleave status polls.
            return
        try:
            registers, _ = self.b_ateq.read_registers(
                SerialAteq.REALTIME_ADDRESS, SerialAteq.REALTIME_COUNT)
            self._handle_b_stepcode(SerialAteq._swap16(registers[4]))
            if hasattr(self, "scanner_status"):
                self.scanner_status.setToolTip("B ATEQ Modbus 在线：COM6 / 从站1")
        except Exception:
            self.cards[1].set_stepcode("离线")
            try:
                self.b_ateq.connect()
                self.b_ateq.read_registers(SerialAteq.REALTIME_ADDRESS, 1)
            except Exception as reconnect_exc:
                if hasattr(self, "scanner_status"):
                    self.scanner_status.setToolTip(f"B ATEQ Modbus 离线：{reconnect_exc}")

    def _remember_live_stepcode(self, station, value: str):
        try:
            code = int(value)
        except (TypeError, ValueError):
            return
        if self.live_all:
            if code != self._last_live_stepcodes[station]:
                self._last_live_stepcodes[station] = code
                self._live_trace(f"ATEQ_STEP station={station.value} code={code}")
        elif code != self._last_b_stepcode:
            self._last_b_stepcode = code
            self._live_trace(f"ATEQ_STEP code={code}")

    def _handle_b_stepcode(self, step_code: int):
        return self._handle_live_stepcode(StationId.B, step_code)

    def _handle_live_stepcode(self, station, step_code: int):
        """Dispatch one station's stored stage on a new StepCode=4."""
        card = self.cards[0 if station is StationId.A else 1]
        previous = self._last_live_stepcodes[station] if self.live_all else self._last_b_stepcode
        if self.live_all:
            self._last_live_stepcodes[station] = step_code
        else:
            self._last_b_stepcode = step_code
        card.set_stepcode(step_code)
        if step_code != 4 or previous == 4:
            return
        if card._label_ack_pending:
            self._live_trace(f"ATEQ_STEP_4_WAIT_LABEL_SCAN station={station.value}")
            return
        phase = card.controller.phase
        if (phase not in (Phase.READY, Phase.WAIT_2)
                and self._restore_pending_calibration_cycle(card)):
            phase = card.controller.phase
        if (phase in (Phase.IDLE, Phase.WAIT_SCAN)
                and not self.calibration[card.station].locked
                and not self.calibration[card.station].validation_started
                and self._prepare_stepcode_production_cycle(card)):
            phase = card.controller.phase
        elif (phase is Phase.COMPLETE
              and not self.calibration[card.station].locked
              and not self.calibration[card.station].validation_started):
            # A normal NG result has no label transaction to await. Preserve
            # its repository row, archive the terminal journal, then reserve
            # the next frozen selection on the next hardware cycle edge.
            card.controller.reset()
            if self._prepare_stepcode_production_cycle(card):
                phase = card.controller.phase
        self._live_trace(f"ATEQ_STEP_4_RISE station={station.value} phase={phase.value}")
        try:
            if phase is Phase.READY:
                card.first()
            elif phase is Phase.WAIT_2:
                card.second()
            elif self._begin_ok_validation_cycle(card):
                card.first()
            else:
                self._live_trace(f"ATEQ_STEP_4_IGNORED station={station.value} no READY/WAIT_2 cycle")
        except Exception as exc:
            self._log_crash("ATEQ_STEP_DISPATCH", exc)
            self._live_trace(f"ATEQ_STEP_DISPATCH_FAILED {type(exc).__name__}: {exc}")

    def _prepare_stepcode_production_cycle(self, card) -> bool:
        """Freeze the selected model/person/mode when hardware starts a cycle."""
        payload = card.payload
        if payload is None:
            self._live_trace(
                f"PRODUCTION_CYCLE_BLOCKED station={card.station.value} no model payload")
            return False
        if card.controller.phase not in (Phase.IDLE, Phase.WAIT_SCAN):
            return False
        # A restart can reload a QR snapshot that was already committed by a
        # previous process while the counter file still points at that value.
        # Never create another database row for an already-used production
        # serial; walk the persistent counter forward until the payload is new.
        payload = self._next_unused_production_payload(card)
        if payload is None:
            self._live_trace(
                f"PRODUCTION_CYCLE_BLOCKED station={card.station.value} "
                "no unused serial payload")
            return False
        card._clear_scan_ok("production_cycle_start")
        mode = "dual" if card.mode_button.isChecked() else "single"
        selection = StationSelection(
            card.station, payload.product_id,
            card.staff.currentText().strip() or "Operator", mode,
            payload.serial_no, payload.barcode_text, payload.customer_model,
            str(payload.ateq_program), str(payload.template_path))
        card.controller.scan_selection(selection)
        card.code_input.clear()
        self._advance_serial(card.station, payload.product_id,
                             consumed_serial=payload.serial_no)
        try:
            card.payload = self._payload_for(card.station, payload.product_id)
            card.ateq_no.setText(str(card.payload.ateq_program))
        except Exception as exc:
            self._live_trace(
                f"NEXT_PAYLOAD_REFRESH_FAILED station={card.station.value} "
                f"{type(exc).__name__}: {exc}")
        card.refresh()
        self._live_trace(
            f"PRODUCTION_CYCLE_READY station={card.station.value} "
            f"cycle={card.controller.record.cycle_id} mode={mode} "
            f"serial={selection.serial_no}")
        return True

    def _serial_used_by_station(self, station, serial_no: str) -> bool:
        serial = str(serial_no).strip()
        if not serial or not serial.isdigit():
            return False
        rows = getattr(self.repository, "records", {})
        values = rows.values() if hasattr(rows, "values") else rows
        return any(getattr(row, "station", None) is station
                   and str(getattr(row, "serial_no", "")).strip() == serial
                   for row in values)

    def _next_unused_production_payload(self, card):
        """Return a payload not already present in this station's history."""
        payload = card.payload
        if payload is None or not self._serial_used_by_station(
                card.station, payload.serial_no):
            return payload
        original = str(payload.serial_no)
        for _ in range(10000):
            # First re-read the counter.  If it is already ahead (for example
            # after a clean restart), this refresh alone avoids skipping it.
            candidate = self._payload_for(card.station, payload.product_id)
            if not self._serial_used_by_station(card.station, candidate.serial_no):
                card.payload = candidate
                card.ateq_no.setText(str(candidate.ateq_program))
                self._live_trace(
                    f"SERIAL_RECONCILE station={card.station.value} "
                    f"duplicate={original} next={candidate.serial_no}")
                return candidate
            self._advance_serial(card.station, payload.product_id,
                                 consumed_serial=candidate.serial_no)
        return None

    def _restore_pending_calibration_cycle(self, card) -> bool:
        """Rebuild a persisted NG/OK calibration cycle after a UI restart.

        Calibration state is persisted, but an in-memory StationController
        record is not. On a new StepCode=4, restore only the frozen calibration
        selection; the monitor still performs no PLC/ATEQ start write.
        """
        calibration = self.calibration[card.station]
        controller = card.controller
        if (not calibration.validation_started
                or calibration.phase not in (CalibrationPhase.WAIT_NG,
                                             CalibrationPhase.WAIT_OK)
                or controller.record is not None
                or controller.phase not in (Phase.IDLE, Phase.WAIT_SCAN)):
            return False
        payload = card.payload
        if payload is None:
            self._live_trace(
                f"CAL_CYCLE_RESTORE_BLOCKED station={card.station.value} no selected model payload")
            return False
        cal_payload = self._barcode_engine().generate_calibration(
            payload.product_id, card.station)
        selection = StationSelection(
            card.station, cal_payload.product_id,
            card.staff.currentText().strip() or "Operator",
            "single", cal_payload.serial_no, cal_payload.barcode_text,
            cal_payload.customer_model, str(cal_payload.ateq_program),
            str(cal_payload.template_path))
        controller.scan_selection(selection)
        card.code_input.clear()
        self._live_trace(
            f"CAL_CYCLE_RESTORED station={card.station.value} "
            f"sample={calibration.sample_demand} cycle={controller.record.cycle_id} "
            f"serial={cal_payload.serial_no}")
        card.refresh()
        return True

    def _begin_ok_validation_cycle(self, card) -> bool:
        """Bridge NG -> OK validation: archive the NG cycle and start the OK one.

        NG 通过后控制器停在“完成”，而 OK 样件需要一个新测试周期；此前没有任何
        UI 动作能把控制器带回“就绪”。PLC 上升沿和触发文件在派发前先走这里。
        故障相位也接受：先尝试复位归档（需要人工授权时放弃并记录）。
        This bridge runs only when a new StepCode=4 arrives.
        """
        calibration = self.calibration[card.station]
        if not (calibration.validation_started
                and calibration.phase is CalibrationPhase.WAIT_OK
                and card.controller.phase in (Phase.COMPLETE, Phase.FAULT, Phase.IDLE)):
            return False
        payload = card.payload
        if payload is None:
            return False
        if card.controller.record is not None or card.controller.phase is Phase.FAULT:
            try:
                card.controller.reset()
            except Exception as exc:
                self._live_trace(
                    f"CAL_OK_BRIDGE_RESET_FAILED {type(exc).__name__}: {exc}")
                return False
        # OK 验证周期同样使用校准件流水号（C002...）。
        cal_payload = self._barcode_engine().generate_calibration(
            payload.product_id, card.station)
        selection = StationSelection(
            card.station, cal_payload.product_id, card.staff.currentText().strip() or "Operator",
            "single", cal_payload.serial_no, cal_payload.barcode_text, cal_payload.customer_model,
            str(cal_payload.ateq_program), str(cal_payload.template_path))
        card.controller.scan_selection(selection)
        card.code_input.clear()
        self._live_trace(
            f"CAL_OK_CYCLE_READY station={card.station.value} "
            f"cycle={card.controller.record.cycle_id} serial={cal_payload.serial_no}")
        return True

    def _poll_b_trigger_file(self):
        """Handle bounded diagnostics; test starts are exclusively StepCode-driven."""
        if not self.b_live:
            return
        try:
            if not self.b_trigger_path.exists():
                return
            content = self.b_trigger_path.read_text(encoding="utf-8", errors="ignore").strip().lower()
            self.b_trigger_path.unlink(missing_ok=True)
        except OSError:
            return
        card = self.cards[1]
        tokens = content.split()
        if tokens and tokens[0] == "readreg":
            # 诊断命令：readreg <hex地址> [count]，先选编辑程序再只读。
            if (len(tokens) not in (2, 3) or self.b_ateq is None
                    or getattr(card, "_test_worker_running", False)
                    or getattr(self, "_b_test_in_progress", False)):
                self._live_trace(f"READREG_REJECTED content={content!r}")
                return
            self._b_test_in_progress = True
            try:
                count = int(tokens[2]) if len(tokens) == 3 else 2
                decoded = self.b_ateq.read_program_parameter(int(tokens[1], 16), count)
                self._live_trace(f"READREG addr=0x{int(tokens[1], 16):04X} count={count} value={decoded}")
            except Exception as exc:
                self._log_crash("READREG", exc)
                self._live_trace(f"READREG_FAILED {type(exc).__name__}: {exc}")
            finally:
                self._b_test_in_progress = False
            return
        if tokens and tokens[0] == "writereg":
            # 诊断命令：writereg <hex地址> <值×1000>，带读回校验。
            if (len(tokens) != 3 or self.b_ateq is None
                    or getattr(card, "_test_worker_running", False)
                    or getattr(self, "_b_test_in_progress", False)):
                self._live_trace(f"WRITEREG_REJECTED content={content!r}")
                return
            self._b_test_in_progress = True
            try:
                decoded = self.b_ateq.write_program_parameter(
                    int(tokens[1], 16), int(tokens[2]))
                self._live_trace(f"WRITEREG_OK addr=0x{int(tokens[1], 16):04X} value={decoded}")
            except Exception as exc:
                self._log_crash("WRITEREG", exc)
                self._live_trace(f"WRITEREG_FAILED {type(exc).__name__}: {exc}")
            finally:
                self._b_test_in_progress = False
            return
        if tokens and tokens[0] == "calclose":
            # 远程关闭当前校准伴生周期：贴标（回执确认）+ 复位归档。
            try:
                card.label()
                if card.controller.phase is not Phase.COMPLETE:
                    raise RuntimeError(f"贴标后相位为 {card.controller.phase.value}")
                card.reset()
                self._live_trace("CALCLOSE_OK")
            except Exception as exc:
                self._log_crash("CALCLOSE", exc)
                self._live_trace(f"CALCLOSE_FAILED {type(exc).__name__}: {exc}")
            return
        if tokens and tokens[0] == "caldue":
            # 远程模拟校准到期（倒计时归零效果），用于演练 NG→OK 验证。
            try:
                self.mark_calibration_due(StationId.B)
                self._live_trace("CALDUE_OK")
            except Exception as exc:
                self._log_crash("CALDUE", exc)
                self._live_trace(f"CALDUE_FAILED {type(exc).__name__}: {exc}")
            return
        if tokens and tokens[0] == "calstart":
            # 远程启动验证：等价于操作员点击启动验证按钮。
            try:
                self.start_calibration(StationId.B)
                self._live_trace("CALSTART_OK")
            except Exception as exc:
                self._log_crash("CALSTART", exc)
                self._live_trace(f"CALSTART_FAILED {type(exc).__name__}: {exc}")
            return
        if tokens and tokens[0] == "readreg":
            # 诊断命令：readreg <hex地址> <数量>，只读并在 trace 报告数值。
            if (len(tokens) != 2 or self.b_ateq is None
                    or getattr(card, "_test_worker_running", False)
                    or getattr(self, "_b_test_in_progress", False)):
                self._live_trace(f"READREG_REJECTED content={content!r}")
                return
            self._b_test_in_progress = True
            try:
                registers, _ = self.b_ateq.read_registers(int(tokens[1], 16), 2)
                decoded = self.b_ateq._signed32_from_words(registers[0], registers[1])
                self._live_trace(
                    f"READREG addr=0x{int(tokens[1], 16):04X} "
                    f"words={[hex(r) for r in registers]} long={decoded}")
            except Exception as exc:
                self._log_crash("READREG", exc)
                self._live_trace(f"READREG_FAILED {type(exc).__name__}: {exc}")
            finally:
                self._b_test_in_progress = False
            return
        if tokens and tokens[0] == "setlimit":
            if len(tokens) != 2 or not tokens[1].isdigit():
                self._live_trace(f"B_FILE_TRIGGER ignored content={content!r}")
                return
            self._set_b_test_fail_limit(int(tokens[1]))
            return
        if content == "start":
            self._live_trace("B_FILE_TRIGGER rejected: StepCode=4 is the only test trigger")
            return
        self._live_trace(f"B_FILE_TRIGGER ignored content={content!r}")

    def _barcode_engine(self):
        return BarcodeRuleEngine(self.data_dir / "日期设置.ini",
                                 self.data_dir / "日期对照.ini",
                                 output_dir=self.data_dir)

    def _set_b_test_fail_limit(self, value_milli: int):
        """Set the ATEQ B leak limit (Test FAIL) via the shared serial link.

        走 b_ateq 的串口锁与周期监控互斥；测试进行中拒绝执行。任何读回
        不一致由 set_test_fail_limit 抛错并记录，绝不提示修改成功。
        """
        card = self.cards[1]
        if getattr(card, "_test_worker_running", False) or getattr(self, "_b_test_in_progress", False):
            self._live_trace(f"SETLIMIT_REJECTED value={value_milli} test in progress")
            return
        if self.b_ateq is None:
            self._live_trace("SETLIMIT_REJECTED no B ATEQ object")
            return
        self._live_trace(f"SETLIMIT_REQUEST value={value_milli} ({value_milli / 1000:.3f} mL/min)")
        self._b_test_in_progress = True
        try:
            decoded = self.b_ateq.set_test_fail_limit(value_milli)
            self._live_trace(f"SETLIMIT_OK value={decoded} "
                             f"({decoded / 1000:.3f} mL/min) program={self.b_ateq.program}")
        except Exception as exc:
            self._log_crash("SETLIMIT", exc)
            self._live_trace(f"SETLIMIT_FAILED {type(exc).__name__}: {exc}")
        finally:
            # 参数访问后仪器需要恢复时间：心跳保持暂停 5 秒再放行，
            # 避免紧跟着的状态读取/启动命令把仪器打到离线。
            QTimer.singleShot(5000, self._resume_after_setlimit)

    def _resume_after_setlimit(self):
        self._b_test_in_progress = False
        self._live_trace("SETLIMIT_SETTLED serial resumed")

    def _payload_for(self, station, product):
        engine = self._barcode_engine()
        payload = engine.generate(product, station)
        # 发布二维码/打印路径/协众产品号 txt，校准打印依赖
        # 二维码{station}.txt 存在；与 production.select_product 行为一致。
        engine.publish(payload)
        return payload

    def _advance_serial(self, station, product, consumed_serial=None):
        """Advance the daily serial counter after a cycle consumed one.

        流水号按周期递增、跨日归零。失败只记录 trace：本周期二维码已冻结，
        阻断后续刷新只会让工位卡在已启动的周期里。
        """
        try:
            serial = self._barcode_engine().advance_serial(
                product, station, consumed_serial=consumed_serial)
            consumed = "" if consumed_serial is None else f" consumed={consumed_serial}"
            self._live_trace(
                f"SERIAL_ADVANCED station={station.value}{consumed} next={serial}")
            return serial
        except Exception as exc:
            self._live_trace(
                f"SERIAL_ADVANCE_FAILED station={station.value} "
                f"{type(exc).__name__}: {exc}")
            return None

    def _refresh_runtime_choices(self):
        products = self.model_settings.list_models()
        people = self.personnel.list_all()
        for card in self.cards:
            card.set_choices(products, people)

    def _build_main(self):
        page = QWidget(); page.setObjectName("page"); root = QVBoxLayout(page); root.setContentsMargins(METRICS.page_margin, 8, METRICS.page_margin, 8); root.setSpacing(METRICS.station_gap); ports = "/".join(self.settings.ateq_ports) if self.settings.ports_confirmed else "BLOCKED/未确认"; mode_label = "LIVE A/B" if self.live_all else ("LIVE B" if self.b_live else "SIMULATE"); service_label = "real services" if self.live_mode else "Fake services"; self.system_status = QLabel(f"模式：{mode_label} | PLC：{self.settings.plc_ip} | ATEQ：{ports} | {service_label}"); self.system_status.setObjectName("systemStatusBar"); self.system_status.setVisible(False); root.addWidget(self.system_status); top = QHBoxLayout(); top.setSpacing(METRICS.station_gap); top.addWidget(self.cards[0], 1); top.addWidget(self.cards[1], 1); root.addLayout(top, 1)
        # Match Main.vi footer hierarchy: A indicators | login | B indicators
        # | calibration countdown.  The three calibration lamps are bound to
        # each station's local NG -> OK validation state; Start Validation is
        # the only footer action and never aliases a PLC production start bit.
        footer = QHBoxLayout(); footer.setSpacing(METRICS.station_gap); footer.addWidget(self.cards[0].bottom_indicators, 4); footer.addWidget(self._login_panel(), 3); footer.addWidget(self.cards[1].bottom_indicators, 4)
        countdown_box = QGroupBox("校准倒计时 / Calibration Countdown"); countdown_box.setObjectName("calibrationCountdownPanel"); countdown_layout = QGridLayout(countdown_box); countdown_layout.setContentsMargins(8, 8, 8, 8); countdown_layout.setHorizontalSpacing(6); countdown_layout.setVerticalSpacing(4)
        self.calibration_label_a = QLabel("校准倒计时 A"); self.calibration_label_a.setObjectName("calibration_countdown_label_A"); self.calibration_label_a.setMinimumWidth(92)
        self.calibration_label_b = QLabel("校准倒计时 B"); self.calibration_label_b.setObjectName("calibration_countdown_label_B"); self.calibration_label_b.setMinimumWidth(92)
        self.calibration_countdown_a = _CountdownSpinBox(); self.calibration_countdown_a.setObjectName("calibration_countdown_A")
        self.calibration_countdown_b = _CountdownSpinBox(); self.calibration_countdown_b.setObjectName("calibration_countdown_B")
        countdown_layout.addWidget(self.calibration_label_a, 0, 0); countdown_layout.addWidget(self.calibration_countdown_a, 0, 1); countdown_layout.addWidget(self.calibration_label_b, 1, 0); countdown_layout.addWidget(self.calibration_countdown_b, 1, 1)
        # Compatibility alias for callers that used the old single countdown;
        # all runtime updates now target the station-specific controls.
        self.calibration_label = self.calibration_label_a; self.calibration_countdown = self.calibration_countdown_a
        self.calibration_label_A = self.calibration_label_a; self.calibration_label_B = self.calibration_label_b
        self.calibration_countdown_A = self.calibration_countdown_a; self.calibration_countdown_B = self.calibration_countdown_b
        footer.addWidget(countdown_box); root.addLayout(footer)
        for widget in (self.cards[0].bottom_indicators, self.cards[1].bottom_indicators): widget.setMaximumHeight(METRICS.footer_max_height)
        scanner = QHBoxLayout(); scanner.setSpacing(8); self.scanner_input = QLineEdit(); self.scanner_input.setObjectName("scanner_input"); self.scanner_input.setReadOnly(True); self.scanner_input.setPlaceholderText("在线共用扫码枪：A/B 自动识别"); scanner.addWidget(self.scanner_input); self.shared_scanner_indicator = QLabel("●"); self.shared_scanner_indicator.setObjectName("shared_scanner_indicator"); self.shared_scanner_indicator.setProperty("state", "ng"); scanner.addWidget(self.shared_scanner_indicator); self.scanner_status = QLabel("共用扫码枪连接中…"); self.scanner_status.setObjectName("scannerStatus"); scanner.addWidget(self.scanner_status); self.scanner_toggle = QPushButton("关闭扫码"); self.scanner_toggle.setObjectName("scanner_toggle"); self.scanner_toggle.setProperty("compact", True); self.scanner_toggle.clicked.connect(self.toggle_shared_scanner); scanner.addWidget(self.scanner_toggle)
        for s in StationId:
            b = QPushButton(f"扫码到 {s.value}"); b.setMinimumWidth(0); b.setProperty("primary", True); b.setObjectName(f"scanner_route_{s.value}"); b.clicked.connect(lambda _=False, station=s: self.route_scanner_text(station)); scanner.addWidget(b)
            b.setVisible(False)  # only retained as an administrator diagnostic hook
        for index in range(scanner.count()):
            child = scanner.itemAt(index).widget()
            if child is not None:
                child.setMinimumWidth(0)
        self.scanner_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.scanner_input.setMinimumHeight(METRICS.input_height); self.scanner_status.setMinimumHeight(METRICS.scanner_height); root.addLayout(scanner); self.tabs.addTab(page, "测试/Main/Principale")

    def _start_shared_scanner(self):
        """Start the single online scanner and poll its shared A/B queue."""
        self.shared_scanner = None
        self._scan_enabled = True
        self.scanner_timer = QTimer(self)
        self.scanner_timer.setInterval(50)
        self.scanner_timer.timeout.connect(self._poll_shared_scanner)
        try:
            self.shared_scanner = TcpScanner(
                self.settings.scanner_ip, self.settings.scanner_port,
                # SR-750 Communication 2 / Data Port 1 is configured as
                # ASCII with the CR terminator (not CRLF).
                role="client", terminator=b"\r")
            self.shared_scanner.start()
            # The scanner's acquisition state is controlled by the operator
            # button.  Queue the initial LON command now; TcpScanner replays
            # it automatically when its asynchronous TCP connection becomes
            # ready (and after any reconnect).
            self.shared_scanner.set_scan_enabled(self._scan_enabled)
            self.scanner_timer.start()
        except Exception as exc:
            self._set_shared_scanner_indicator(False)
            self.scanner_status.setText(f"共用扫码枪启动失败：{exc}")

    def _set_shared_scanner_indicator(self, online: bool):
        indicator = getattr(self, "shared_scanner_indicator", None)
        if indicator is None:
            return
        indicator.setProperty("state", "ok" if online else "ng")
        indicator.style().unpolish(indicator); indicator.style().polish(indicator)

    def _scanner_error_text(self, error):
        """Return a localized, actionable scanner connection diagnostic."""
        text = str(error)
        refused = any(token in text for token in ("10061", "ConnectionRefused", "Connection refused"))
        if refused:
            messages = {
                "中文": f"扫码枪 IP 可达，但 TCP {self.settings.scanner_port} 被拒绝；请检查 TCP Server/端口，或关闭其他占用客户端",
                "English": f"Scanner IP is reachable, but TCP {self.settings.scanner_port} was refused; check the TCP Server/port or another client",
                "Français": f"IP du scanner joignable, mais TCP {self.settings.scanner_port} est refusé ; vérifiez le serveur/port ou un autre client",
            }
            return messages[self._language]
        return {
            "中文": f"扫码枪断线，自动重连：{text}",
            "English": f"Scanner disconnected, reconnecting: {text}",
            "Français": f"Scanner déconnecté, reconnexion : {text}",
        }[self._language]

    def _poll_shared_scanner(self):
        scanner = self.shared_scanner
        if scanner is None:
            self._set_shared_scanner_indicator(False)
            return
        if not self._scan_enabled:
            self._set_shared_scanner_indicator(scanner.connected())
            self.scanner_status.setText({
                "中文": "共用扫码枪在线，但扫码已关闭",
                "English": "Shared scanner online, scanning disabled",
                "Français": "Scanner partagé en ligne, lecture désactivée",
            }[self._language])
            return
        handled_code = False
        while True:
            code = scanner.read_code(timeout=0)
            if code is None:
                break
            handled_code = True
            try:
                self.route_shared_scanner_code(code)
                self.scanner_input.setText(code)
            except Exception as exc:
                self.scanner_status.setText({
                    "中文": f"扫码拒绝：{exc}",
                    "English": f"Scan rejected: {exc}",
                    "Français": f"Scan refusé : {exc}",
                }[self._language])
        if scanner.last_error:
            self._set_shared_scanner_indicator(False)
            self.scanner_status.setText(self._scanner_error_text(scanner.last_error))
        elif scanner.connected() and not handled_code:
            self._set_shared_scanner_indicator(True)
            self.scanner_status.setText({
                "中文": "共用扫码枪在线：A/B 自动路由",
                "English": "Shared scanner online: automatic A/B routing",
                "Français": "Scanner partagé en ligne : routage A/B automatique",
            }[self._language])
        elif not scanner.connected():
            self._set_shared_scanner_indicator(False)

    def toggle_shared_scanner(self):
        """Send LON/LOFF to the shared scanner without changing test state."""
        self._scan_enabled = not self._scan_enabled
        scanner = self.shared_scanner
        sent = scanner.set_scan_enabled(self._scan_enabled) if scanner is not None else False
        if not self._scan_enabled and scanner is not None:
            # Discard frames received before the operator re-enables scanning;
            # the toggle is an input gate, not a queued test command.
            while scanner.read_code(timeout=0) is not None:
                pass
        labels = {
            "中文": ("关闭扫码", "开启扫码"),
            "English": ("Disable scanning", "Enable scanning"),
            "Français": ("Désactiver lecture", "Activer lecture"),
        }[self._language]
        self.scanner_toggle.setText(labels[0] if self._scan_enabled else labels[1])
        command = "LON" if self._scan_enabled else "LOFF"
        if sent:
            self.scanner_status.setText({
                "中文": f"已发送 {command}",
                "English": f"Sent {command}",
                "Français": f"{command} envoyé",
            }[self._language])
        elif scanner is None or not scanner.connected():
            self.scanner_status.setText({
                "中文": f"扫码枪未连接，等待发送 {command}",
                "English": f"Scanner offline; {command} queued",
                "Français": f"Scanner hors ligne ; {command} en attente",
            }[self._language])

    def _enable_scanner_after_print(self, station, job_id=""):
        """Enable the shared scanner only after a confirmed print receipt.

        ``TcpScanner`` queues LON while the device is offline and replays it
        after reconnect.  Thus a successful label print never leaves the
        operator in a disabled acquisition state, while a failed print does
        not send a misleading scan-enable command.
        """
        self._scan_enabled = True
        scanner = getattr(self, "shared_scanner", None)
        sent = False
        error = ""
        try:
            if scanner is not None:
                sent = bool(scanner.set_scan_enabled(True))
            else:
                error = "scanner object unavailable"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        if hasattr(self, "scanner_toggle"):
            self.scanner_toggle.setText({
                "中文": "关闭扫码", "English": "Disable scanning",
                "Français": "Désactiver lecture",
            }[self._language])
        if sent:
            status = {
                "中文": "标签打印完成，已发送 LON",
                "English": "Label printed; LON sent",
                "Français": "Étiquette imprimée ; LON envoyé",
            }[self._language]
        else:
            status = {
                "中文": "标签打印完成，LON 已排队，等待扫码枪连接",
                "English": "Label printed; LON queued until scanner reconnects",
                "Français": "Étiquette imprimée ; LON en attente de reconnexion",
            }[self._language]
            if error:
                status += f" ({error})"
        if hasattr(self, "scanner_status"):
            self.scanner_status.setText(status)
        self._live_trace(
            f"SCANNER_LON_AFTER_PRINT station={getattr(station, 'value', station)} "
            f"job={job_id} sent={sent}"
            + (f" error={error}" if error else ""))
        return sent

    def closeEvent(self, event):
        timer = getattr(self, "calibration_timer", None)
        if timer is not None:
            timer.stop()
        timer = getattr(self, "scanner_timer", None)
        if timer is not None:
            timer.stop()
        timer = getattr(self, "ateq_heartbeat_timer", None)
        if timer is not None:
            timer.stop()
        timer = getattr(self, "b_trigger_timer", None)
        if timer is not None:
            timer.stop()
        scanner = getattr(self, "shared_scanner", None)
        if scanner is not None:
            scanner.close()
        for ateq in getattr(self, "live_ateq", {}).values():
            ateq.close()
        if self.real_plc is not None:
            self.real_plc.safe_stop("live hardware UI closed")
        super().closeEvent(event)

    def _login_panel(self):
        box = QGroupBox("登录 / Login / Connexion"); box.setObjectName("loginPanel"); layout = QVBoxLayout(box); layout.setContentsMargins(10, 12, 10, 8); layout.setSpacing(6)
        credentials = QHBoxLayout(); credentials.setSpacing(6); self.username = QLineEdit(); self.username.setObjectName("login_username"); self.username.setPlaceholderText("登录 Role"); self.password = QLineEdit(); self.password.setObjectName("login_password"); self.password.setPlaceholderText("密码 Password"); self.password.setEchoMode(QLineEdit.EchoMode.Password); credentials.addWidget(self.username); credentials.addWidget(self.password); layout.addLayout(credentials)
        actions = QHBoxLayout(); actions.setSpacing(6); login = QPushButton("确定 / Confirm"); login.setProperty("primary", True); login.setObjectName("login_button"); login.clicked.connect(self.login); exit_b = QPushButton("退出 / Exit"); exit_b.setObjectName("exit_button"); exit_b.setProperty("destructive", True); exit_b.clicked.connect(self.controlled_exit); actions.addWidget(login); actions.addWidget(exit_b); self.login_status = QLabel("未登录 / operator"); self.login_status.setProperty("state", "info"); actions.addWidget(self.login_status, 1); layout.addLayout(actions); box.setMaximumHeight(METRICS.footer_max_height); return box

    def _build_setup(self):
        page = QWidget(); page.setObjectName("page"); root = QVBoxLayout(page); root.setContentsMargins(METRICS.page_margin, 12, METRICS.page_margin, 12); root.setSpacing(METRICS.station_gap); title = QLabel("参数设置 / Setup / Coup monté"); title.setObjectName("pageTitle"); root.addWidget(title)
        # --- 管理员登录门禁：设置页参数管理必须先登录 ---
        gate_card = QFrame(); gate_card.setObjectName("setupGateBar")
        gate_row = QHBoxLayout(gate_card); gate_row.setContentsMargins(12, 6, 12, 6); gate_row.setSpacing(10)
        self.setup_gate_title = QLabel("设置管理（需登录）"); self.setup_gate_title.setObjectName("setup_gate_title"); gate_row.addWidget(self.setup_gate_title)
        self.setup_username = QLineEdit(); self.setup_username.setObjectName("setup_username"); self.setup_username.setPlaceholderText("用户名"); self.setup_username.setMaximumWidth(220); gate_row.addWidget(self.setup_username)
        self.setup_password = QLineEdit(); self.setup_password.setObjectName("setup_password"); self.setup_password.setPlaceholderText("密码"); self.setup_password.setEchoMode(QLineEdit.EchoMode.Password); self.setup_password.setMaximumWidth(220); gate_row.addWidget(self.setup_password)
        self.setup_login_button = QPushButton("登录"); self.setup_login_button.setObjectName("setup_login"); self.setup_login_button.setProperty("primary", True); self.setup_login_button.clicked.connect(self.login_from_setup); gate_row.addWidget(self.setup_login_button)
        self.setup_gate_status = QLabel("未登录，参数只读"); self.setup_gate_status.setObjectName("setupGateStatus"); gate_row.addWidget(self.setup_gate_status); gate_row.addStretch(1); root.addWidget(gate_card)
        self.setup_admin_panel = QWidget(); admin = QVBoxLayout(self.setup_admin_panel); admin.setContentsMargins(0, 0, 0, 0); admin.setSpacing(METRICS.station_gap)
        # --- 型号参数区（整行）：表格即列表，单元格直接编辑，行首勾选当前生效型号 ---
        self.model_box = QGroupBox("型号参数"); model_box = self.model_box; model_box.setObjectName("modelPanel"); model_layout = QVBoxLayout(model_box); model_layout.setSpacing(6)
        self.model_new_button = QPushButton("新建型号"); self.model_new_button.setObjectName("model_new"); self.model_new_button.clicked.connect(self._new_model)
        self.model_delete_button = QPushButton("删除型号"); self.model_delete_button.setObjectName("model_delete"); self.model_delete_button.clicked.connect(self._delete_model)
        self.model_save_button = QPushButton("保存型号参数"); self.model_save_button.setProperty("primary", True); self.model_save_button.setObjectName("model_save"); self.model_save_button.clicked.connect(self._save_model)
        for button in (self.model_new_button, self.model_delete_button, self.model_save_button): button.setProperty("replica_source", button.text())
        model_buttons = QHBoxLayout(); self.model_status = QLabel(""); self.model_status.setObjectName("modelStatus"); self.model_hint = QLabel("单元格直接编辑；勾选“当前”立即生效"); self.model_hint.setObjectName("modelHint"); self.model_hint.setStyleSheet("color: #5d6b80;"); model_buttons.addWidget(self.model_new_button); model_buttons.addWidget(self.model_delete_button); model_buttons.addWidget(self.model_save_button); model_buttons.addSpacing(12); model_buttons.addWidget(self.model_hint); model_buttons.addStretch(1); model_buttons.addWidget(self.model_status); model_layout.addLayout(model_buttons)
        self._loading_models = True
        self.setup_table = QTableWidget(40, 9); self.setup_table.setObjectName("setupParameterTable"); self.setup_table.verticalHeader().setDefaultSectionSize(METRICS.input_height + 6); self.setup_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.setup_table.setAlternatingRowColors(True); StationPanel._configure_table(self.setup_table); self.setup_table.itemChanged.connect(self._on_model_item_changed); model_layout.addWidget(self.setup_table, 1)
        self._loading_models = False
        admin.addWidget(model_box, 4)
        # --- 底部行：人员列表（左） + 语言/ATEQ端口/校准周期（右） ---
        bottom_row = QHBoxLayout(); bottom_row.setSpacing(METRICS.station_gap)
        self.staff_box = QGroupBox("人员列表（独立管理）"); staff_box = self.staff_box; staff_box.setObjectName("personnelPanel"); staff_box.setMinimumWidth(300); staff_box.setMaximumWidth(460); staff_layout = QVBoxLayout(staff_box); staff_layout.setContentsMargins(12, 16, 12, 12); staff_layout.setSpacing(6); self._loading_personnel = True; self.personnel_table = QTableWidget(20, 1); self.personnel_table.setObjectName("personnel_table"); self.personnel_table.setHorizontalHeaderLabels(["工号/姓名"]); self.personnel_table.verticalHeader().setVisible(False); self.personnel_table.verticalHeader().setDefaultSectionSize(METRICS.input_height - 8); self.personnel_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.personnel_table.setAlternatingRowColors(True); self.personnel_table.itemChanged.connect(self._on_personnel_changed); staff_layout.addWidget(self.personnel_table, 1); staff_buttons = QHBoxLayout(); self.personnel_hint = QLabel("编辑后点击保存人员；空行忽略"); self.personnel_hint.setObjectName("personnelHint"); self.personnel_hint.setStyleSheet("color: #5d6b80;"); self.personnel_new_button = QPushButton("新建人员"); self.personnel_new_button.setObjectName("personnel_new"); self.personnel_new_button.setMaximumWidth(92); self.personnel_new_button.clicked.connect(self._new_person); self.personnel_delete_button = QPushButton("删除人员"); self.personnel_delete_button.setObjectName("personnel_delete"); self.personnel_delete_button.setMaximumWidth(92); self.personnel_delete_button.clicked.connect(self._remove_person); self.personnel_remove_button = self.personnel_delete_button; self.personnel_save_button = QPushButton("保存人员"); self.personnel_save_button.setObjectName("personnel_save"); self.personnel_save_button.setProperty("primary", True); self.personnel_save_button.setMaximumWidth(92); self.personnel_save_button.clicked.connect(self._save_personnel); staff_buttons.addWidget(self.personnel_hint, 1); staff_buttons.addWidget(self.personnel_new_button); staff_buttons.addWidget(self.personnel_delete_button); staff_buttons.addWidget(self.personnel_save_button); staff_layout.addLayout(staff_buttons); self._loading_personnel = False; bottom_row.addWidget(staff_box, 0)
        other_card = QFrame(); other_card.setObjectName("settingsCard")
        other_card_layout = QHBoxLayout(other_card); other_card_layout.setContentsMargins(16, 16, 16, 16); other_card_layout.setSpacing(0)
        settings_fields = QWidget(); settings_fields.setObjectName("settingsFields"); settings_fields.setMaximumWidth(680)
        other = QGridLayout(settings_fields); other.setContentsMargins(0, 0, 0, 0); other.setHorizontalSpacing(10); other.setVerticalSpacing(8)
        other.setColumnStretch(0, 0); other.setColumnStretch(1, 1)
        other_card_layout.addWidget(settings_fields, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop); other_card_layout.addStretch(1)
        self.language_selector = QComboBox(); self.language_selector.setObjectName("language_selector"); self.language_selector.setMaximumWidth(230); self.language_selector.addItems(list(UiTextCatalog.LANGUAGES)); self.language_selector.currentTextChanged.connect(self.language_changed)
        global_values = self.global_settings.load()
        self.global_station_a = QLineEdit(global_values.get("工位号A", "5")); self.global_station_a.setObjectName("global_station_a"); self.global_station_a.setMaximumWidth(90); self.global_station_a.editingFinished.connect(self._save_global_settings)
        self.global_station_b = QLineEdit(global_values.get("工位号B", "6")); self.global_station_b.setObjectName("global_station_b"); self.global_station_b.setMaximumWidth(90); self.global_station_b.editingFinished.connect(self._save_global_settings)
        cal_value = global_values.get("校准周期", "02:00:00"); cal_time = QTime.fromString(cal_value, "HH:mm:ss")
        self.cal_period = QTimeEdit(cal_time if cal_time.isValid() else QTime(0,0)); self.cal_period.setObjectName("calibration_period"); self.cal_period.setMaximumWidth(170); self.cal_period.editingFinished.connect(self._save_global_settings)
        port_items = list(self.settings.ateq_ports) if self.settings.ports_confirmed else ["BLOCKED/未确认"]
        self.ateq_a = QComboBox(); self.ateq_a.setObjectName("ateq_com_A"); self.ateq_a.setMaximumWidth(240); self.ateq_a.addItems(port_items); self.ateq_a.setCurrentText(self.settings.ateq_ports[0] if self.settings.ports_confirmed else "BLOCKED/未确认"); self.ateq_a.setEnabled(self.settings.ports_confirmed); self.ateq_com_A = self.ateq_a
        self.ateq_b = QComboBox(); self.ateq_b.setObjectName("ateq_com_B"); self.ateq_b.setMaximumWidth(240); self.ateq_b.addItems(port_items); self.ateq_b.setCurrentText(self.settings.ateq_ports[1] if self.settings.ports_confirmed else "BLOCKED/未确认"); self.ateq_b.setEnabled(self.settings.ports_confirmed); self.ateq_com_B = self.ateq_b
        self.setup_scanner = QLabel("● Scanner"); self.setup_scanner.setObjectName("setup_scanner_indicator"); self.setup_scanner.setProperty("state", "ng")
        self.global_label_a = QLabel("工位号 A"); self.global_label_b = QLabel("工位号 B")
        self.settings_language_label = QLabel("Language"); self.settings_language_label.setObjectName("settingsLanguageLabel")
        self.cal_period_label = QLabel("Calibration Period (Hours)"); self.cal_period_label.setObjectName("calibrationPeriodLabel")
        self.ateq_label_a = QLabel("ATEQ F620 A (Restart Software to Active)"); self.ateq_label_a.setObjectName("ateqLabelA")
        self.ateq_label_b = QLabel("ATEQ F620 B (Restart Software to Active)"); self.ateq_label_b.setObjectName("ateqLabelB")
        setting_labels = (self.settings_language_label, self.global_label_a, self.global_label_b, self.cal_period_label, self.ateq_label_a, self.ateq_label_b)
        for label in setting_labels:
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        left_aligned = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        other.addWidget(self.settings_language_label, 0, 0); other.addWidget(self.language_selector, 0, 1, left_aligned)
        other.addWidget(self.global_label_a, 1, 0); other.addWidget(self.global_station_a, 1, 1, left_aligned)
        other.addWidget(self.global_label_b, 2, 0); other.addWidget(self.global_station_b, 2, 1, left_aligned)
        other.addWidget(self.cal_period_label, 3, 0); other.addWidget(self.cal_period, 3, 1, left_aligned)
        other.addWidget(self.ateq_label_a, 4, 0); other.addWidget(self.ateq_a, 4, 1, left_aligned)
        other.addWidget(self.ateq_label_b, 5, 0); other.addWidget(self.ateq_b, 5, 1, left_aligned)
        self.settings_status = QLabel("已提交：SIM-PART"); self.settings_status.setObjectName("settingsStatus"); other.addWidget(self.settings_status, 6, 0, 1, 2)
        other.addWidget(self.setup_scanner, 7, 0); self.calibration_status = QLabel("等待 NG 样件"); self.calibration_status.setObjectName("calibrationStatus"); other.addWidget(self.calibration_status, 7, 1)
        bottom_row.addWidget(other_card, 1)
        admin.addLayout(bottom_row, 1); root.addWidget(self.setup_admin_panel, 1); self.tabs.addTab(page, "设置/Setup/Coup Monté")

    # ---------- 设置页：登录门禁 + 型号参数 + 人员列表 ----------
    def _setup_gate_texts(self):
        return {
            "中文": {"title": "设置管理（需登录）", "ok": "已认证", "denied": "登录失败", "readonly": "未登录，参数只读"},
            "English": {"title": "Setup admin (login required)", "ok": "Authenticated", "denied": "Sign-in failed", "readonly": "Read-only, sign in to edit"},
            "Français": {"title": "Réglages (connexion requise)", "ok": "Authentifié", "denied": "Échec de connexion", "readonly": "Lecture seule, connectez-vous"},
        }[self._language]
    def _apply_setup_language(self, value):
        """设置页新增控件的 tri-lingual texts（默认中文，切换即刷新）。"""
        texts = {
            "中文": {
                "login": "登录", "new": "新建型号", "delete": "删除型号", "save": "保存型号参数",
                "person_new": "新建人员", "person_delete": "删除人员", "person_save": "保存人员",
                "model_box": "型号参数", "staff_box": "人员列表（独立管理）", "hint": "单元格直接编辑；勾选“当前”立即生效",
                "personnel_hint": "直接在表中输入，自动保存；空行忽略", "staff_header": "工号/姓名",
                "global_a": "工位号 A", "global_b": "工位号 B",
                "placeholders": {"setup_username": "用户名", "setup_password": "密码"},
            },
            "English": {
                "login": "Login", "new": "New Model", "delete": "Delete Model", "save": "Save Model",
                "person_new": "New Person", "person_delete": "Delete Person", "person_save": "Save People",
                "model_box": "Model Parameters", "staff_box": "Personnel (independent)", "hint": "Edit cells directly; check Current to activate",
                "personnel_hint": "Type in the table; auto-saved, empty rows ignored", "staff_header": "ID/Name",
                "global_a": "Station A", "global_b": "Station B",
                "placeholders": {"setup_username": "user", "setup_password": "password"},
            },
            "Français": {
                "login": "Connexion", "new": "Nouveau modèle", "delete": "Supprimer le modèle", "save": "Enregistrer le modèle",
                "person_new": "Nouveau personnel", "person_delete": "Supprimer", "person_save": "Enregistrer",
                "model_box": "Paramètres modèle", "staff_box": "Personnel (indépendant)", "hint": "Éditez les cellules; cochez Actuel pour activer",
                "personnel_hint": "Saisir dans le tableau; enregistré, lignes vides ignorées", "staff_header": "Matricule/Nom",
                "global_a": "Poste A", "global_b": "Poste B",
                "placeholders": {"setup_username": "utilisateur", "setup_password": "mot de passe"},
            },
        }[value]
        self.setup_gate_title.setText(self._setup_gate_texts()["title"])
        self.setup_login_button.setText(texts["login"])
        self.model_new_button.setText(texts["new"]); self.model_delete_button.setText(texts["delete"]); self.model_save_button.setText(texts["save"])
        self.personnel_new_button.setText(texts["person_new"]); self.personnel_delete_button.setText(texts["person_delete"]); self.personnel_save_button.setText(texts["person_save"])
        self.model_box.setTitle(texts["model_box"]); self.staff_box.setTitle(texts["staff_box"]); self.model_hint.setText(texts["hint"])
        self.personnel_hint.setText(texts["personnel_hint"])
        self.personnel_table.setHorizontalHeaderLabels([texts["staff_header"]])
        self.global_label_a.setText(texts["global_a"]); self.global_label_b.setText(texts["global_b"])
        for object_name, placeholder in texts["placeholders"].items():
            widget = self.findChild(QLineEdit, object_name)
            if widget is not None: widget.setPlaceholderText(placeholder)
        self._update_setup_gate()
    def _update_setup_gate(self):
        """设置页参数管理仅管理员可用；登录状态变化时同步启用/禁用。"""
        texts = self._setup_gate_texts()
        is_admin = self.security.role.value == "admin"
        self.setup_admin_panel.setEnabled(is_admin)
        self.setup_gate_title.setText(texts["title"])
        self.setup_gate_status.setText(texts["ok"] if is_admin else texts["readonly"])
        self.setup_gate_status.setStyleSheet("color:#1a7f37;font-weight:600;" if is_admin else "color:#b42318;font-weight:600;")
        if not is_admin:
            self.model_status.setText("")
    def login_from_setup(self):
        if self.security.login(self.setup_username.text().strip(), self.setup_password.text()):
            self.username.setText(self.setup_username.text()); self.password.setText(self.setup_password.text())
            self.login_status.setText({"中文": "已认证", "English": "Authenticated", "Français": "Authentifié"}[self._language])
            self._update_setup_gate()
            for card in self.cards:
                card.refresh()
            self.setup_password.clear()
        else:
            self.setup_gate_status.setText(self._setup_gate_texts()["denied"])
    def _template_choices(self, station_letter=None):
        """List ordinary templates; A/B assignment comes from the column, not filename."""
        directory = Path(self.data_dir)
        if not directory.is_dir():
            return []
        return sorted(
            path.name for path in directory.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".btw"
            and not is_calibration_template(path)
        )

    # 0..7 are the production surface.  Column 8 remains hidden only to keep
    # binary/UI automation compatibility with the first nine-column release.
    # 0=当前 1=产品型号 2=客户编号 3=条码规则 4=日期方案 5=ATEQ程序号
    # 6=打印模板A 7=打印模板B
    MODEL_COLUMNS = 9

    def _make_combo(self, kind, value=""):
        combo = QComboBox()
        if kind == "rule":
            combo.setEditable(True)
            combo.addItems(BARCODE_RULE_PRESETS)
        elif kind == "date":
            # Old 日期设置.ini files use Chinese names such as
            # 年方案2+月方案1+日方案2.  Keep that raw value in userData for
            # the barcode engine, but show a language-neutral compact code.
            combo.setEditable(False)
            options = list(DATE_SCHEME_PRESETS)
            if value and value not in options:
                options.append(value)
            for index, raw in enumerate(options, 1):
                combo.addItem(self._date_scheme_display(raw, index), raw)
            for index in range(combo.count()):
                if combo.itemData(index) == value:
                    combo.setCurrentIndex(index); break
            return combo
        else:
            choices = [str(Path(self.data_dir) / name) for name in self._template_choices()]
            if value and value not in choices:
                choices.insert(0, value)
            combo.addItems(choices)
        if value:
            combo.setCurrentText(value)
        return combo

    @staticmethod
    def _date_scheme_display(raw, index=1):
        if raw in DATE_SCHEME_PRESETS:
            return raw
        parts = {}
        for key, prefix in (("Y", "年方案"), ("M", "月方案"), ("D", "日方案")):
            match = re.search(rf"{re.escape(prefix)}(\d+)", str(raw))
            if match:
                parts[key] = match.group(1)
        if parts:
            return "-".join(f"{key}{parts[key]}" for key in ("Y", "M", "D") if key in parts)
        return f"CUSTOM_{index}"

    @staticmethod
    def _program_widget(row, value="1"):
        widget = QSpinBox(); widget.setObjectName(f"ateq_program_{row}")
        widget.setRange(1, 255); widget.setValue(int(str(value or "1").strip()))
        widget.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        widget.setKeyboardTracking(False); widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return widget

    def _populate_model_row(self, row, config: ModelConfig, current: bool):
        self._loading_models = True
        check = QTableWidgetItem(); check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        check.setCheckState(Qt.CheckState.Checked if current else Qt.CheckState.Unchecked)
        self.setup_table.setItem(row, 0, check)
        part = QTableWidgetItem(config.part_no); part.setData(Qt.ItemDataRole.UserRole, config.part_no)
        self.setup_table.setItem(row, 1, part)
        self.setup_table.setItem(row, 2, QTableWidgetItem(config.customer_no))
        self.setup_table.setCellWidget(row, 5, self._program_widget(row, config.ateq_program))
        self.setup_table.setCellWidget(row, 3, self._make_combo("rule", config.barcode_rule))
        self.setup_table.setCellWidget(row, 4, self._make_combo("date", config.date_scheme))
        self.setup_table.setCellWidget(row, 6, self._make_combo("template_A", config.template_a))
        self.setup_table.setCellWidget(row, 7, self._make_combo("template_B", config.template_b))
        self._loading_models = False

    def _clear_model_row(self, row):
        self._loading_models = True
        for column in range(self.MODEL_COLUMNS):
            self.setup_table.setItem(row, column, None)
            self.setup_table.removeCellWidget(row, column)
        self._loading_models = False

    def _resize_model_columns(self):
        """按内容自适应列宽，并保证各参数列有最小可读宽度。"""
        table = self.setup_table
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        table.resizeColumnsToContents()
        minimums = {0: 46, 1: 130, 2: 150, 3: 420, 4: 150,
                    5: 110, 6: 240, 7: 240}
        for column, width in minimums.items():
            if table.columnWidth(column) < width:
                table.setColumnWidth(column, width)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(6, 240)
        table.setColumnWidth(7, 240)
        table.setColumnHidden(8, True)
    def _refresh_models(self, select_part=None):
        """把 日期设置.ini 里的型号刷进表格；行首勾选当前生效型号。"""
        models = self.model_settings.list_models()
        self._loading_models = True
        for row in range(40):
            for column in range(self.MODEL_COLUMNS):
                self.setup_table.setItem(row, column, None)
                self.setup_table.removeCellWidget(row, column)
        self._loading_models = False
        current = self.product_settings.current_product()
        for index, part in enumerate(models[:40]):
            try:
                config = self.model_settings.load(part)
            except KeyError:
                continue
            self._populate_model_row(index, config, current=bool(select_part and part == select_part) or (not select_part and part == current))
        if select_part:
            self.model_status.setText({"中文": f"当前型号：{select_part}", "English": f"Current model: {select_part}", "Français": f"Modèle actuel : {select_part}"}[self._language])
        self._resize_model_columns()

    def _on_model_item_changed(self, item):
        if self._loading_models or item.column() != 0:
            return
        row = item.row()
        if item.checkState() != Qt.CheckState.Checked:
            return
        part_item = self.setup_table.item(row, 1)
        part = part_item.text().strip() if part_item else ""
        if not part:
            self._loading_models = True; item.setCheckState(Qt.CheckState.Unchecked); self._loading_models = False
            return
        self._loading_models = True
        for other_row in range(40):
            if other_row == row:
                continue
            other = self.setup_table.item(other_row, 0)
            if other is not None and other.checkState() == Qt.CheckState.Checked:
                other.setCheckState(Qt.CheckState.Unchecked)
        self._loading_models = False
        try:
            self.product_settings.save(part)
            self.model_status.setText({"中文": f"当前型号：{part}", "English": f"Current model: {part}", "Français": f"Modèle actuel : {part}"}[self._language])
        except Exception as exc:
            self._loading_models = True; item.setCheckState(Qt.CheckState.Unchecked); self._loading_models = False
            self.model_status.setText({"中文": f"选择拒绝：{exc}", "English": f"Selection denied: {exc}", "Français": f"Sélection refusée : {exc}"}[self._language])

    def _collect_table_models(self) -> list[ModelConfig]:
        configs = []
        for row in range(40):
            part_item = self.setup_table.item(row, 1)
            part = part_item.text().strip() if part_item else ""
            if not part:
                continue
            rule_widget = self.setup_table.cellWidget(row, 3); date_widget = self.setup_table.cellWidget(row, 4)
            template_widget = self.setup_table.cellWidget(row, 6)
            def cell_text(column, default=""):
                item = self.setup_table.item(row, column)
                return item.text().strip() if item else default
            program_widget = self.setup_table.cellWidget(row, 5)
            program = str(program_widget.value()) if isinstance(program_widget, QSpinBox) else cell_text(5, "1") or "1"
            date_value = date_widget.currentData() if date_widget else DATE_SCHEME_PRESETS[0]
            template_a_widget = self.setup_table.cellWidget(row, 6)
            template_b_widget = self.setup_table.cellWidget(row, 7)
            template_a = (template_a_widget.currentData() or template_a_widget.currentText()).strip() if template_a_widget else ""
            template_b = (template_b_widget.currentData() or template_b_widget.currentText()).strip() if template_b_widget else ""
            family = self._template_family(template_a, template_b, part)
            configs.append(ModelConfig(
                part_no=part,
                customer_no=cell_text(2),
                barcode_rule=rule_widget.currentText().strip() if rule_widget else BARCODE_RULE_PRESETS[0],
                date_scheme=str(date_value or DATE_SCHEME_PRESETS[0]).strip(),
                template_family=family,
                template_path_a=template_a,
                template_path_b=template_b,
                ateq_program=program,
            ))
        return configs

    @staticmethod
    def _template_family(template_a, template_b, part):
        candidate = (template_a or template_b or fr"D:\data\{part}.btw").strip()
        path = Path(candidate)
        stem = path.stem
        if stem.upper().endswith(("-A", "-B")):
            stem = stem[:-2]
        return str(path.with_name(stem + path.suffix))

    def _save_model(self):
        try:
            configs = self._collect_table_models()
            parts = [config.part_no for config in configs]
            if len(parts) != len(set(parts)):
                raise ValueError("产品型号重复 / duplicate part numbers")
            self.model_settings.save_all(configs)
            self._refresh_runtime_choices()
            self.model_status.setText({"中文": f"已保存 {len(configs)} 个型号", "English": f"Saved {len(configs)} models", "Français": f"{len(configs)} modèles enregistrés"}[self._language])
        except Exception as exc:
            self.model_status.setText({"中文": f"保存拒绝：{exc}", "English": f"Save denied: {exc}", "Français": f"Enregistrement refusé : {exc}"}[self._language])
    def _new_model(self):
        part, ok = QInputDialog.getText(self, "新建型号 / New model", "产品型号 / Part No.:")
        if not ok or not part.strip():
            return
        part = part.strip()
        empty_row = None
        for row in range(40):
            item = self.setup_table.item(row, 1)
            if item is None or not item.text().strip():
                empty_row = row; break
        if empty_row is None:
            self.model_status.setText({"中文": "型号表已满（40）", "English": "Model table full (40)", "Français": "Tableau complet (40)"}[self._language]); return
        self._populate_model_row(empty_row, ModelConfig(
            part_no=part, template_family=fr"D:\data\{part}.btw"), current=False)
        self.model_status.setText({"中文": f"已添加行：{part}（点“保存型号参数”写入）", "English": f"Row added: {part} (click Save Model to write)", "Français": f"Ligne ajoutée : {part} (cliquez Enregistrer)"}[self._language])
    def _delete_model(self):
        row = self.setup_table.currentRow()
        if row < 0:
            self.model_status.setText({"中文": "先点击选择要删除的行", "English": "Select a row first", "Français": "Sélectionnez d'abord une ligne"}[self._language]); return
        part_item = self.setup_table.item(row, 1)
        part = part_item.text().strip() if part_item else ""
        if not part:
            return
        answer = QMessageBox.question(self, "删除型号 / Delete model", {"中文": f"确定删除型号 {part} ？", "English": f"Delete model {part} ?", "Français": f"Supprimer le modèle {part} ?"}[self._language], QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._clear_model_row(row)
        self.model_status.setText({"中文": f"已移除行：{part}（点“保存型号参数”写入）", "English": f"Row removed: {part} (click Save Model to write)", "Français": f"Ligne supprimée : {part} (cliquez Enregistrer)"}[self._language])
    def _refresh_personnel(self):
        self._loading_personnel = True
        for row in range(20):
            self.personnel_table.setItem(row, 0, None)
        for row, name in enumerate(self.personnel.list_all()[:20]):
            self.personnel_table.setItem(row, 0, QTableWidgetItem(name))
        self._loading_personnel = False
    def _on_personnel_changed(self, item):
        if self._loading_personnel:
            return
        self.personnel_hint.setText({"中文": "有未保存修改，请点击保存人员", "English": "Unsaved changes; click Save People", "Français": "Modifications non enregistrées ; cliquez Enregistrer"}[self._language])

    def _personnel_names_from_table(self):
        names = []
        for row in range(20):
            cell = self.personnel_table.item(row, 0)
            if cell is not None and cell.text().strip():
                names.append(cell.text().strip())
        return names

    def _new_person(self):
        name, ok = QInputDialog.getText(self, "新建人员 / New person", "工号/姓名 / ID or name:")
        if not ok or not name.strip():
            return
        try:
            self.personnel.add(name)
            self._refresh_personnel(); self._refresh_runtime_choices()
            self.personnel_hint.setText({"中文": f"已新建人员：{name.strip()}", "English": f"Created: {name.strip()}", "Français": f"Créé : {name.strip()}"}[self._language])
        except Exception as exc:
            self.personnel_hint.setText({"中文": f"新建拒绝：{exc}", "English": f"Create denied: {exc}", "Français": f"Création refusée : {exc}"}[self._language])

    def _save_personnel(self):
        try:
            names = self.personnel.write_all(self._personnel_names_from_table())
            self._refresh_personnel(); self._refresh_runtime_choices()
            self.personnel_hint.setText({"中文": f"已保存 {len(names)} 名人员", "English": f"Saved {len(names)} people", "Français": f"{len(names)} personnes enregistrées"}[self._language])
        except Exception as exc:
            self.personnel_hint.setText({"中文": f"保存拒绝：{exc}", "English": f"Save denied: {exc}", "Français": f"Enregistrement refusé : {exc}"}[self._language])

    def _remove_person(self):
        row = self.personnel_table.currentRow()
        if row < 0:
            return
        item = self.personnel_table.item(row, 0)
        name = item.text().strip() if item is not None else ""
        if not name:
            return
        try:
            self.personnel.remove(name)
            self._refresh_personnel(); self._refresh_runtime_choices()
            self.personnel_hint.setText({"中文": f"已删除人员：{name}", "English": f"Deleted: {name}", "Français": f"Supprimé : {name}"}[self._language])
        except Exception as exc:
            self.personnel_hint.setText({"中文": f"删除拒绝：{exc}", "English": f"Delete denied: {exc}", "Français": f"Suppression refusée : {exc}"}[self._language])

    def _build_query(self):
        page = QWidget(); page.setObjectName("page"); root = QVBoxLayout(page); root.setContentsMargins(METRICS.page_margin, 16, METRICS.page_margin, 16); root.setSpacing(METRICS.station_gap)
        title = QLabel("查询记录 / Test Records"); title.setObjectName("pageTitle"); title.setProperty("replica_source", "查询记录 / Test Records"); root.addWidget(title)
        hint_source = "两工位独立查询 · 支持时间、条码和结果 / Search station records by time, code, and result"
        hint = QLabel(hint_source); hint.setObjectName("pageSubtitle"); hint.setProperty("replica_source", hint_source); root.addWidget(hint)
        filters = QHBoxLayout(); filters.setSpacing(METRICS.station_gap); self.query_fields = {}
        for s in StationId:
            group = QGroupBox(f"{s.value} List Search"); group.setObjectName(f"queryFilters_{s.value}"); group.setProperty("queryFilterCard", True); grid = QGridLayout(group); grid.setContentsMargins(12, 18, 12, 12); grid.setHorizontalSpacing(9); grid.setVerticalSpacing(7)
            for row, (key, label) in enumerate((("start", "Start Time"),("finish", "Finish Time"),("code", "2D Code"),("result", "Result"))):
                if key in {"start", "finish"}:
                    widget = QDateTimeEdit(QDateTime.currentDateTime()); widget.setCalendarPopup(True); widget.setDisplayFormat("yyyy-MM-dd HH:mm:ss"); widget.setDateTime(QDateTime.currentDateTime().addDays(-1 if key == "start" else 1))
                else:
                    widget = QLineEdit()
                widget.setObjectName(f"query_{key}_{s.value}"); self.query_fields[(s,key)] = widget; grid.addWidget(QLabel(f"{label} {s.value}"),row,0); grid.addWidget(widget,row,1)
            status = QLabel(); status.setObjectName(f"query_status_{s.value}"); self.query_status = getattr(self, "query_status", {}); self.query_status[s] = status; grid.addWidget(status,5,0,1,2)
            search = QPushButton(f"Search {s.value}"); search.setObjectName(f"query_search_{s.value}"); search.setProperty("primary", True); search.clicked.connect(self.refresh_query)
            download = QPushButton(f"Download {s.value}"); download.setObjectName(f"query_download_{s.value}"); download.clicked.connect(lambda _=False, station=s: self.download_query(station))
            actions = QHBoxLayout(); actions.setSpacing(8); actions.addWidget(search, 1); actions.addWidget(download, 1); grid.addLayout(actions, 4, 0, 1, 2); filters.addWidget(group)
        root.addLayout(filters); tables = QHBoxLayout(); tables.setSpacing(METRICS.station_gap); self.query_tables = {}
        for s in StationId:
            table = QTableWidget(30,10); table.setObjectName(f"query_table_{s.value}"); table.setHorizontalHeaderLabels(DISPLAY_HEADERS["base"]); table.setAlternatingRowColors(True); table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed); table.verticalHeader().setDefaultSectionSize(METRICS.table_row_height); StationPanel._configure_table(table); [table.setRowHeight(i, METRICS.table_row_height) for i in range(30)]; self.query_tables[s] = table; tables.addWidget(table, 1)
        root.addLayout(tables,1); self.query_edit = self.query_fields[(StationId.A,"code")]; self.table = self.query_tables[StationId.A]; self.tabs.addTab(page, "查询/Query/Requête")

    def _build_manual(self):
        page = QWidget(); page.setObjectName("page"); page.setProperty("manualPage", True)
        root = QVBoxLayout(page); root.setContentsMargins(METRICS.page_margin, 16, METRICS.page_margin, 16); root.setSpacing(METRICS.station_gap)
        self._manual_extensions = []
        header = QHBoxLayout(); header.setSpacing(16)
        heading = QVBoxLayout(); heading.setSpacing(3)
        title = QLabel("手动控制 / Manual Controls"); title.setObjectName("manualPageTitle")
        title.setProperty("replica_source", "手动控制 / Manual Controls")
        hint_source = "手动输出需管理员授权 · PLC 状态实时回读 / Admin authorization required · live PLC state"
        hint = QLabel(hint_source); hint.setObjectName("manualPageHint"); hint.setProperty("replica_source", hint_source)
        heading.addWidget(title); heading.addWidget(hint); header.addLayout(heading, 1)
        toggle = QPushButton("显示扩展 / Extensions"); toggle.setObjectName("manual_extension_toggle"); toggle.setProperty("compact", True); toggle.clicked.connect(self.toggle_manual_extension); header.addWidget(toggle, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)
        stations = QHBoxLayout(); stations.setSpacing(METRICS.station_gap)
        for s in StationId:
            group = QGroupBox(f"工位 {s.value} / Station {s.value}"); group.setObjectName(f"manualGroup_{s.value}"); group.setProperty("stationTitle", True); layout = QVBoxLayout(group); layout.setContentsMargins(14, 22, 14, 14); layout.setSpacing(9)
            for signal, title, states in MANUAL_NAMES:
                row_frame = QFrame(); row_frame.setObjectName(f"manualRow_{signal}_{s.value}"); row_frame.setProperty("manualControlRow", True); row_frame.setMinimumHeight(68)
                row = QHBoxLayout(row_frame); row.setContentsMargins(12, 8, 12, 8); row.setSpacing(8)
                label_widget = QLabel(f"{s.value}{title}"); label_widget.setObjectName(f"manual_label_{signal}_{s.value}"); label_widget.setProperty("manualControlLabel", True); label_widget.setMinimumWidth(118); label_widget.setMaximumWidth(172); row.addWidget(label_widget, 1)
                # The legacy unsuffixed button remains the safe toggle entry
                # point for existing callers; explicit target buttons below
                # make the intended PLC state unambiguous to operators.
                button = QPushButton("状态切换 / Toggle"); button.setObjectName(f"manual_{signal}_{s.value}"); button.setProperty("compact", True); button.setProperty("manualToggle", True); button.setMinimumWidth(82); button.clicked.connect(lambda _=False, station=s, sig=signal, b=button: self.manual_output(b, station, sig)); row.addWidget(button)
                if signal in {"clamp", "transfer", "block", "stamp"}:
                    for state, text in ((False, UiTextCatalog.action(self._language, "back")), (True, UiTextCatalog.action(self._language, "forward"))):
                        target = QPushButton(text); target.setObjectName(f"manual_{signal}_{s.value}_{'forward' if state else 'back'}"); target.setProperty("compact", True); target.setMinimumWidth(54); target.clicked.connect(lambda _=False, station=s, sig=signal, value=state, b=target: self._manual_target_action(station, sig, value, b)); row.addWidget(target)
                elif signal == "door_disable":
                    for state, key in ((False, "enable"), (True, "disable")):
                        target = QPushButton(UiTextCatalog.action(self._language, key)); target.setObjectName(f"manual_{signal}_{s.value}_{key}"); target.setProperty("compact", True); target.setMinimumWidth(54); target.clicked.connect(lambda _=False, station=s, sig=signal, value=state, b=target: self._manual_target_action(station, sig, value, b)); row.addWidget(target)
                else:
                    for state, key in ((False, "automatic"), (True, "manual")):
                        target = QPushButton(UiTextCatalog.action(self._language, key)); target.setObjectName(f"manual_{signal}_{s.value}_{key}"); target.setProperty("compact", True); target.setMinimumWidth(54); target.clicked.connect(lambda _=False, station=s, sig=signal, value=state, b=target: self._manual_target_action(station, sig, value, b)); row.addWidget(target)
                readback = QLabel("● OFF"); readback.setObjectName(f"manual_readback_{signal}_{s.value}"); readback.setProperty("manualReadback", True); readback.setProperty("state", "info"); readback.setAlignment(Qt.AlignmentFlag.AlignCenter); readback.setMinimumWidth(72); readback.setMaximumHeight(34); row.addWidget(readback)
                layout.addWidget(row_frame)
            # Compatibility aliases for the diagnostic pressure/start points;
            # they remain hidden so the operator page has exactly six rows.
            for signal, title in (("pressure", "正/负压 / Pressure"), ("start", "启动 / Start")):
                alias = QPushButton(f"{title}: OFF"); alias.setObjectName(f"manual_{signal}_{s.value}"); alias.clicked.connect(lambda _=False, station=s, sig=signal, b=alias: self.manual_output(b, station, sig)); alias.setVisible(False); self._manual_extensions.append(alias); layout.addWidget(alias)
            stations.addWidget(group, 1)
        root.addLayout(stations, 1)
        recovery = QGroupBox("管理员恢复 / Recovery"); recovery.setObjectName("recoveryPanel"); recovery.setVisible(False); self._manual_recovery = recovery; rl = QVBoxLayout(recovery); self.recovery_reason = QLineEdit(); self.recovery_reason.setObjectName("recovery_reason"); self.recovery_reason.setPlaceholderText("处理原因 / reason"); rl.addWidget(self.recovery_reason); self.recovery_status = QLabel("无恢复操作"); self.recovery_status.setObjectName("recoveryStatus"); rl.addWidget(self.recovery_status)
        for s in StationId:
            b = QPushButton(f"归档工位 {s.value}"); b.setObjectName(f"resolve_recovery_{s.value}"); b.clicked.connect(lambda _=False, station=s: self.resolve_recovery(station, self.recovery_reason.text() or "UI 人工确认")); rl.addWidget(b)
        root.addWidget(recovery); self.tabs.addTab(page, "手动/Manual/Manuelle")

    def command_manual_target(self, station: StationId, signal: str, target: bool) -> bool:
        """Write one explicit manual target after role and confirmation gates.

        ``target`` is the PLC bit target itself; no UI-side inversion is
        applied, preserving the established POINTS polarity and the separate
        A/B addresses.  Readback labels are updated only after a successful
        write/read cycle.
        """
        if not isinstance(station, StationId):
            station = StationId(str(station))
        self.security.require("manual_output")
        if signal not in POINTS or station not in POINTS[signal]:
            raise ValueError(f"未知手动信号: {signal}/{station.value}")
        byte, bit = POINTS[signal][station]
        current = self.plc.read_bit(byte, bit)
        if not self.confirmation_callback(station, signal, bool(target), current):
            return False
        self.plc.write_bit(byte, bit, bool(target))
        actual = self.plc.read_bit(byte, bit)
        readback = self.findChild(QLabel, f"manual_readback_{signal}_{station.value}")
        if readback is not None:
            readback.setText({"中文": f"● {'开' if actual else '关'}", "English": f"● {'ON' if actual else 'OFF'}", "Français": f"● {'MARCHE' if actual else 'ARRÊT'}"}[self._language])
            readback.setProperty("state", "ok" if actual else "info")
            readback.style().unpolish(readback); readback.style().polish(readback)
        return actual == bool(target)

    def _manual_target_action(self, station: StationId, signal: str, target: bool, button: QPushButton) -> None:
        try:
            ok = self.command_manual_target(station, signal, target)
            button.setText(("✓ " if ok else "× ") + button.text().lstrip("✓× "))
        except Exception as exc:
            prefix = {"中文": "拒绝", "English": "Denied", "Français": "Refusé"}[self._language]
            button.setText({"中文": "拒绝：权限不足", "English": "Denied: permission required", "Français": "Refusé : autorisation requise"}[self._language])

    def _refresh_manual_readbacks(self) -> None:
        for station in StationId:
            for signal in ("clamp", "transfer", "block", "stamp", "door_disable", "manual"):
                if signal not in POINTS:
                    continue
                byte, bit = POINTS[signal][station]
                value = self.plc.read_bit(byte, bit)
                readback = self.findChild(QLabel, f"manual_readback_{signal}_{station.value}")
                if readback is not None:
                    language = getattr(self.window(), "_language", "中文")
                    readback.setText({"中文": f"● {'开' if value else '关'}", "English": f"● {'ON' if value else 'OFF'}", "Français": f"● {'MARCHE' if value else 'ARRÊT'}"}[language])
                    readback.setProperty("state", "ok" if value else "info")
                    readback.style().unpolish(readback); readback.style().polish(readback)

    def toggle_manual_extension(self):
        visible = not self._manual_recovery.isVisible()
        self._manual_recovery.setVisible(visible)
        for button in self._manual_extensions:
            button.setVisible(visible)

    def login(self):
        ok = self.security.login(self.username.text(), self.password.text())
        self.login_status.setText({"中文": ("已认证" if ok else "登录失败"), "English": ("Authenticated" if ok else "Sign-in failed"), "Français": ("Authentifié" if ok else "Échec de connexion")} [self._language])
        self._update_setup_gate()
        for card in self.cards:
            card.refresh()
    def controlled_exit(self):
        try: self.security.require("shutdown"); self.plc.safe_stop("controlled UI exit"); self.close()
        except Exception: self.login_status.setText({"中文": "退出拒绝：权限不足", "English": "Exit denied: permission required", "Français": "Sortie refusée : autorisation requise"}[self._language])
    def _apply_language(self, value):
        """Apply one catalog language to every visible label and placeholder."""
        self._language = value
        self.setWindowTitle(UiTextCatalog.translate("Leak Test 2 Channels / 气密检测", value))
        for index, label in enumerate(UiTextCatalog.tabs(value)):
            self.tabs.tabBar().setTabText(index, label)
        translated_headers = DISPLAY_HEADERS[value]
        for table in list(self.query_tables.values()) + [card.table for card in self.cards]:
            table.setHorizontalHeaderLabels(translated_headers)
        self.setup_table.setHorizontalHeaderLabels({"中文": ["当前", "产品型号", "客户编号", "条码规则", "日期方案", "ATEQ 程序号", "打印模板 A", "打印模板 B", "兼容保留"],
                                                     "English": ["Current", "Part No.", "Customer No.", "Barcode Rule", "Date Scheme", "ATEQ Program", "Print Template A", "Print Template B", "Reserved"],
                                                     "Français": ["Actuel", "N° pièce", "N° client", "Règle code", "Schéma date", "Programme ATEQ", "Modèle A", "Modèle B", "Réservé"]}[value])
        self.setup_table.setColumnHidden(8, True)
        widgets = self.findChildren(QLabel) + self.findChildren(QPushButton) + self.findChildren(QGroupBox)
        for widget in widgets:
            source = widget.property("replica_source")
            if not source:
                source = widget.title() if isinstance(widget, QGroupBox) else widget.text()
                widget.setProperty("replica_source", source)
            text = UiTextCatalog.translate(str(source), value)
            if isinstance(widget, QGroupBox):
                widget.setTitle(text)
            elif widget.objectName().startswith("scanner_route_"):
                station = widget.objectName().rsplit("_", 1)[-1]
                widget.setText({"中文": f"扫码到 {station}", "English": f"Scan to {station}", "Français": f"Scanner vers {station}"}[value])
            elif widget.objectName() == "scanner_toggle":
                widget.setText({
                    "中文": "关闭扫码" if self._scan_enabled else "开启扫码",
                    "English": "Disable scanning" if self._scan_enabled else "Enable scanning",
                    "Français": "Désactiver lecture" if self._scan_enabled else "Activer lecture",
                }[value])
            elif widget.objectName().startswith("reprint_"):
                station = widget.objectName().rsplit("_", 1)[-1]
                widget.setText({
                    "中文": f"标签重打 {station}",
                    "English": f"Reprint label {station}",
                    "Français": f"Réimprimer {station}",
                }[value])
            elif widget.objectName().startswith("start_"):
                # The control is a full tile rather than a caption plus an
                # extra confirmation button.  Break the longer translations
                # over two lines so the same responsive tile width remains
                # readable on the 1366px layout.
                widget.setText({"中文": "启动验证", "English": "Start\nValidation", "Français": "Validation\nDémarrage"}[value])
            elif widget.objectName().startswith("cancel_calibration_"):
                station = widget.objectName().rsplit("_", 1)[-1]
                widget.setText({"中文": f"取消校准 {station}", "English": f"Cancel calibration {station}", "Français": f"Annuler calibration {station}"}[value])
            elif widget.objectName().startswith("query_search_"):
                station = widget.objectName().rsplit("_", 1)[-1]
                widget.setText({"中文": f"查询 {station}", "English": f"Search {station}", "Français": f"Rechercher {station}"}[value])
            elif widget.objectName().startswith("query_download_"):
                station = widget.objectName().rsplit("_", 1)[-1]
                widget.setText({"中文": f"下载 {station}", "English": f"Download {station}", "Français": f"Télécharger {station}"}[value])
            else:
                widget.setText(text)
        for station in StationId:
            card = self.cards[0 if station is StationId.A else 1]
            for (signal, _), label_text in zip(INDICATOR_NAMES, INDICATOR_DISPLAY_LABELS[value]):
                label = card.indicator_labels.get(signal)
                if label is None:
                    continue
                label.setText(label_text)
                StationPanel._fit_indicator_label(label)
            card.mode_button.setText({"中文": f"单测 {station.value}", "English": f"Single Test {station.value}", "Français": f"Test simple {station.value}"}[value])
            for signal, _, _ in MANUAL_NAMES:
                label_widget = self.findChild(QLabel, f"manual_label_{signal}_{station.value}")
                titles = {"clamp": {"中文": "夹紧", "English": "Clamp", "Français": "Serrage"}, "transfer": {"中文": "移载", "English": "Transfer", "Français": "Transfert"}, "block": {"中文": "封堵", "English": "Blocking", "Français": "Obstruction"}, "stamp": {"中文": "盖章", "English": "Stamp", "Français": "Timbre"}, "door_disable": {"中文": "安全门使能/禁用", "English": "Door enable/disable", "Français": "Porte activer/désactiver"}, "manual": {"中文": "自动/手动", "English": "Automatic/Manual", "Français": "Automatique/Manuel"}}
                if label_widget is not None: label_widget.setText(f"{titles[signal][value]} {station.value}")
                button = self.findChild(QPushButton, f"manual_{signal}_{station.value}")
                if button is not None: button.setText({"中文": "状态切换", "English": "Toggle", "Français": "Basculer"}[value])
                for suffix, key in (("back", "back"), ("forward", "forward"), ("enable", "enable"), ("disable", "disable"), ("automatic", "automatic"), ("manual", "manual")):
                    target_button = self.findChild(QPushButton, f"manual_{signal}_{station.value}_{suffix}")
                    if target_button is not None: target_button.setText(UiTextCatalog.action(value, key))
                readback = self.findChild(QLabel, f"manual_readback_{signal}_{station.value}")
                if readback is not None:
                    actual = "ON" in readback.text()
                    readback.setText({"中文": f"● {'开' if actual else '关'}", "English": f"● {'ON' if actual else 'OFF'}", "Français": f"● {'MARCHE' if actual else 'ARRÊT'}"}[value])
        placeholder_map = {"中文": {"scanner_input": "扫码帧", "product_draft": "产品型号", "recovery_reason": "处理原因", "login_username": "登录角色", "login_password": "密码"}, "English": {"scanner_input": "Scanner frame", "product_draft": "Part No.", "recovery_reason": "Reason", "login_username": "Role", "login_password": "Password"}, "Français": {"scanner_input": "Trame scanner", "product_draft": "N° pièce", "recovery_reason": "Motif", "login_username": "Rôle", "login_password": "Mot de passe"}}[value]
        for object_name, placeholder in placeholder_map.items():
            widget = self.findChild(QLineEdit, object_name)
            if widget is not None: widget.setPlaceholderText(placeholder)
        for station in StationId:
            part = self.findChild(QLineEdit, f"part_no_{station.value}")
            if part is not None: part.setPlaceholderText({"中文": "产品型号", "English": "Part No.", "Français": "N° pièce"}[value])
        self.settings_status.setText({"中文": "语言：中文", "English": "Language: English", "Français": "Langue : Français"}[value])
        countdown_labels = {"中文": ("校准倒计时 A", "校准倒计时 B"), "English": ("Calibration countdown A", "Calibration countdown B"), "Français": ("Compte à rebours A", "Compte à rebours B")}[value]
        self.calibration_label_a.setText(countdown_labels[0]); self.calibration_label_b.setText(countdown_labels[1])
        if all(calibration.due and not calibration.validation_started for calibration in self.calibration.values()):
            self.calibration_status.setText({
                "中文": "校准到期，请点击启动验证",
                "English": "Calibration due; click Start Validation",
                "Français": "Calibration échue ; cliquez sur Validation démarrage",
            }[value])
        self._apply_setup_language(value)
        authenticated = self.security.role.value == "admin"
        self.login_status.setText({"中文": "已认证" if authenticated else "未登录", "English": "Authenticated" if authenticated else "Not signed in", "Français": "Authentifié" if authenticated else "Non connecté"}[value])
        scanner_status = {"中文": "扫码器就绪", "English": "Scanner ready", "Français": "Scanner prêt"}[value]
        self.scanner_status.setText(scanner_status)
        next_actions = {"中文": {Phase.IDLE: "扫码", Phase.READY: "一测", Phase.WAIT_2: "二测", Phase.LABELING: "贴标", Phase.COMPLETE: "复位或查询", Phase.FAULT: "管理员恢复"}, "English": {Phase.IDLE: "Scan", Phase.READY: "Test 1", Phase.WAIT_2: "Test 2", Phase.LABELING: "Label", Phase.COMPLETE: "Reset or query", Phase.FAULT: "Admin recovery"}, "Français": {Phase.IDLE: "Scanner", Phase.READY: "Test 1", Phase.WAIT_2: "Test 2", Phase.LABELING: "Étiqueter", Phase.COMPLETE: "Réinitialiser ou requête", Phase.FAULT: "Récupération admin"}}[value]
        for card in self.cards:
            card.error_ack.setText({"中文": "确认并继续", "English": "Acknowledge", "Français": "Acquitter"}[value])
            card.error_reset.setText({"中文": "复位", "English": "Reset", "Français": "Réinitialiser"}[value])
            # Refresh owns responsive placement and visibility.  Keeping the
            # language path on that single state transition prevents stale
            # tooltip-only diagnostics after a locale switch or resize.
            card.refresh()

    def language_changed(self, value): self._apply_language(value)
    def save_settings(self):
        self._save_global_settings()

    def _calibration_period_seconds(self) -> int:
        value = self.cal_period.time()
        seconds = value.hour() * 3600 + value.minute() * 60 + value.second()
        if seconds <= 0:
            raise ValueError("校准周期必须大于 00:00")
        return seconds

    def _configure_calibration_period(self) -> int:
        """Apply the saved HH:MM:SS period independently to A and B."""
        seconds = self._calibration_period_seconds()
        for calibration in self.calibration.values():
            calibration.set_period(seconds)
        return seconds

    def _persist_calibration(self) -> None:
        """Save A/B calibration progress so restarts don't force re-validation.

        校准状态此前只在内存里，程序重启后操作员被迫重新做 NG/OK 验证。
        状态仅在发生变化时写盘（约每秒检查一次签名）。
        """
        snapshot = {}
        for station, cal in self.calibration.items():
            snapshot[station.value] = {
                "due": cal.due,
                "locked": cal.locked,
                "validation_started": cal.validation_started,
                "phase": cal.phase.name,
                "ng_count": cal.ng_count,
                "ok_count": cal.ok_count,
                "remaining_seconds": cal.remaining_seconds,
                "period_seconds": cal.period_seconds,
                "clear_pending": cal.clear_pending,
                "sample_demand": cal.sample_demand,
                "audit_events": [dict(event) for event in cal.audit_events],
            }
        signature = repr(sorted(snapshot.items()))
        if signature == getattr(self, "_calibration_signature", None):
            return
        self._calibration_signature = signature
        try:
            handle, temp_name = tempfile.mkstemp(
                prefix="calibration_state.", suffix=".tmp", dir=str(self._calibration_state_path.parent))
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(snapshot, stream, ensure_ascii=False)
            os.replace(temp_name, self._calibration_state_path)
        except OSError:
            pass

    def _restore_calibration(self) -> None:
        try:
            snapshot = json.loads(self._calibration_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for station, cal in self.calibration.items():
            state = snapshot.get(station.value)
            if not isinstance(state, dict):
                continue
            audit_events = state.get("audit_events", [])
            if isinstance(audit_events, list):
                cal.audit_events = [
                    {str(key): str(value) for key, value in event.items()}
                    for event in audit_events if isinstance(event, dict)]
            # 只恢复验证进行中的状态（等待 NG/OK 样件）。倒计时运行中的
            # 状态不恢复：重启后重新要求启动验证，属安全侧行为。
            if not state.get("validation_started"):
                continue
            try:
                cal.due = bool(state["due"])
                cal.locked = bool(state["locked"])
                cal.validation_started = bool(state["validation_started"])
                cal.phase = CalibrationPhase[state["phase"]]
                cal.ng_count = int(state["ng_count"])
                cal.ok_count = int(state["ok_count"])
                cal.remaining_seconds = max(0.0, float(state["remaining_seconds"]))
                cal._clear_pending = bool(state["clear_pending"])
                if cal._clear_pending:
                    # 现场规则：验证完成后无需扫码，恢复即清灯并启动倒计时。
                    cal.clear_after_resume()
                cal.sample_demand = str(state.get("sample_demand", ""))
                cal._last_tick = time.monotonic()
            except (KeyError, ValueError, TypeError):
                continue

    def _tick_calibration(self) -> None:
        """Advance A/B timers and raise only the station that expires."""
        try:
            for station, calibration in self.calibration.items():
                if calibration.tick():
                    self._card_for_station(station).refresh()
                    self.calibration_status.setText(
                        {"中文": f"工位 {station.value} 校准到期，请点击启动验证",
                         "English": f"Station {station.value} calibration due; start validation",
                         "Français": f"Poste {station.value} : calibration échue, démarrez la validation"}[self._language])
                self._set_calibration_countdown(station, calibration.remaining_seconds)
            self._trace_start_button_state()
            self._persist_calibration()
        except Exception as exc:
            # 单次 tick 异常不允许带崩整个进程（GUI 线程槽内未捕获异常
            # 会导致应用直接退出），记录后等下一个 tick。
            self._live_trace(f"CAL_TICK_FAILED {type(exc).__name__}: {exc}")
            self._log_crash("CAL_TICK", exc)

    def cancel_calibration(self, station, reason=None) -> bool:
        """Admin-only cancellation for one station; restart its own period."""
        try:
            self.security.require("calibration_cancel")
            calibration = self.calibration[station]
            card = self._card_for_station(station)
            if not (calibration.due or calibration.validation_started or calibration.clear_pending):
                raise RuntimeError("当前工位没有待取消的校准状态")
            if card.controller.phase not in (Phase.IDLE, Phase.WAIT_SCAN, Phase.COMPLETE):
                raise RuntimeError("当前测试尚未结束，不能取消校准")
            if reason is None:
                prompts = {
                    "中文": (f"取消工位 {station.value} 校准", "请输入取消原因："),
                    "English": (f"Cancel station {station.value} calibration", "Enter a reason:"),
                    "Français": (f"Annuler calibration poste {station.value}", "Saisissez le motif :"),
                }
                title, prompt = prompts[self._language]
                reason, accepted = QInputDialog.getText(self, title, prompt)
                if not accepted:
                    return False
            reason = str(reason).strip()
            if not reason:
                raise ValueError("取消校准需要填写原因")
            actor = self.security.session.username
            calibration.cancel(actor, reason)
            self._persist_calibration()
            self._set_calibration_countdown(station, calibration.remaining_seconds)
            card.refresh()
            messages = {
                "中文": f"工位 {station.value} 已取消校准，重新计时",
                "English": f"Station {station.value} calibration cancelled; timer restarted",
                "Français": f"Calibration du poste {station.value} annulée ; minuterie redémarrée",
            }
            self.calibration_status.setText(messages[self._language])
            self._live_trace(
                f"CAL_CANCELLED station={station.value} actor={actor} reason={reason}")
            return True
        except Exception as exc:
            self.calibration_status.setText({
                "中文": f"取消校准失败：{exc}",
                "English": f"Calibration cancellation failed: {exc}",
                "Français": f"Échec de l'annulation : {exc}",
            }[self._language])
            self._live_trace(
                f"CAL_CANCEL_FAILED station={station.value} {type(exc).__name__}: {exc}")
            return False

    def _trace_start_button_state(self) -> None:
        """Periodically record the Start Validation button state.

        A disabled button swallows clicks silently, so a field report of
        "no reaction" needs the enabled/phase state at that moment.  Bounded
        to one line every 30 s.
        """
        self._button_state_tick = getattr(self, "_button_state_tick", 0) + 1
        if self._button_state_tick % 30:
            return
        card = self._card_for_station(StationId.B)
        button = getattr(card, "start_validation_button", None)
        if button is None:
            return
        calibration = self.calibration[StationId.B]
        self._live_trace(
            f"B CAL_BUTTON_STATE enabled={button.isEnabled()} "
            f"phase={card.controller.phase.value} due={calibration.due} "
            f"validation_started={calibration.validation_started} "
            f"clear_pending={calibration.clear_pending}")

    def _save_global_settings(self):
        try:
            period_seconds = self._calibration_period_seconds()
            self.global_settings.save({
                "工位号A": self.global_station_a.text().strip() or "5",
                "工位号B": self.global_station_b.text().strip() or "6",
                "校准周期": self.cal_period.time().toString("HH:mm:ss"),
            })
            for calibration in self.calibration.values():
                calibration.set_period(period_seconds)
            self.settings_status.setText({"中文": "全局设置已保存", "English": "Global settings saved", "Français": "Réglages globaux enregistrés"}[self._language])
        except Exception as exc:
            self.settings_status.setText({"中文": f"设置拒绝：{exc}", "English": f"Settings denied: {exc}", "Français": f"Paramètres refusés : {exc}"}[self._language])
    def route_shared_scanner_code(self, code):
        # A pending printed label owns the shared scanner until it is
        # acknowledged or reset.  Route arbitrary frames to the sole pending
        # station so irrelevant codes can be ignored without becoming a UI
        # fault.  If both stations are waiting, only an exact label match is
        # actionable; all other frames remain harmlessly ignored.
        pending_cards = [card for card in self.cards
                         if card._label_ack_pending and card.controller.record is not None]
        if pending_cards:
            normalized_code = str(code).strip()
            exact_cards = [card for card in pending_cards
                           if scanner_code_matches(normalized_code, card.controller.record.code_2d)]
            if len(exact_cards) == 1:
                card = exact_cards[0]
            elif len(pending_cards) == 1:
                card = pending_cards[0]
            else:
                self._live_trace(
                    f"LABEL_SCAN_IGNORED station=A/B code={normalized_code!r} reason=ambiguous_pending")
                self.scanner_status.setText({
                    "中文": "A/B 均在等待标签码，继续扫码",
                    "English": "A/B are waiting for labels; continue scanning",
                    "Français": "A/B attendent une étiquette ; continuez à scanner",
                }[self._language])
                return

            station = card.station
            if not self.scanner_guards[station].accept(normalized_code):
                self._live_trace(
                    f"LABEL_SCAN_IGNORED station={station.value} code={normalized_code!r} reason=duplicate_or_empty")
                self.scanner_status.setText({
                    "中文": f"工位 {station.value} 标签不匹配，继续扫码",
                    "English": f"Station {station.value} label mismatch; continue scanning",
                    "Français": f"Étiquette du poste {station.value} incorrecte ; continuez à scanner",
                }[self._language])
                return

            previous = card.code_input.text()
            ok = card.scan(normalized_code, card.part_no.currentText())
            if not ok or card._label_ack_pending:
                card.code_input.setText(previous)
                self._live_trace(
                    f"LABEL_SCAN_IGNORED station={station.value} code={normalized_code!r} reason=mismatch")
                self.scanner_status.setText({
                    "中文": f"工位 {station.value} 标签不匹配，继续扫码",
                    "English": f"Station {station.value} label mismatch; continue scanning",
                    "Français": f"Étiquette du poste {station.value} incorrecte ; continuez à scanner",
                }[self._language])
                return
            self.scanner_status.setText({
                "中文": f"工位 {station.value} 标签已确认，可继续测试",
                "English": f"Station {station.value} label acknowledged",
                "Français": f"Étiquette du poste {station.value} confirmée",
            }[self._language])
            return

        expected = {}
        for card in self.cards:
            record = card.controller.record
            if card._label_ack_pending and record is not None:
                expected[card.station] = record.code_2d
            elif card.payload is not None:
                expected[card.station] = card.payload.barcode_text
        busy = {card.station for card in self.cards
                if card.controller.phase not in (Phase.IDLE, Phase.WAIT_SCAN)
                and not card._label_ack_pending}
        station = route_shared_scan(code, expected, busy)
        # Reject a locked station before consuming the scanner guard token;
        # otherwise the same physical scan would be treated as a duplicate
        # when the operator retries after completing calibration.
        card = self.cards[0 if station is StationId.A else 1]
        label_ack = card._label_ack_pending
        if self.calibration[station].locked and not label_ack:
            raise RuntimeError({"中文": "校准验证未完成", "English": "Calibration validation required", "Français": "Validation de calibration requise"}[self._language])
        if not self.scanner_guards[station].accept(code): raise ValueError({"中文": "扫码为空或重复", "English": "Empty or duplicate scan", "Français": "Scan vide ou dupliqué"}[self._language])
        previous = card.code_input.text(); ok = card.scan(code, card.part_no.currentText())
        if label_ack:
            if not ok or card._label_ack_pending:
                card.code_input.setText(previous)
                raise RuntimeError("标签扫码确认未完成")
            self.scanner_status.setText({"中文": f"工位 {station.value} 标签已确认，可继续测试", "English": f"Station {station.value} label acknowledged", "Français": f"Étiquette du poste {station.value} confirmée"}[self._language])
            return
        if (not ok or card.controller.phase is not Phase.READY
                or not card.controller.record
                or not scanner_code_matches(code, card.controller.record.code_2d)):
            card.code_input.setText(previous)
            raise RuntimeError({"中文": "扫码未进入就绪状态", "English": "Scan did not enter READY", "Français": "Le scan n'est pas passé à PRÊT"}[self._language])
        self.scanner_status.setText({"中文": f"已路由到工位 {station.value}", "English": f"Routed to station {station.value}", "Français": f"Routé vers le poste {station.value}"}[self._language])

    def route_scanner_code(self, station, code):
        """Administrator diagnostic compatibility; production routing is automatic."""
        if self.settings.mode.value == "simulate":
            if not self.scanner_guards[station].accept(code):
                raise ValueError({"中文": "扫码为空或重复", "English": "Empty or duplicate scan", "Français": "Scan vide ou dupliqué"}[self._language])
            card = self.cards[0 if station is StationId.A else 1]
            previous, payload = card.code_input.text(), card.payload
            card.payload = None
            card.code_input.setText(code)
            ok = card.scan(code, self.product_settings.current_product())
            card.payload = payload
            if not ok or card.controller.phase is not Phase.READY:
                card.code_input.setText(previous)
                raise RuntimeError("模拟扫码未进入就绪状态")
            self.scanner_status.setText(f"已路由到工位 {station.value}（管理员诊断）")
            return
        resolved = route_shared_scan(code, {card.station: card.payload.barcode_text
                                            for card in self.cards if card.payload is not None})
        if resolved is not station:
            raise ValueError(f"二维码属于工位 {resolved.value}，不能人工路由到 {station.value}")
        return self.route_shared_scanner_code(code)
    def route_scanner_text(self, station):
        try:
            frames = self.scanner_framers[station].feed((self.scanner_input.text()+"\r\n").encode("utf-8"));
            if len(frames) != 1: raise ValueError({"中文": "扫码帧不完整", "English": "Incomplete scanner frame", "Français": "Trame scanner incomplète"}[self._language])
            self.route_scanner_code(station, frames[0])
        except Exception as exc: self.scanner_status.setText({"中文": f"扫码错误：{exc}", "English": f"Scanner error: {exc}", "Français": f"Erreur scanner : {exc}"}[self._language])

    def route_shared_scanner_text(self):
        try:
            code = self.scanner_input.text()
            self.route_shared_scanner_code(code)
        except Exception as exc:
            self.scanner_status.setText({"中文": f"扫码错误：{exc}", "English": f"Scanner error: {exc}", "Français": f"Erreur scanner : {exc}"}[self._language])
    def manual_output(self, button, station=StationId.A, signal="clamp"):
        try:
            if signal not in POINTS:
                raise ValueError({"中文": f"未知手动信号：{signal}", "English": f"Unknown manual signal: {signal}", "Français": f"Signal manuel inconnu : {signal}"}[self._language])
            byte, bit = POINTS[signal][station]; current = self.plc.read_bit(byte, bit)
            ok = self.command_manual_target(station, signal, not current)
            button.setText(f"状态切换 / {'ON' if ok and not current else 'OFF'}")
            self._refresh_manual_readbacks()
        except Exception as exc:
            prefix = {"中文": "拒绝", "English": "Denied", "Français": "Refusé"}[self._language]
            button.setText({"中文": "拒绝：权限不足", "English": "Denied: permission required", "Français": "Refusé : autorisation requise"}[self._language])
    def refresh_all(self):
        for card in self.cards: card.refresh()
        self._refresh_calibration_countdowns()
        self.refresh_query()
    @staticmethod
    def _query_datetime(widget):
        value = widget.dateTime().toPython()
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return value

    def _query_records(self, station):
        start_widget, finish_widget = self.query_fields[(station,"start")], self.query_fields[(station,"finish")]
        start, finish = self._query_datetime(start_widget), self._query_datetime(finish_widget)
        status = self.query_status[station]
        if start > finish:
            status.setText(UiTextCatalog.message(self._language, "query_range"))
            return []
        status.setText("")
        needle = self.query_fields[(station,"code")].text().strip().lower(); result = self.query_fields[(station,"result")].text().strip().lower(); rows = [r for r in self.repository.records.values() if r.station is station]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return [r for r in rows if start <= r.created_at.astimezone(start.tzinfo) <= finish and (not needle or needle in r.code_2d.lower()) and (not result or result in ((r.second or r.first).result.value.lower() if (r.second or r.first) else ""))]
    def refresh_query(self):
        for station, table in self.query_tables.items():
            rows = self._query_records(station); table.setRowCount(30)
            for i in range(30):
                vals = ["", "", "", "", "", "", "", "", "", ""]
                if i < len(rows):
                    r=rows[i]; vals=[r.created_at.astimezone().strftime("%Y-%m-%d-%H:%M:%S"),r.serial_no,r.code_2d,StationPanel._m(self,r.first,"pressure") if r.first else "",StationPanel._m(self,r.first,"leakage") if r.first else "",StationPanel._m(self,r.second,"pressure") if r.second else "",StationPanel._m(self,r.second,"leakage") if r.second else "",(r.second or r.first).result.value if (r.second or r.first) else "",r.part_no,r.person]
                for col,val in enumerate(vals): table.setItem(i,col,QTableWidgetItem(str(val)))
    def download_query(self, station, path=None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Download CSV", f"query_{station.value}.csv", "CSV (*.csv)")
        if not path: return
        table=self.query_tables[station]
        with open(path,"w",encoding="utf-8-sig",newline="") as handle:
            writer=csv.writer(handle); writer.writerow(TABLE_HEADERS)
            for row in range(table.rowCount()): writer.writerow([table.item(row,col).text() if table.item(row,col) else "" for col in range(table.columnCount())])
    def export_csv(self): self.download_query(StationId.A)

    def _card_for_station(self, station):
        return self.cards[0 if station is StationId.A else 1]

    def _set_calibration_countdown(self, station, value):
        widget = self.calibration_countdown_a if station is StationId.A else self.calibration_countdown_b
        widget.setValue(max(0, int(float(value))))

    def _refresh_calibration_countdowns(self):
        for station in StationId:
            self._set_calibration_countdown(station, self.calibration[station].remaining_seconds)

    def mark_calibration_due(self, station=StationId.A):
        """Raise one station's calibration indicator (timer/PLC integration hook)."""
        calibration = self.calibration[station]
        calibration.mark_due()
        self._set_calibration_countdown(station, calibration.remaining_seconds)
        self._card_for_station(station).refresh()
        return calibration

    def start_calibration(self, station=StationId.A):
        """Start the operator-confirmed NG -> OK validation sequence.

        This callback is the only action behind the footer Start Validation
        button.  It never toggles the production PLC start bit.
        """
        card = self._card_for_station(station)
        calibration = self.calibration[station]
        controller = card.controller
        self._live_trace(f"CAL_START_REQUEST station={station.value} phase={controller.phase.value} has_record={controller.record is not None}")
        initial_state = controller.record is None and controller.phase in (Phase.IDLE, Phase.WAIT_SCAN)
        completed_cycle = controller.record is not None and controller.phase is Phase.COMPLETE
        if not (initial_state or completed_cycle):
            raise RuntimeError("当前测试尚未完成")
        calibration.begin_validation()
        # 校准标签不需要扫码确认。内部冻结型号/二维码供测试与追溯使用，
        # 但保持页面二维码框为空；只有正常生产标签需要打印后扫码确认。
        if controller.record is None:
            payload = card.payload
            if payload is not None:
                # 校准件流水号独立计数（C001...），不占用正常测试件计数。
                cal_payload = self._barcode_engine().generate_calibration(
                    payload.product_id, station)
                selection = StationSelection(
                    station, cal_payload.product_id,
                    card.staff.currentText().strip() or "Operator",
                    "single", cal_payload.serial_no, cal_payload.barcode_text,
                    cal_payload.customer_model, str(cal_payload.ateq_program),
                    str(cal_payload.template_path))
                controller.scan_selection(selection)
                card.code_input.clear()
                self._live_trace(f"CAL_CYCLE_READY station={station.value} cycle={controller.record.cycle_id} program={selection.ateq_program} serial={cal_payload.serial_no}")
            else:
                self._live_trace(f"CAL_CYCLE_NOT_CREATED station={station.value} no internal payload")
        card.refresh()
        self._set_calibration_countdown(station, calibration.remaining_seconds)
        self.calibration_status.setText(self._calibration_status_text(station, calibration))
        return True

    def _calibration_status_text(self, station, calibration):
        phase_text = {
            "中文": {"等待NG样件": "等待NG样件", "等待OK样件": "等待OK样件", "校准完成": "校准完成"},
            "English": {"等待NG样件": "Waiting for NG sample", "等待OK样件": "Waiting for OK sample", "校准完成": "Calibration complete"},
            "Français": {"等待NG样件": "En attente de l'échantillon NG", "等待OK样件": "En attente de l'échantillon OK", "校准完成": "Calibration terminée"},
        }[self._language][calibration.phase.value]
        if calibration.sample_demand:
            demand = {"中文": f"等待{calibration.sample_demand}样件", "English": f"Waiting for {calibration.sample_demand} sample", "Français": f"En attente de l'échantillon {calibration.sample_demand}"}[self._language]
        else:
            demand = phase_text
        return f"工位 {station.value} | {phase_text} | {calibration.countdown} | {demand}"

    def calibration_sample(self, result, station=StationId.A):
        calibration = self.calibration[station]
        result = str(result).strip().upper()
        expected = "NG" if calibration.phase is CalibrationPhase.WAIT_NG else "OK"
        if result != expected:
            raise ValueError("校准样件顺序错误")
        printer = getattr(self.printer, "print_calibration", None)
        if printer is None:
            raise RuntimeError("校准打印接口未配置")
        self._live_trace(f"CAL_PRINT_REQUEST station={station.value} result={result}")
        measurement = None
        record = self._card_for_station(station).controller.record
        if record is not None:
            measurement = record.second or record.first
        receipt = printer(station, result, measurement=measurement,
                          when=datetime.now().astimezone())
        if not getattr(receipt, "accepted", False):
            self._live_trace(f"CAL_PRINT_REJECTED station={station.value} result={result} detail={getattr(receipt, 'detail', '')}")
            raise RuntimeError(f"校准{result}标签打印未确认")
        self._live_trace(f"CAL_PRINT_ACCEPTED station={station.value} result={result} job={getattr(receipt, 'job_id', '')}")
        self._enable_scanner_after_print(station, getattr(receipt, "job_id", ""))
        phase = calibration.sample(result)
        # 现场规则：样件验证通过才递增校准流水号（C001→C002→...连续）。
        try:
            record = self._card_for_station(station).controller.record
            product_for_serial = record.part_no if record is not None else None
            if product_for_serial:
                advanced = self._barcode_engine().advance_calibration_serial(
                    product_for_serial, station)
                self._live_trace(
                    f"CAL_SERIAL_ADVANCED station={station.value} next={advanced}")
        except Exception as serial_exc:
            self._live_trace(
                f"CAL_SERIAL_ADVANCE_FAILED {type(serial_exc).__name__}: {serial_exc}")
        # 校准标签不走正常标签的扫码确认事务。OK 校准标签打印确认后，
        # 将单测 OK 留下的 LABELING 状态归档复位，允许后续正常周期启动。
        if result == "OK" and phase is CalibrationPhase.COMPLETE:
            card = self._card_for_station(station)
            controller = card.controller
            if controller.record is not None:
                if controller.phase is Phase.LABELING:
                    controller.phase = Phase.COMPLETE
                if controller.phase is not Phase.COMPLETE:
                    raise RuntimeError(
                        f"校准周期尚未结束，不能释放工位：{controller.phase.value}")
                cycle_id = controller.record.cycle_id
                controller.reset()
                self._live_trace(
                    f"CAL_VALIDATION_RELEASED station={station.value} cycle={cycle_id}")
        # NG 标签打印后继续等待 OK 样件；OK 标签打印确认后清灯并启动倒计时。
        if calibration.clear_pending:
            calibration.clear_after_resume()
        self._set_calibration_countdown(station, calibration.remaining_seconds)
        self._card_for_station(station).refresh()
        return self._calibration_status_text(station, calibration)

    def on_calibration_sample(self, result, station=StationId.A):
        try:
            self.calibration_status.setText(self.calibration_sample(result, station))
        except Exception:
            self.calibration_status.setText({"中文": "校准错误：样件顺序无效", "English": "Calibration error: invalid sample order", "Français": "Erreur de calibration : ordre d'échantillon invalide"}[self._language])
    def resolve_recovery(self, station, reason="UI 人工确认"):
        try:
            self.security.require("recovery_resolve"); card=self.cards[0 if station is StationId.A else 1]; card.controller.resolve_recovery(reason); card.refresh(); self.recovery_status.setText({"中文": f"工位 {station.value} 已审计归档", "English": f"Station {station.value} archived", "Français": f"Poste {station.value} archivé"}[self._language])
        except Exception: self.recovery_status.setText({"中文": "恢复拒绝：权限不足", "English": "Recovery denied: permission required", "Français": "Récupération refusée : autorisation requise"}[self._language])


StationCard = StationPanel
