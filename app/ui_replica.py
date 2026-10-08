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

from .models import Phase, StationId, Result, CycleSelection
from .ateq import FakeAteq, SerialAteq
from .config import Settings
from .journal import CycleJournal
from .license import LicenseVerifier
from .permissions import AuthSession, SecurityContext
from .plc import FakePlc, Snap7Plc
from .composition import build_plc, build_weight_service, start_relay_service
from .laser import FakeMarker, LaserMarker, LaserFileWriter, LaserChannel
from .points import sim_point_map
from .composition import build_point_map, build_date_code_fn
from .repository import FakeRepository, PyMySQLRepository
from .station import StationController
from .calibration import Calibration, CalibrationPhase
from .settings_service import ProductSettingsService
from .model_settings import (DATE_SCHEME_PRESETS,
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
TABLE_HEADERS = ["Time / Heure", "Part No. / N° pièce", "#1 Pressure / Pression", "#1 Leakage / Fuite", "#2 Pressure / Pression", "#2 Leakage / Fuite", "Result / Résultat", "Marked / 打码", "Staff / Personnel", "Cycle ID", "Daily Seq / 当日序号"]
DISPLAY_HEADERS = {
    "base": ["Time\n时间", "Part\nNo.", "#1\nPress.", "#1\nLeak.", "#2\nPress.", "#2\nLeak.", "Result\nOK·NG", "Marked\n打码", "Staff\n人员", "Cycle\nID", "当日序号\nSeq"],
    "中文": ["时间", "产品型号", "一测压力", "一测泄漏", "二测压力", "二测泄漏", "结果", "打码", "人员", "周期号", "当日序号"],
    "English": ["Time", "Part\nNo.", "#1\nPress.", "#1\nLeak.", "#2\nPress.", "#2\nLeak.", "Result", "Marked", "Staff", "Cycle\nID", "Daily Seq"],
    "Français": ["Heure", "N°\npièce", "Press.\n#1", "Fuite\n#1", "Press.\n#2", "Fuite\n#2", "Résultat", "Marqué", "Pers.", "Cycle", "Séq. jour"],
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

    def __init__(self, station, repository, marker, plc, journal, security,
                 product_provider, confirm_callback, changed_callback,
                 model_provider=None, personnel_provider=None,
                 calibration_provider=None, calibration_start_callback=None,
                 ateq=None, point_map=None):
        super().__init__()
        self.station, self.repository, self.plc = station, repository, plc
        self.point_map = point_map if point_map is not None else sim_point_map()
        self.security, self.confirm_callback = security, confirm_callback
        self.changed_callback = changed_callback
        self.product_provider = product_provider
        self.model_provider = model_provider
        self.personnel_provider = personnel_provider
        self.calibration_provider = calibration_provider
        self.calibration_start_callback = calibration_start_callback
        self.test_finished.connect(self._finish_test)
        self.model_config = None
        self._error_key = None
        self._pressure_alarm_active = False
        self.controller = StationController(station, repository, marker, ateq or FakeAteq(), journal,
            safe_stop=plc, license_status=security.license_status, security=security)
        self.stepcode_updated.connect(self._apply_stepcode_display)
        if hasattr(self.controller.ateq, "stepcode_callback"):
            # 显示端信号为 str；适配器回调传整数，这里显式转换——否则 emit
            # 类型异常会被监视循环的兜底 except 吞掉，测试期间步骤码不刷新。
            self.controller.ateq.stepcode_callback = (
                lambda code: self.stepcode_updated.emit(str(code)))
        if hasattr(self.controller.ateq, "step5_check"):
            self.controller.ateq.step5_check = self._positive_hold_guard
        if hasattr(self.controller.ateq, "abort_check"):
            self.controller.ateq.abort_check = self._plc_reset_abort_check
        self._pressure_trip_seconds = 2.0
        self._pressure_window_seconds = 5.0
        self._pressure_on_seconds = 1.0
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
        self.mode_button = QPushButton(f"Single Test / 单测 {station.value}"); self.mode_button.setObjectName(f"single_dual_{station.value}"); self.mode_button.setCheckable(True); self.mode_button.toggled.connect(self._mode_changed); self.mode_button.setChecked(True); top.addWidget(QLabel(f"Single/Dual {station.value}"), 0, 0); top.addWidget(self.mode_button, 1, 0)
        self.total_today = QSpinBox(); self.total_today.setObjectName(f"total_today_{station.value}"); self.total_today.setReadOnly(True); self.total_today.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons); self.total_today.setFixedWidth(72); top.addWidget(QLabel(f"Total Today {station.value}"), 0, 1); top.addWidget(self.total_today, 1, 1)
        self.ok_today = QSpinBox(); self.ok_today.setObjectName(f"ok_today_{station.value}"); self.ok_today.setReadOnly(True); self.ok_today.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons); self.ok_today.setFixedWidth(72); top.addWidget(QLabel(f"OK Today {station.value}"), 0, 2); top.addWidget(self.ok_today, 1, 2)
        self.ateq_no = QLineEdit("SIM"); self.ateq_no.setObjectName(f"ateq_no_{station.value}"); self.ateq_no.setReadOnly(True); self.ateq_no.setMaxLength(3); self.ateq_no.setFixedWidth(64); top.addWidget(QLabel(f"ATEQ No. {station.value}"), 0, 3); top.addWidget(self.ateq_no, 1, 3)
        self.part_no = QComboBox(); self.part_no.setObjectName(f"part_no_{station.value}"); self.part_no.currentTextChanged.connect(self._product_changed); top.addWidget(QLabel(f"Part No. {station.value}"), 0, 4); top.addWidget(self.part_no, 1, 4)
        self.staff = QComboBox(); self.staff.setObjectName(f"staff_{station.value}"); self.staff.currentTextChanged.connect(self._staff_changed); top.addWidget(QLabel(f"Staff {station.value}"), 0, 5); top.addWidget(self.staff, 1, 5)
        for column in range(6):
            top.setColumnMinimumWidth(column, 0); top.setColumnStretch(column, 1)
        # Compact numeric fields leave the reclaimed width to the Part No. field.
        top.setColumnStretch(4, 3)
        for index in range(top.count()):
            child = top.itemAt(index).widget()
            if child is not None:
                child.setMinimumWidth(0)
                if isinstance(child, QLabel):
                    child.setWordWrap(True); child.setMaximumWidth(110)
        outer.addLayout(top)
        alerts = QHBoxLayout(); alerts.setSpacing(8); self.reprint = QPushButton(f"重打码 {station.value}"); self.reprint.setObjectName(f"reprint_{station.value}"); self.reprint.setProperty("compact", True); self.reprint.setFixedHeight(32); self.reprint.clicked.connect(self.remark); alerts.addWidget(self.reprint)
        self.pressure_alarm_label = QLabel(); self.pressure_alarm_label.setObjectName(f"pressure_alarm_{station.value}"); self.pressure_alarm_label.setProperty("state", "ng"); self.pressure_alarm_label.setWordWrap(True); self.pressure_alarm_label.setVisible(False); alerts.addWidget(self.pressure_alarm_label)
        alerts.addStretch(1); outer.addLayout(alerts)
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
        for signal, label in (("laser_start", "激光启动 / Laser"), ("door_disable", "安全门 / Door"), ("pressure", "正/负压 / Pressure"), ("manual", "手动 / Manual")):
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

    def _apply_mode_caption(self, language: str) -> None:
        """按当前勾选状态显示单/双测；语言刷新不得把文字写成固定的"单测"。"""
        dual = self.mode_button.isChecked()
        labels = {
            "中文": ("双测", "单测"),
            "English": ("Dual Test", "Single Test"),
            "Français": ("Test double", "Test simple"),
        }.get(language, ("双测", "单测"))
        self.mode_button.setText(f"{labels[0] if dual else labels[1]} {self.station.value}")

    def _mode_changed(self, dual):
        self._apply_mode_caption(getattr(self.window(), "_language", "中文"))
        self._product_changed()

    def _staff_changed(self, *_args):
        """人员切换只影响下一个周期的冻结数据，与 ATEQ 程序无关。"""
        return

    def _product_changed(self, *_args):
        if not hasattr(self, "part_no") or not self.model_provider or not self.part_no.currentText().strip():
            return
        try:
            self.model_config = self.model_provider(self.part_no.currentText().strip())
            self.ateq_no.setText(str(self.model_config.ateq_program))
            if isinstance(self.controller.ateq, SerialAteq):
                if self.controller.phase is not Phase.IDLE:
                    # 周期进行中不写 ATEQ 程序（周期启动时会按冻结型号选择），
                    # 也不显示 ERR：界面上的型号变更不应打断运行中的周期。
                    trace = getattr(self.window(), "_live_trace", None)
                    if trace is not None:
                        trace(f"ATEQ_PROGRAM_SYNC_DEFERRED station={self.station.value} "
                              f"phase={self.controller.phase.value}")
                    self._error_key = None
                    return
                self.controller.ateq.select_program(str(self.model_config.ateq_program))
                actual_program = self.controller.ateq.current_program()
                if actual_program != int(self.model_config.ateq_program):
                    raise RuntimeError(
                        f"ATEQ 程序读回不一致：期望 {self.model_config.ateq_program}，实际 {actual_program}")
                self.ateq_no.setText(str(actual_program))
                trace = getattr(self.window(), "_live_trace", None)
                if trace is not None:
                    trace(f"ATEQ_PROGRAM_SYNC station={self.station.value} program={actual_program} product={self.part_no.currentText().strip()}")
            self._error_key = None
        except Exception as exc:
            self.model_config = None
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
    def _daily_sequence_map(rows):
        """按（本地日期, 产品型号）给记录编"当日序号"（每天每型号从 1 起）。

        序号由记录本身推导（按 created_at 升序），因此重启 UI、跨天、
        换型号都天然正确，且不需要数据库结构变更。超出 9999 按 9999 截断。
        """
        counters: dict[tuple[str, str], int] = {}
        mapping: dict[str, int] = {}
        for record in sorted(rows, key=lambda item: item.created_at):
            key = (record.created_at.astimezone().strftime("%Y%m%d"), record.part_no)
            counters[key] = counters.get(key, 0) + 1
            mapping[record.cycle_id] = counters[key]
        return mapping

    @staticmethod
    def _daily_sequence_text(record, sequence: int | None) -> str:
        if not sequence:
            return ""
        date_code = record.created_at.astimezone().strftime("%Y%m%d")
        return f"{date_code}{record.station.value}{min(int(sequence), 9999):04d}"

    def _stash_daily_sequence(self, record) -> None:
        """把"当日序号"写入记录，供打码文本第五行使用。

        与记录表"当日序号"列同源（按本地日期 + 产品型号，每天每型号从
        0001 起）；计算失败则留空，不影响打码主体字段。
        """
        try:
            sequences = self._daily_sequence_map(self._records())
            record.daily_sequence = self._daily_sequence_text(
                record, sequences.get(record.cycle_id))
        except Exception:
            record.daily_sequence = ""

    @staticmethod
    def _configure_table(table):
        header = table.horizontalHeader()
        # 列宽随内容自适应，配合像素级横向滚动：时间/二维码等字段完整显示，
        # 不再出现省略号截断。
        # 各列横向均分填满表格宽度（周期号不再独占剩余空间，也不会过窄）。
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(80)
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
        table = QTableWidget(30, 11); table.setObjectName(name); table.setHorizontalHeaderLabels(DISPLAY_HEADERS["base"]); table.setAlternatingRowColors(True); table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed); table.verticalHeader().setDefaultSectionSize(METRICS.table_row_height); table.verticalHeader().setMinimumSectionSize(METRICS.table_row_height); StationPanel._configure_table(table); [table.setRowHeight(i, METRICS.table_row_height) for i in range(30)]; table.setMinimumHeight(340); return table

    def _point_read(self, signal):
        byte, bit = self.point_map.address(signal); return self.plc.read_bit(byte, bit)

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

    def _plc_reset_abort_check(self) -> None:
        """PLC 面板复位（A=M0.0 / B=M0.3）请求期间：立即中止本工位监视。"""
        if getattr(self.window(), "_plc_reset_pending", False):
            raise RuntimeError("PLC 复位（面板按钮），终止监视")

    def _positive_hold_guard(self) -> None:
        """正压保压判定：StepCode=5 后的窗口内需确认开关 ON 持续 1 秒。

        现场约定（2026-10-08）：A 工位保压正常信号 = M886、异常终止输出 =
        M885；B 工位正常信号 = M887、异常终止输出 = M884。StepCode=5 刚出现
        时开关可能尚未闭合，因此在 ``_pressure_window_seconds``（5 秒）窗口
        内轮询开关：连续 ON 达到 ``_pressure_on_seconds``（1 秒）即判定正常
        并继续测试；整个窗口 ON 不足 1 秒（含全 OFF）则把终止输出置 1 保持
        2 秒并抛出异常中止本次监视（走既有故障/恢复路径）。
        仅 LIVE + 二次测试（正压）生效；读失败不误报。
        """
        if not getattr(self.window(), "live_mode", False):
            return
        if self.controller.phase is not Phase.TEST_2:
            return
        if not (self.point_map.has("pressure_alarm") and self.point_map.has("pressure_trip")):
            return
        trace = getattr(self.window(), "_live_trace", None)
        byte, bit = self.point_map.address("pressure_alarm")
        started = time.monotonic()
        deadline = started + self._pressure_window_seconds
        on_since: float | None = None
        on_for = 0.0
        normal = False
        while True:
            try:
                value = bool(self.plc.read_bit(byte, bit))
            except Exception as exc:
                if trace is not None:
                    trace(f"PRESSURE_SWITCH_READ_FAILED station={self.station.value} "
                          f"{type(exc).__name__}: {exc}")
                return
            now = time.monotonic()
            if value:
                if on_since is None:
                    on_since = now
                on_for = now - on_since
                if on_for >= self._pressure_on_seconds:
                    normal = True
                    break
            else:
                on_since = None
                on_for = 0.0
            if now >= deadline:
                break
            time.sleep(0.2)
        if normal:
            if trace is not None:
                trace(f"PRESSURE_SWITCH_OK station={self.station.value} step5 "
                      f"M{byte}.{bit}=1（ON 持续 {on_for:.1f}s / 窗口 "
                      f"{self._pressure_window_seconds:g}s）继续测试")
            return
        trip_byte, trip_bit = self.point_map.address("pressure_trip")
        if trace is not None:
            trace(f"PRESSURE_SWITCH_ABNORMAL station={self.station.value} step5 "
                  f"M{byte}.{bit}=0（{self._pressure_window_seconds:g}s 窗口内 ON 不足 "
                  f"{self._pressure_on_seconds:g}s）→ M{trip_byte}.{trip_bit} 置 1 保持 "
                  f"{self._pressure_trip_seconds:g} 秒并终止测试")
        try:
            self.plc.write_bit(trip_byte, trip_bit, True)
            time.sleep(self._pressure_trip_seconds)
            self.plc.write_bit(trip_byte, trip_bit, False)
        except Exception as exc:
            if trace is not None:
                trace(f"PRESSURE_TRIP_WRITE_FAILED station={self.station.value} "
                      f"{type(exc).__name__}: {exc}")
        raise RuntimeError(
            f"正压保压阶段压力开关异常（M{byte}.{bit}=0，{self._pressure_window_seconds:g} "
            f"秒窗口内 ON 不足 {self._pressure_on_seconds:g} 秒），终止测试")

    def set_pressure_alarm(self, active: bool) -> None:
        """PLC->PC 压力开关报警显示（只读轮询，不参与联锁）。"""
        active = bool(active)
        if active == self._pressure_alarm_active:
            return
        self._pressure_alarm_active = active
        self._refresh_pressure_alarm()
        trace = getattr(self.window(), "_live_trace", None)
        if trace is not None:
            trace(f"PRESSURE_ALARM station={self.station.value} active={active}")

    def _refresh_pressure_alarm(self) -> None:
        active = self._pressure_alarm_active
        self.pressure_alarm_label.setVisible(active)
        if active:
            language = getattr(self.window(), "_language", "中文")
            self.pressure_alarm_label.setText(
                UiTextCatalog.message(language, "pressure_alarm", station=self.station.value))
        indicator = self.indicators.get("pressure")
        if indicator is not None:
            indicator.setProperty("state", "ng" if active else "ok")
            indicator.style().unpolish(indicator)
            indicator.style().polish(indicator)

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
            return ({"中文": f"工位 {self.station.value}：验证完成",
                     "English": f"Station {self.station.value}: validation complete",
                     "Français": f"Poste {self.station.value} : validation terminée"}[language],
                    "warn",
                    {"中文": "验证完成", "English": "Validation\nComplete", "Français": "Validation\nterminée"}[language])
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
            byte, bit = self.point_map.address(signal); current = self.plc.read_bit(byte, bit); requested = not current
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
            # A normal production OK (both stages in dual mode) ends in
            # MARKING.  The operator-facing UI has no separate mark button,
            # so complete the controller marking transaction here.  Sample
            # cycles without marking never reach MARKING.
            if (calibration is None or not calibration.validation_started) \
                    and self.controller.phase is Phase.MARKING:
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
                        trace(f"{self.station.value} MARK_AUTO_REQUEST cycle={record.cycle_id}")
                    self.mark()
            self.refresh(); self.changed_callback()
        except Exception as exc:
            self._log_crash("FINISH_TEST", exc)
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None:
                trace(f"{self.station.value} FINISH_TEST_FAILED {type(exc).__name__}: {exc}")
        finally:
            # 测试期间收到 PLC 面板复位：等 worker 收尾后统一落地复位。
            window = self.window()
            if getattr(window, "_plc_reset_pending", False):
                window._apply_plc_reset()

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

    def mark(self):
        trace = getattr(self.window(), "_live_trace", None)
        if trace is not None:
            trace(f"{self.station.value} MARK_REQUEST cycle="
                 f"{getattr(self.controller.record, 'cycle_id', '')}")
        record = self.controller.record
        if record is not None:
            self._stash_daily_sequence(record)
        try:
            marked = self.controller.mark()
            if marked:
                self._error_key = None
                if trace is not None:
                    trace(f"{self.station.value} MARK_ACCEPTED job={self.controller.mark_job_id}")
            else:
                self._error_key = "mark_error"
                if trace is not None:
                    trace(f"{self.station.value} MARK_REJECTED")
        except Exception as exc:
            self._error_key = "mark_error"
            if trace is not None:
                trace(f"{self.station.value} MARK_FAILED {type(exc).__name__}: {exc}")
        self.refresh(); self.changed_callback()

    def remark(self):
        """Admin re-mark of the completed cycle (idempotent re-pulse)."""
        try:
            self.security.require("remark")
            if self.controller.record is None: raise RuntimeError("没有可重打码周期")
            self._stash_daily_sequence(self.controller.record)
            marked = self.controller.remark()
            if not marked:
                raise RuntimeError("重打码未确认")
            self._error_key = None
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None:
                trace(f"{self.station.value} MARK_REMARKED job={self.controller.mark_job_id}")
        except Exception: self._error_key = "remark_denied"
        self.refresh(); self.changed_callback()

    def reconnect_plc(self) -> None:
        """人工恢复动作后尽力重连 PLC 并恢复写权限。

        安全停止会断开适配器并关闭写门禁（fail-closed）；若不重连，站点在
        重启 UI 前无法继续工作。仅在操作员/管理员显式恢复动作后调用。
        """
        connect = getattr(self.plc, "connect", None)
        enable = getattr(self.plc, "enable_writes", None)
        try:
            if connect is not None:
                connect()
            if enable is not None:
                enable(True)
        except Exception as exc:
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None:
                trace(f"PLC_RECONNECT_FAILED station={self.station.value} "
                      f"{type(exc).__name__}: {exc}")
            return
        trace = getattr(self.window(), "_live_trace", None)
        if trace is not None:
            trace(f"PLC_RECONNECTED station={self.station.value}")

    def reset(self):
        try:
            if self.controller.recovery_required:
                # Reset is the operator's escape hatch from a failed cycle.
                # Keep the recovery journal/audit trail, but do not require a
                # second administrator permission check.
                self.controller.resolve_recovery(
                    f"UI reset {self.station.value}", require_permission=False)
            else:
                self.controller.reset()
            self.reconnect_plc()
            # Keep the simulation lifecycle marker, but do not disconnect a
            # live PLC on an operator reset.
            if isinstance(self.plc, FakePlc):
                self.plc.safe_stop(f"UI reset {self.station.value}")
            self._error_key = None
            # Reset is also the operator retry for a transient ATEQ/program
            # selection failure.  Re-read the model so a stale BLOCKED/BLO
            # display is not left behind.
            self._product_changed()
            if self.model_config is None and self.ateq_no.text() in ("ERR", "BLO", "BLOCKED"):
                self.ateq_no.setText("---")
            trace = getattr(self.window(), "_live_trace", None)
            if trace is not None and self.model_config is not None:
                trace(f"ATEQ_RESET_SYNC station={self.station.value} "
                      f"program={self.model_config.ateq_program}")
        except Exception: self._error_key = "reset_error"
        self.refresh(); self.changed_callback()

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
        # 压力开关报警只在正压 StepCode=5 期间有效，其余步骤立即熄灭。
        if value != "5":
            self.set_pressure_alarm(False)

    def refresh(self):
        c = self.controller; rows = self._records(); self.total_today.setValue(len(rows)); self.ok_today.setValue(sum(1 for r in rows if r.second and r.second.result is Result.OK))
        sequences = self._daily_sequence_map(rows)
        for row in range(30):
            values = ["", "", "", "", "", "", "", "", "", "", ""]
            if row < len(rows):
                record = rows[row]; values = [record.created_at.astimezone().strftime("%Y-%m-%d-%H:%M:%S"), record.part_no, self._m(record.first, "pressure"), self._m(record.first, "leakage"), self._m(record.second, "pressure"), self._m(record.second, "leakage"), (record.second or record.first).result.value if (record.second or record.first) else "", "√" if record.marked else "", record.person, record.cycle_id, self._daily_sequence_text(record, sequences.get(record.cycle_id))]
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
            values["start_validation"] = self._point_read("start") if self.point_map.has("start") else False
        language = getattr(self.window(), "_language", "中文")
        self._refresh_pressure_alarm()
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
            terminal_or_initial = ((c.record is None and c.phase is Phase.IDLE) or
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
            self.cancel_calibration_button.setVisible(is_admin)
            # 保持可点击：不满足取消条件时由点击处理给出明确原因
            #（避免按钮灰掉后操作员"点了没反应"）。
            self.cancel_calibration_button.setEnabled(is_admin)
        prompts = {"中文": {Phase.IDLE: "等待启动", Phase.READY: "一测", Phase.WAIT_2: "二测", Phase.MARKING: "打码", Phase.COMPLETE: "复位或查询", Phase.FAULT: "管理员恢复"}, "English": {Phase.IDLE: "Await start", Phase.READY: "Test 1", Phase.WAIT_2: "Test 2", Phase.MARKING: "Marking", Phase.COMPLETE: "Reset or query", Phase.FAULT: "Admin recovery"}, "Français": {Phase.IDLE: "Attente départ", Phase.READY: "Test 1", Phase.WAIT_2: "Test 2", Phase.MARKING: "Marquage", Phase.COMPLETE: "Réinitialiser ou requête", Phase.FAULT: "Récupération admin"}}
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

    def __init__(self, language: str = "中文", *, live: bool = False,
                 config_path: Path | None = None, preflight_passed: bool = False):
        super().__init__()
        if live and not preflight_passed:
            raise RuntimeError("LIVE_BLOCKED: live UI 需要预检全部通过并显式传入启动令牌")
        self.setWindowTitle("ATEQ F620 双腔气密检测 + 激光打码"); self.resize(METRICS.canonical_width, METRICS.canonical_height); self.setMinimumSize(1100, 700)
        app = QApplication.instance(); family = "Segoe UI"
        for font_path in (Path(r"C:\Windows\Fonts\Noto Sans SC (TrueType).otf"), Path(r"C:\Windows\Fonts\simsun.ttc")):
            if font_path.exists():
                fid = QFontDatabase.addApplicationFont(str(font_path)); families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
                if families: family = families[0]; break
        if app: app.setFont(QFont(family))
        self.setStyleSheet(stylesheet())
        selected_config = config_path or (Path(__file__).parents[1] / "config" / "default.toml")
        self.live_mode = bool(live)
        self.settings = Settings.from_toml(selected_config) if selected_config.exists() else Settings()
        self.station = self.settings.station
        self.live_trace_path = Path(r"D:\ATEQ\live_trace.log")
        self.live_ateq = {}
        if self.live_mode:
            adapter = SerialAteq(self.settings.ateq_com, self.station.value,
                                 slave=self.settings.ateq_slave, timeout_s=0.8)
            try:
                adapter.connect()
                # Opening COM only proves the Windows handle is available.
                # Perform the same Modbus holding-register read as the
                # reference implementation so the UI cannot claim ATEQ
                # online without a response from the configured slave.
                adapter.read_registers(SerialAteq.REALTIME_ADDRESS, 1)
            except Exception:
                adapter.close()
                raise
            self.live_ateq[self.station] = adapter
        self.point_map = build_point_map(self.settings)
        self.data_dir = Path(r"D:\data")
        if self.live_mode:
            # Live mode is deliberately all-or-nothing for the real services.
            # A fake PLC/DB fallback would make a field test look successful
            # while leaving no trace in the real system.
            # PLC 适配器按配置选择：S7 snap7 / FX 编程口直连 / 经 B 电脑中转。
            self.real_plc = build_plc(self.settings)
            self.weight_service = build_weight_service(self.settings, self.real_plc)
            self.relay_server = start_relay_service(self.settings, self.real_plc)
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
            self.marker = LaserMarker(
                LaserFileWriter(LaserChannel(self.settings.laser_file(),
                                             self.settings.laser_encoding,
                                             self.settings.laser_newline)),
                self.plc, self.point_map,
                hold_seconds=self.settings.laser_hold_seconds,
                settle_seconds=self.settings.laser_settle_seconds,
                clear_after_seconds=self.settings.laser_clear_after_seconds,
                wait_done=self.settings.laser_wait_done,
                done_timeout_s=self.settings.laser_done_timeout_s,
                date_code_fn=build_date_code_fn(self.settings))
        else:
            self.real_plc = None
            self.weight_service = None
            self.relay_server = None
            self.plc = FakePlc()
            self.repository = FakeRepository(self.settings)
            self.marker = FakeMarker()
        if self.live_mode:
            # 现场要求：UI 启动时清零本工位打码请求位（M888/M889 锁存残留
            # 会在 PLC 运行时被误当作打码请求）；清零失败则拒绝启动（fail-closed）。
            self._clear_laser_start_bit()
        self.calibration = {s: Calibration(station=s, initial_due=True) for s in StationId}
        self._calibration_state_path = Path(
            os.environ.get("LEAKTEST_CAL_STATE", r"D:\ATEQ\calibration_state.json"))
        self._restore_calibration(); self.security = SecurityContext(AuthSession(demo=True, password_file=self.data_dir / "管理员.txt"), LicenseVerifier(simulator=True).verify(b"SIMULATE", b"SIMULATE-SIGNATURE")); self.product_settings = ProductSettingsService(self.security); self.model_settings = ModelSettingsService(self.security, self.data_dir / "日期设置.ini"); self.personnel = PersonnelService(self.security, self.data_dir / "作业员列表.txt"); self.global_settings = GlobalSettingsService(self.security, self.data_dir / "全局设置.ini"); self.confirmation_callback = self._confirm_output; self._setup_values = {"customer_no":"", "ateq_no":"SIM"}; self.journal_dir = self._build_journal_dir(); self.tabs = CompatibilityTabs(); self._language = language if language in UiTextCatalog.LANGUAGES else "中文"; self._i18n_widgets = []
        self.cards = [StationPanel(self.station, self.repository, self.marker,
                                   self.plc,
                                   CycleJournal(self.journal_dir / f"{self.station.value}.json"),
                                   self.security, self.product_settings.current_product,
                                   lambda station, signal, requested, current: self.confirmation_callback(station, signal, requested, current),
                                   self.refresh_all, self._model_for, self.personnel.list_all,
                                   lambda station: self.calibration[station], self.start_calibration,
                                   self.live_ateq.get(self.station), self.point_map)]
        self._build_main(); self._build_setup(); self._build_query(); self._build_manual(); self.setCentralWidget(self.tabs)
        self._configure_calibration_period(); self._update_setup_gate(); self._refresh_models(); self._refresh_personnel(); self._refresh_runtime_choices()
        self._refresh_calibration_countdowns()
        self.calibration_timer = QTimer(self)
        self.calibration_timer.setInterval(1000)
        self.calibration_timer.timeout.connect(self._tick_calibration)
        self.calibration_timer.start()
        self.ateq_heartbeat_timer = QTimer(self)
        self.ateq_heartbeat_timer.setInterval(100)
        self.ateq_heartbeat_timer.timeout.connect(self._ateq_heartbeat)
        self.pressure_alarm_timer = QTimer(self)
        self.pressure_alarm_timer.setInterval(2000)
        self.pressure_alarm_timer.timeout.connect(self._poll_pressure_alarm)
        self.pressure_alarm_timer.start()
        self.plc_reset_timer = QTimer(self)
        self.plc_reset_timer.setInterval(200)
        self.plc_reset_timer.timeout.connect(self._poll_plc_reset)
        self._last_live_stepcode = None
        self._b_test_in_progress = False
        self._plc_reset_pending = False
        self._last_reset_bit = None
        self.cards[0].stepcode_updated.connect(lambda value: self._remember_live_stepcode(self.station, value))
        # Configuration diagnostics only; physical cycles are dispatched
        # exclusively by the ATEQ StepCode=4 hardware edge.
        if self.live_mode:
            self.ateq_heartbeat_timer.start()
            self.plc_reset_timer.start()
        self.calibration_status.setText("校准到期，请点击启动验证")
        self.laser_status.setText(self._laser_status_text())
        initial_titles = {"clamp": "夹紧 / Clamp / Serrage", "transfer": "移载 / Transfer / Transfert", "block": "封堵 / Blocking / Obstruction", "stamp": "盖章 / Stamp / Timbre", "door_disable": "安全门使能/禁用 / Door enable/disable", "manual": "自动/手动 / Automatic/Manual"}
        for signal, _, _ in MANUAL_NAMES:
            label_widget = self.findChild(QLabel, f"manual_label_{signal}_{self.station.value}")
            if label_widget is not None: label_widget.setText(f"{self.station.value} {initial_titles[signal]}")
        # Apply the selected catalog before the first frame is shown.
        self.language_selector.setCurrentText(self._language)
        self._apply_language(self._language)

    def _clear_laser_start_bit(self) -> None:
        """LIVE 启动清零：强制复位本工位打码请求位（socket 残留不得触发打码）。"""
        if not self.point_map.has("laser_start"):
            return
        byte, bit = self.point_map.address("laser_start")
        try:
            self.plc.write_bit(byte, bit, False)
        except Exception as exc:
            raise RuntimeError(
                f"LIVE 启动清零打码位 M{byte}.{bit} 失败: {type(exc).__name__}: {exc}") from exc
        self._live_trace(f"LASER_START_CLEARED station={self.station.value} M{byte}.{bit}")

    def _build_journal_dir(self) -> Path:
        """Durable per-station journal directory.

        Priority: ``LEAKTEST_JOURNAL_DIR`` override → live ``D:\\ATEQ\\journal``
        → per-run temp in simulate.  Live/env creation failures raise: a live
        station must never silently fall back to a volatile journal.
        """
        override = os.environ.get("LEAKTEST_JOURNAL_DIR")
        if override:
            path = Path(override)
        elif self.live_mode:
            path = Path(r"D:\ATEQ\journal")
        else:
            return Path(tempfile.mkdtemp(prefix="LaserLeakTest-replica-"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _single_mode_marking_unsupported(self, card, *, sample: bool) -> bool:
        """True for marking-capable single-mode cycles on the MySQL live path.

        Sample sites (``sample=True``) always freeze ``test_mode="single"`` and
        can only reach ``mark()`` when ``mark_samples`` is enabled
        (``station.py:126-136``); the default live calibration sample stays
        allowed.  SIMULATE and non-MySQL repositories are unaffected.
        """
        if not self.live_mode or not isinstance(card.controller.repository,
                                                 PyMySQLRepository):
            return False
        mode = "single" if sample else ("dual" if card.mode_button.isChecked()
                                        else "single")
        marking_capable = (not sample) or card.controller.mark_samples
        return mode == "single" and marking_capable

    def _laser_status_text(self) -> str:
        if self.live_mode:
            return (f"LIVE 工位 {self.station.value} | PLC {self.settings.plc_ip} | "
                    f"ATEQ {self.settings.ateq_com}/从站{self.settings.ateq_slave} | "
                    f"打码文件 {self.settings.laser_file()}")
        return f"SIMULATE | 打码文件目录（模拟） {self.settings.laser_file()}"

    def resizeEvent(self, event):
        """Re-evaluate responsive error rows whenever the operator resizes."""
        super().resizeEvent(event)
        for card in getattr(self, "cards", ()):
            card.refresh()

    def _live_trace(self, message: str) -> None:
        # Live tracing is active only for the live hardware UI.
        if not self.live_mode:
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

    def _poll_pressure_alarm(self):
        """PLC->PC 压力开关状态：2 秒只读轮询（仅正压 StepCode=5 期间显示报警）。"""
        card = self.cards[0]
        if not card.point_map.has("pressure_alarm"):
            return
        try:
            byte, bit = card.point_map.address("pressure_alarm")
            normal = bool(card.plc.read_bit(byte, bit))
        except Exception as exc:
            self._live_trace(f"PRESSURE_ALARM_READ_FAILED {type(exc).__name__}: {exc}")
            return
        in_positive_hold = (
            getattr(self, "_last_live_stepcode", None) == 5
            and card.controller.phase is Phase.TEST_2)
        card.set_pressure_alarm(in_positive_hold and not normal)

    def _poll_plc_reset(self):
        """PLC 面板复位（A=M0.0 / B=M0.3）：上升沿→清报警并复位未完成周期。"""
        card = self.cards[0]
        if not card.point_map.has("start"):
            return
        byte, bit = card.point_map.address("start")
        try:
            current = bool(card.plc.read_bit(byte, bit))
        except Exception:
            return  # 读失败不动作，下个周期再试
        previous = self._last_reset_bit
        self._last_reset_bit = current
        if previous is None or not current or previous:
            return  # 仅 0→1 上升沿；启动时只记录初值
        self._live_trace(f"PLC_RESET station={self.station.value} M{byte}.{bit} 0->1")
        if getattr(card, "_test_worker_running", False):
            self._plc_reset_pending = True
            self._live_trace(
                f"PLC_RESET_DEFERRED station={self.station.value} 测试进行中，终止监视后复位")
            return
        self._apply_plc_reset()

    def _apply_plc_reset(self):
        """面板复位落地：清上位机报警 + 归档未完成周期 + 重连 PLC 恢复写权限。"""
        self._plc_reset_pending = False
        card = self.cards[0]
        controller = card.controller
        try:
            if controller.recovery_required:
                controller.resolve_recovery(
                    f"PLC 复位 {self.station.value}", require_permission=False)
            else:
                controller.reset()
                if controller.recovery_required:
                    # 活动周期被安全中止：按面板复位意图立即归档
                    controller.resolve_recovery(
                        f"PLC 复位 {self.station.value}", require_permission=False)
            card._error_key = None
            card.reconnect_plc()
            card.refresh()
            card.changed_callback()
            self._live_trace(
                f"PLC_RESET_APPLIED station={self.station.value} phase={controller.phase.value}")
        except Exception as exc:
            self._live_trace(
                f"PLC_RESET_FAILED station={self.station.value} {type(exc).__name__}: {exc}")

    def _ateq_heartbeat(self):
        """Keep the F620 Modbus session alive with a read-only status poll."""
        ateq = self.live_ateq.get(self.station)
        if ateq is None:
            return
        if getattr(self, "_b_test_in_progress", False):
            # A test owns the serial link; do not interleave status polls.
            return
        try:
            registers, _ = ateq.read_registers(
                SerialAteq.REALTIME_ADDRESS, SerialAteq.REALTIME_COUNT)
            self._handle_live_stepcode(self.station, SerialAteq._swap16(registers[4]))
        except Exception:
            self.cards[0].set_stepcode("离线")
            try:
                ateq.connect()
                ateq.read_registers(SerialAteq.REALTIME_ADDRESS, 1)
            except Exception as reconnect_exc:
                self._live_trace(f"ATEQ_RECONNECT_FAILED station={self.station.value} {reconnect_exc}")

    def _remember_live_stepcode(self, station, value: str):
        try:
            code = int(value)
        except (TypeError, ValueError):
            return
        if code != getattr(self, "_last_live_stepcode", None):
            self._last_live_stepcode = code
            self._live_trace(f"ATEQ_STEP station={station.value} code={code}")

    def _handle_live_stepcode(self, station, step_code: int):
        """Dispatch one station's stored stage on a new StepCode=4."""
        card = self.cards[0]
        previous = getattr(self, "_last_live_stepcode", None)
        self._last_live_stepcode = step_code
        card.set_stepcode(step_code)
        if step_code != previous:
            # 记录每次 StepCode 变化，供远程诊断 ATEQ 周期时序。
            self._live_trace(f"ATEQ_STEP station={station.value} code={step_code}")
        if step_code != 4 or previous == 4:
            return
        phase = card.controller.phase
        if (phase not in (Phase.READY, Phase.WAIT_2)
                and self._restore_pending_calibration_cycle(card)):
            phase = card.controller.phase
        if (phase is Phase.IDLE
                and not self.calibration[card.station].locked
                and not self.calibration[card.station].validation_started
                and self._prepare_stepcode_production_cycle(card)):
            phase = card.controller.phase
        elif (phase is Phase.COMPLETE
              and not self.calibration[card.station].locked
              and not self.calibration[card.station].validation_started):
            # A normal NG result has no marking transaction to await. Preserve
            # its repository row, archive the terminal journal, then start the
            # next cycle on the next hardware cycle edge.
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
        part_no = card.part_no.currentText().strip()
        if not part_no:
            self._live_trace(
                f"PRODUCTION_CYCLE_BLOCKED station={card.station.value} no model selected")
            return False
        if card.controller.phase is not Phase.IDLE:
            return False
        if self._single_mode_marking_unsupported(card, sample=False):
            card._error_key = "single_mode_unsupported"
            card.refresh()
            self._live_trace(
                f"PRODUCTION_CYCLE_BLOCKED station={card.station.value} "
                f"reason=single_mode_unsupported")
            return False
        try:
            config = self._model_for(part_no)
        except Exception as exc:
            self._live_trace(
                f"PRODUCTION_CYCLE_BLOCKED station={card.station.value} model={part_no} "
                f"{type(exc).__name__}: {exc}")
            return False
        mode = "dual" if card.mode_button.isChecked() else "single"
        selection = CycleSelection(card.station, part_no,
                                   card.staff.currentText().strip() or "Operator", mode,
                                   str(config.ateq_program),
                                   date_scheme=self._resolved_date_scheme(config))
        # 每个周期开始前恢复本机 PLC 连接与写门禁：带 journal 重启后适配器
        # 可能处于断开/写禁用状态，否则本周期打码会被 capability policy 拒绝。
        card.reconnect_plc()
        card.controller.start_cycle(selection)
        card.refresh()
        self._live_trace(
            f"PRODUCTION_CYCLE_READY station={card.station.value} "
            f"cycle={card.controller.record.cycle_id} mode={mode} program={config.ateq_program}")
        return True

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
                or controller.phase is not Phase.IDLE):
            return False
        part_no = card.part_no.currentText().strip()
        if not part_no:
            self._live_trace(
                f"CAL_CYCLE_RESTORE_BLOCKED station={card.station.value} no selected model")
            return False
        try:
            config = self._model_for(part_no)
        except Exception as exc:
            self._live_trace(
                f"CAL_CYCLE_RESTORE_BLOCKED station={card.station.value} "
                f"{type(exc).__name__}: {exc}")
            return False
        if self._single_mode_marking_unsupported(card, sample=True):
            self._live_trace(
                f"CAL_CYCLE_RESTORE_BLOCKED station={card.station.value} "
                f"reason=single_mode_unsupported")
            return False
        selection = CycleSelection(card.station, part_no,
                                   card.staff.currentText().strip() or "Operator",
                                   "single", str(config.ateq_program),
                                   date_scheme=self._resolved_date_scheme(config))
        controller.start_cycle(selection, sample=True)
        card.refresh()
        self._live_trace(
            f"CAL_CYCLE_RESTORED station={card.station.value} "
            f"sample={calibration.sample_demand} cycle={controller.record.cycle_id}")
        return True

    def _begin_ok_validation_cycle(self, card) -> bool:
        """Bridge NG -> OK validation: archive the NG cycle and start the OK one.

        NG 通过后控制器停在“完成”，而 OK 样件需要一个新测试周期。PLC 上升沿
        在派发前先走这里。故障相位也接受：先尝试复位归档。
        This bridge runs only when a new StepCode=4 arrives.
        """
        calibration = self.calibration[card.station]
        if not (calibration.validation_started
                and calibration.phase is CalibrationPhase.WAIT_OK
                and card.controller.phase in (Phase.COMPLETE, Phase.FAULT, Phase.IDLE)):
            return False
        part_no = card.part_no.currentText().strip()
        if not part_no:
            return False
        if self._single_mode_marking_unsupported(card, sample=True):
            self._live_trace(
                f"CAL_OK_BRIDGE_BLOCKED station={card.station.value} "
                f"reason=single_mode_unsupported")
            return False
        if card.controller.record is not None or card.controller.phase is Phase.FAULT:
            try:
                card.controller.reset()
            except Exception as exc:
                self._live_trace(
                    f"CAL_OK_BRIDGE_RESET_FAILED {type(exc).__name__}: {exc}")
                return False
        try:
            config = self._model_for(part_no)
        except Exception as exc:
            self._live_trace(
                f"CAL_OK_BRIDGE_MODEL_FAILED station={card.station.value} "
                f"{type(exc).__name__}: {exc}")
            return False
        selection = CycleSelection(card.station, part_no,
                                   card.staff.currentText().strip() or "Operator",
                                   "single", str(config.ateq_program),
                                   date_scheme=self._resolved_date_scheme(config))
        card.controller.start_cycle(selection, sample=True)
        card.refresh()
        self._live_trace(
            f"CAL_OK_CYCLE_READY station={card.station.value} "
            f"cycle={card.controller.record.cycle_id}")
        return True

    def _model_for(self, part_no: str) -> ModelConfig:
        """Load one committed model configuration (型号参数)."""
        return self.model_settings.load(part_no)

    def _resolved_date_scheme(self, config: ModelConfig) -> str:
        """Resolve 型号日期方案 → 工位设置 → YYYYMMDD（空值逐级回退）。"""
        model_value = config.date_scheme if isinstance(getattr(config, "date_scheme", ""), str) else ""
        station_value = getattr(self.settings, "laser_date_scheme", "")
        if not isinstance(station_value, str):
            station_value = ""
        return model_value.strip() or station_value.strip() or "YYYYMMDD"

    def _refresh_runtime_choices(self):
        products = self.model_settings.list_models()
        people = self.personnel.list_all()
        for card in self.cards:
            card.set_choices(products, people)

    def _build_main(self):
        page = QWidget(); page.setObjectName("page"); root = QVBoxLayout(page); root.setContentsMargins(METRICS.page_margin, 8, METRICS.page_margin, 8); root.setSpacing(METRICS.station_gap)
        ports = f"{self.settings.ateq_com}/从站{self.settings.ateq_slave}" if self.settings.ports_confirmed else "BLOCKED/未确认"
        mode_label = f"LIVE {self.station.value}" if self.live_mode else "SIMULATE"
        service_label = "real services" if self.live_mode else "Fake services"
        self.system_status = QLabel(f"模式：{mode_label} | PLC：{self.settings.plc_ip} | ATEQ：{ports} | {service_label}"); self.system_status.setObjectName("systemStatusBar"); self.system_status.setVisible(False); root.addWidget(self.system_status)
        top = QHBoxLayout(); top.setSpacing(METRICS.station_gap); top.addWidget(self.cards[0], 1); root.addLayout(top, 1)
        # Footer: station indicators | login | calibration countdown.  The
        # calibration lamps are bound to the station's local NG -> OK
        # validation state; Start Validation is the only footer action.
        footer = QHBoxLayout(); footer.setSpacing(METRICS.station_gap); footer.addWidget(self.cards[0].bottom_indicators, 4); footer.addWidget(self._login_panel(), 3)
        countdown_box = QGroupBox("校准倒计时 / Calibration Countdown"); countdown_box.setObjectName("calibrationCountdownPanel"); countdown_layout = QGridLayout(countdown_box); countdown_layout.setContentsMargins(8, 8, 8, 8); countdown_layout.setHorizontalSpacing(6); countdown_layout.setVerticalSpacing(4)
        self.calibration_countdown_label = QLabel("校准倒计时"); self.calibration_countdown_label.setObjectName("calibration_countdown_label"); self.calibration_countdown_label.setMinimumWidth(92)
        self.calibration_countdown = _CountdownSpinBox(); self.calibration_countdown.setObjectName("calibration_countdown")
        countdown_layout.addWidget(self.calibration_countdown_label, 0, 0); countdown_layout.addWidget(self.calibration_countdown, 0, 1)
        footer.addWidget(countdown_box); root.addLayout(footer)
        self.cards[0].bottom_indicators.setMaximumHeight(METRICS.footer_max_height)
        # 打码状态栏：显示本工位打码文件路径与联机摘要。
        laser_bar = QHBoxLayout(); laser_bar.setSpacing(8)
        self.laser_indicator = QLabel("●"); self.laser_indicator.setObjectName("laser_indicator"); self.laser_indicator.setProperty("state", "ok"); laser_bar.addWidget(self.laser_indicator)
        self.laser_status = QLabel("激光打码就绪"); self.laser_status.setObjectName("laserStatus"); laser_bar.addWidget(self.laser_status, 1)
        self.laser_status.setMinimumHeight(METRICS.scanner_height); root.addLayout(laser_bar); self.tabs.addTab(page, "测试/Main/Principale")

    def closeEvent(self, event):
        timer = getattr(self, "calibration_timer", None)
        if timer is not None:
            timer.stop()
        timer = getattr(self, "ateq_heartbeat_timer", None)
        if timer is not None:
            timer.stop()
        timer = getattr(self, "pressure_alarm_timer", None)
        if timer is not None:
            timer.stop()
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
        self.setup_table = QTableWidget(40, 5); self.setup_table.setObjectName("setupParameterTable"); self.setup_table.verticalHeader().setDefaultSectionSize(METRICS.input_height + 6); self.setup_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.setup_table.setAlternatingRowColors(True); StationPanel._configure_table(self.setup_table); self.setup_table.itemChanged.connect(self._on_model_item_changed); model_layout.addWidget(self.setup_table, 1)
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
        cal_value = global_values.get("校准周期", "08:00:00"); cal_time = QTime.fromString(cal_value, "HH:mm:ss")
        self.cal_period = QTimeEdit(cal_time if cal_time.isValid() else QTime(0,0)); self.cal_period.setObjectName("calibration_period"); self.cal_period.setMaximumWidth(170); self.cal_period.editingFinished.connect(self._save_global_settings)
        port_text = self.settings.ateq_com if self.settings.ports_confirmed else "BLOCKED/未确认"
        self.ateq_port = QLineEdit(port_text); self.ateq_port.setObjectName("ateq_com"); self.ateq_port.setReadOnly(True); self.ateq_port.setMaximumWidth(240)
        self.setup_laser = QLabel("● Laser"); self.setup_laser.setObjectName("setup_laser_indicator"); self.setup_laser.setProperty("state", "ng")
        self.settings_language_label = QLabel("Language"); self.settings_language_label.setObjectName("settingsLanguageLabel")
        self.cal_period_label = QLabel("Calibration Period (Hours)"); self.cal_period_label.setObjectName("calibrationPeriodLabel")
        self.ateq_label = QLabel("ATEQ F620 (Restart Software to Active)"); self.ateq_label.setObjectName("ateqLabel")
        setting_labels = (self.settings_language_label, self.cal_period_label, self.ateq_label)
        for label in setting_labels:
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        left_aligned = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        other.addWidget(self.settings_language_label, 0, 0); other.addWidget(self.language_selector, 0, 1, left_aligned)
        other.addWidget(self.cal_period_label, 1, 0); other.addWidget(self.cal_period, 1, 1, left_aligned)
        other.addWidget(self.ateq_label, 2, 0); other.addWidget(self.ateq_port, 2, 1, left_aligned)
        self.settings_status = QLabel("已提交：SIM-PART"); self.settings_status.setObjectName("settingsStatus"); other.addWidget(self.settings_status, 3, 0, 1, 2)
        other.addWidget(self.setup_laser, 4, 0); self.calibration_status = QLabel("等待 NG 样件"); self.calibration_status.setObjectName("calibrationStatus"); other.addWidget(self.calibration_status, 4, 1)
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
    # 0..4 are the production surface.
    # 0=当前 1=产品型号 2=客户编号 3=日期方案 4=ATEQ程序号
    # 0..7 are the production surface.  Column 8 remains hidden only to keep
    # binary/UI automation compatibility with the first nine-column release.
    # 0=当前 1=产品型号 2=客户编号 3=条码规则 4=日期方案 5=ATEQ程序号
    # 6=打印模板A 7=打印模板B
    MODEL_COLUMNS = 5

    def _make_combo(self, kind, value=""):
        combo = QComboBox()
        if kind == "date":
            # Old 日期设置.ini files use Chinese names such as
            # 年方案2+月方案1+日方案2.  Keep that raw value in userData for
            # the date engine, but show a language-neutral compact code.
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
        raise ValueError(f"未知配置控件: {kind}")

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
        self.setup_table.setCellWidget(row, 3, self._make_combo("date", config.date_scheme))
        self.setup_table.setCellWidget(row, 4, self._program_widget(row, config.ateq_program))
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
        minimums = {0: 46, 1: 150, 2: 170, 3: 170, 4: 130}
        for column, width in minimums.items():
            if table.columnWidth(column) < width:
                table.setColumnWidth(column, width)
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
            def cell_text(column, default=""):
                item = self.setup_table.item(row, column)
                return item.text().strip() if item else default
            date_widget = self.setup_table.cellWidget(row, 3)
            program_widget = self.setup_table.cellWidget(row, 4)
            program = str(program_widget.value()) if isinstance(program_widget, QSpinBox) else cell_text(4, "1") or "1"
            date_value = date_widget.currentData() if date_widget else DATE_SCHEME_PRESETS[0]
            configs.append(ModelConfig(
                part_no=part,
                customer_no=cell_text(2),
                date_scheme=str(date_value or DATE_SCHEME_PRESETS[0]).strip(),
                ateq_program=program,
            ))
        return configs

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
        self._populate_model_row(empty_row, ModelConfig(part_no=part), current=False)
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
        hint_source = "本工位记录查询 · 支持时间、型号和结果 / Search station records by time, part, and result"
        hint = QLabel(hint_source); hint.setObjectName("pageSubtitle"); hint.setProperty("replica_source", hint_source); root.addWidget(hint)
        s = self.station
        filters = QHBoxLayout(); filters.setSpacing(METRICS.station_gap); self.query_fields = {}
        group = QGroupBox(f"{s.value} List Search"); group.setObjectName(f"queryFilters_{s.value}"); group.setProperty("queryFilterCard", True); grid = QGridLayout(group); grid.setContentsMargins(12, 18, 12, 12); grid.setHorizontalSpacing(9); grid.setVerticalSpacing(7)
        for row, (key, label) in enumerate((("start", "Start Time"),("finish", "Finish Time"),("part", "Part No."),("result", "Result"))):
            if key in {"start", "finish"}:
                widget = QDateTimeEdit(QDateTime.currentDateTime()); widget.setCalendarPopup(True); widget.setDisplayFormat("yyyy-MM-dd HH:mm:ss"); widget.setDateTime(QDateTime.currentDateTime().addDays(-1 if key == "start" else 1))
            else:
                widget = QLineEdit()
            widget.setObjectName(f"query_{key}_{s.value}"); self.query_fields[(s,key)] = widget; grid.addWidget(QLabel(f"{label} {s.value}"),row,0); grid.addWidget(widget,row,1)
        status = QLabel(); status.setObjectName(f"query_status_{s.value}"); self.query_status = {s: status}; grid.addWidget(status,5,0,1,2)
        search = QPushButton(f"Search {s.value}"); search.setObjectName(f"query_search_{s.value}"); search.setProperty("primary", True); search.clicked.connect(self.refresh_query)
        download = QPushButton(f"Download {s.value}"); download.setObjectName(f"query_download_{s.value}"); download.clicked.connect(lambda _=False, station=s: self.download_query(station))
        actions = QHBoxLayout(); actions.setSpacing(8); actions.addWidget(search, 1); actions.addWidget(download, 1); grid.addLayout(actions, 4, 0, 1, 2); filters.addWidget(group)
        root.addLayout(filters)
        table = QTableWidget(30,11); table.setObjectName(f"query_table_{s.value}"); table.setHorizontalHeaderLabels(DISPLAY_HEADERS["base"]); table.setAlternatingRowColors(True); table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed); table.verticalHeader().setDefaultSectionSize(METRICS.table_row_height); StationPanel._configure_table(table); [table.setRowHeight(i, METRICS.table_row_height) for i in range(30)]; self.query_tables = {s: table}
        root.addWidget(table,1); self.tabs.addTab(page, "查询/Query/Requête")

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
        for s in (self.station,):
            group = QGroupBox(f"工位 {s.value} / Station {s.value}"); group.setObjectName(f"manualGroup_{s.value}"); group.setProperty("stationTitle", True); layout = QVBoxLayout(group); layout.setContentsMargins(14, 22, 14, 14); layout.setSpacing(9)
            for signal, title, states in MANUAL_NAMES:
                row_frame = QFrame(); row_frame.setObjectName(f"manualRow_{signal}_{s.value}"); row_frame.setProperty("manualControlRow", True); row_frame.setMinimumHeight(68)
                row = QHBoxLayout(row_frame); row.setContentsMargins(12, 8, 12, 8); row.setSpacing(8)
                label_widget = QLabel(f"{s.value}{title}"); label_widget.setObjectName(f"manual_label_{signal}_{s.value}"); label_widget.setProperty("manualControlLabel", True); label_widget.setMinimumWidth(118); label_widget.setMaximumWidth(172); row.addWidget(label_widget, 1)
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
        b = QPushButton(f"归档工位 {self.station.value}"); b.setObjectName(f"resolve_recovery_{self.station.value}"); b.clicked.connect(lambda _=False, station=self.station: self.resolve_recovery(station, self.recovery_reason.text() or "UI 人工确认")); rl.addWidget(b)
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
        byte, bit = self.point_map.address(signal)
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
        for signal in ("clamp", "transfer", "block", "stamp", "door_disable", "manual"):
            try:
                byte, bit = self.point_map.address(signal)
            except Exception:
                continue
            value = self.plc.read_bit(byte, bit)
            readback = self.findChild(QLabel, f"manual_readback_{signal}_{self.station.value}")
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
        self.setup_table.setHorizontalHeaderLabels({"中文": ["当前", "产品型号", "客户编号", "日期方案", "ATEQ 程序号"],
                                                     "English": ["Current", "Part No.", "Customer No.", "Date Scheme", "ATEQ Program"],
                                                     "Français": ["Actuel", "N° pièce", "N° client", "Schéma date", "Programme ATEQ"]}[value])
        widgets = self.findChildren(QLabel) + self.findChildren(QPushButton) + self.findChildren(QGroupBox)
        for widget in widgets:
            source = widget.property("replica_source")
            if not source:
                source = widget.title() if isinstance(widget, QGroupBox) else widget.text()
                widget.setProperty("replica_source", source)
            text = UiTextCatalog.translate(str(source), value)
            if isinstance(widget, QGroupBox):
                widget.setTitle(text)
            elif widget.objectName().startswith("reprint_"):
                station = widget.objectName().rsplit("_", 1)[-1]
                widget.setText({
                    "中文": f"重打码 {station}",
                    "English": f"Re-mark {station}",
                    "Français": f"Re-marquer {station}",
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
        for station in (self.station,):
            card = self.cards[0]
            for (signal, _), label_text in zip(INDICATOR_NAMES, INDICATOR_DISPLAY_LABELS[value]):
                label = card.indicator_labels.get(signal)
                if label is None:
                    continue
                label.setText(label_text)
                StationPanel._fit_indicator_label(label)
            card._apply_mode_caption(value)
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
        placeholder_map = {"中文": {"product_draft": "产品型号", "recovery_reason": "处理原因", "login_username": "登录角色", "login_password": "密码"}, "English": {"product_draft": "Part No.", "recovery_reason": "Reason", "login_username": "Role", "login_password": "Password"}, "Français": {"product_draft": "N° pièce", "recovery_reason": "Motif", "login_username": "Rôle", "login_password": "Mot de passe"}}[value]
        for object_name, placeholder in placeholder_map.items():
            widget = self.findChild(QLineEdit, object_name)
            if widget is not None: widget.setPlaceholderText(placeholder)
        for station in StationId:
            part = self.findChild(QLineEdit, f"part_no_{station.value}")
            if part is not None: part.setPlaceholderText({"中文": "产品型号", "English": "Part No.", "Français": "N° pièce"}[value])
        self.settings_status.setText({"中文": "语言：中文", "English": "Language: English", "Français": "Langue : Français"}[value])
        self.calibration_countdown_label.setText({"中文": "校准倒计时", "English": "Calibration countdown", "Français": "Compte à rebours"}[value])
        own = self.calibration[self.station]
        if own.due and not own.validation_started:
            self.calibration_status.setText({
                "中文": "校准到期，请点击启动验证",
                "English": "Calibration due; click Start Validation",
                "Français": "Calibration échue ; cliquez sur Validation démarrage",
            }[value])
        self._apply_setup_language(value)
        authenticated = self.security.role.value == "admin"
        self.login_status.setText({"中文": "已认证" if authenticated else "未登录", "English": "Authenticated" if authenticated else "Not signed in", "Français": "Authentifié" if authenticated else "Non connecté"}[value])
        self.laser_status.setText(self._laser_status_text())
        next_actions = {"中文": {Phase.IDLE: "等待启动", Phase.READY: "一测", Phase.WAIT_2: "二测", Phase.MARKING: "打码", Phase.COMPLETE: "复位或查询", Phase.FAULT: "管理员恢复"}, "English": {Phase.IDLE: "Await start", Phase.READY: "Test 1", Phase.WAIT_2: "Test 2", Phase.MARKING: "Marking", Phase.COMPLETE: "Reset or query", Phase.FAULT: "Admin recovery"}, "Français": {Phase.IDLE: "Attente départ", Phase.READY: "Test 1", Phase.WAIT_2: "Test 2", Phase.MARKING: "Marquage", Phase.COMPLETE: "Réinitialiser ou requête", Phase.FAULT: "Récupération admin"}}[value]
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
        self.calibration[self.station].set_period(seconds)
        return seconds

    def _persist_calibration(self) -> None:
        """Save A/B calibration progress so restarts don't force re-validation.

        校准状态此前只在内存里，程序重启后操作员被迫重新做 NG/OK 验证。
        状态仅在发生变化时写盘（约每秒检查一次签名）。
        """
        snapshot = {}
        station, cal = self.station, self.calibration[self.station]
        if True:
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
        for station, cal in ((self.station, self.calibration[self.station]),):
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
        """Advance the station timer and raise the lamp when it expires."""
        try:
            station = self.station
            calibration = self.calibration[station]
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
            if card.controller.recovery_required:
                raise RuntimeError(
                    "存在未完成周期：请先在【手动/Manual】页归档或按机器面板复位，再取消校准")
            if not (calibration.due or calibration.validation_started or calibration.clear_pending):
                raise RuntimeError("当前工位没有待取消的校准状态")
            if card.controller.phase is Phase.FAULT:
                raise RuntimeError("当前工位处于故障：请先复位/归档后再取消校准")
            if card.controller.phase not in (Phase.IDLE, Phase.COMPLETE):
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
        card = self._card_for_station(self.station)
        button = getattr(card, "start_validation_button", None)
        if button is None:
            return
        calibration = self.calibration[self.station]
        self._live_trace(
            f"CAL_BUTTON_STATE station={self.station.value} enabled={button.isEnabled()} "
            f"phase={card.controller.phase.value} due={calibration.due} "
            f"validation_started={calibration.validation_started} "
            f"clear_pending={calibration.clear_pending}")

    def _save_global_settings(self):
        try:
            period_seconds = self._calibration_period_seconds()
            self.global_settings.save({
                "校准周期": self.cal_period.time().toString("HH:mm:ss"),
            })
            self.calibration[self.station].set_period(period_seconds)
            self.settings_status.setText({"中文": "全局设置已保存", "English": "Global settings saved", "Français": "Réglages globaux enregistrés"}[self._language])
        except Exception as exc:
            self.settings_status.setText({"中文": f"设置拒绝：{exc}", "English": f"Settings denied: {exc}", "Français": f"Paramètres refusés : {exc}"}[self._language])
    def manual_output(self, button, station=StationId.A, signal="clamp"):
        try:
            byte, bit = self.point_map.address(signal); current = self.plc.read_bit(byte, bit)
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
        needle = self.query_fields[(station,"part")].text().strip().lower(); result = self.query_fields[(station,"result")].text().strip().lower(); rows = [r for r in self.repository.records.values() if r.station is station]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return [r for r in rows if start <= r.created_at.astimezone(start.tzinfo) <= finish and (not needle or needle in r.part_no.lower()) and (not result or result in ((r.second or r.first).result.value.lower() if (r.second or r.first) else ""))]
    def refresh_query(self):
        for station, table in self.query_tables.items():
            rows = self._query_records(station); table.setRowCount(30)
            sequences = StationPanel._daily_sequence_map(
                [r for r in self.repository.records.values() if r.station is station])
            for i in range(30):
                vals = ["", "", "", "", "", "", "", "", "", "", ""]
                if i < len(rows):
                    r=rows[i]; vals=[r.created_at.astimezone().strftime("%Y-%m-%d-%H:%M:%S"),r.part_no,StationPanel._m(self,r.first,"pressure") if r.first else "",StationPanel._m(self,r.first,"leakage") if r.first else "",StationPanel._m(self,r.second,"pressure") if r.second else "",StationPanel._m(self,r.second,"leakage") if r.second else "",(r.second or r.first).result.value if (r.second or r.first) else "","√" if r.marked else "",r.person,r.cycle_id,StationPanel._daily_sequence_text(r, sequences.get(r.cycle_id))]
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
        if station is not self.station:
            raise KeyError(f"工位 {station.value} 不属于本实例（当前 {self.station.value}）")
        return self.cards[0]

    def _set_calibration_countdown(self, station, value):
        widget = self.calibration_countdown
        widget.setValue(max(0, int(float(value))))

    def _refresh_calibration_countdowns(self):
        self._set_calibration_countdown(self.station, self.calibration[self.station].remaining_seconds)

    def mark_calibration_due(self, station=StationId.A):
        """Raise one station's calibration indicator (timer/PLC integration hook)."""
        calibration = self.calibration[station]
        calibration.mark_due()
        self._set_calibration_countdown(station, calibration.remaining_seconds)
        self._card_for_station(station).refresh()
        return calibration

    def start_calibration(self, station=None):
        """Start the operator-confirmed NG -> OK validation sequence.

        This callback is the only action behind the footer Start Validation
        button.  It never toggles the production PLC start bit.
        """
        station = station or self.station
        card = self._card_for_station(station)
        calibration = self.calibration[station]
        controller = card.controller
        self._live_trace(f"CAL_START_REQUEST station={station.value} phase={controller.phase.value} has_record={controller.record is not None}")
        initial_state = controller.record is None and controller.phase is Phase.IDLE
        completed_cycle = controller.record is not None and controller.phase is Phase.COMPLETE
        if not (initial_state or completed_cycle):
            raise RuntimeError("当前测试尚未完成")
        if self._single_mode_marking_unsupported(card, sample=True):
            self._live_trace(
                f"CAL_START_BLOCKED station={station.value} "
                f"reason=single_mode_unsupported")
            raise RuntimeError(
                UiTextCatalog.message(self._language, "single_mode_unsupported"))
        calibration.begin_validation()
        # 校准周期内部冻结型号/人员（单测模式）；样件周期默认不打码。
        if controller.record is None:
            part_no = card.part_no.currentText().strip()
            if part_no:
                try:
                    config = self._model_for(part_no)
                except Exception as exc:
                    self._live_trace(
                        f"CAL_CYCLE_NOT_CREATED station={station.value} {type(exc).__name__}: {exc}")
                    raise
                selection = CycleSelection(station, part_no,
                                           card.staff.currentText().strip() or "Operator",
                                           "single", str(config.ateq_program),
                                           date_scheme=self._resolved_date_scheme(config))
                controller.start_cycle(selection, sample=True)
                card.refresh()
                self._live_trace(f"CAL_CYCLE_READY station={station.value} cycle={controller.record.cycle_id} program={selection.ateq_program}")
            else:
                self._live_trace(f"CAL_CYCLE_NOT_CREATED station={station.value} no selected model")
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

    def calibration_sample(self, result, station=None):
        station = station or self.station
        calibration = self.calibration[station]
        result = str(result).strip().upper()
        expected = "NG" if calibration.phase is CalibrationPhase.WAIT_NG else "OK"
        if result != expected:
            raise ValueError("校准样件顺序错误")
        self._live_trace(f"CAL_SAMPLE station={station.value} result={result}")
        phase = calibration.sample(result)
        # OK 样件验证确认后，把单测留下的状态归档复位，允许后续正常周期启动。
        if result == "OK" and phase is CalibrationPhase.COMPLETE:
            card = self._card_for_station(station)
            controller = card.controller
            if controller.record is not None:
                if controller.phase is Phase.MARKING:
                    # mark_samples 开启时样件同样打码。
                    card.mark()
                if controller.phase is Phase.MARKING:
                    controller.phase = Phase.COMPLETE
                if controller.phase is not Phase.COMPLETE:
                    raise RuntimeError(
                        f"校准周期尚未结束，不能释放工位：{controller.phase.value}")
                cycle_id = controller.record.cycle_id
                controller.reset()
                self._live_trace(
                    f"CAL_VALIDATION_RELEASED station={station.value} cycle={cycle_id}")
        # 验证通过后清灯并启动倒计时。
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
            self.security.require("recovery_resolve"); card=self._card_for_station(station); card.controller.resolve_recovery(reason); card._error_key = None; card.reconnect_plc(); card.refresh(); self.recovery_status.setText({"中文": f"工位 {station.value} 已审计归档", "English": f"Station {station.value} archived", "Français": f"Poste {station.value} archivé"}[self._language])
        except Exception: self.recovery_status.setText({"中文": "恢复拒绝：权限不足", "English": "Recovery denied: permission required", "Français": "Récupération refusée : autorisation requise"}[self._language])


StationCard = StationPanel
