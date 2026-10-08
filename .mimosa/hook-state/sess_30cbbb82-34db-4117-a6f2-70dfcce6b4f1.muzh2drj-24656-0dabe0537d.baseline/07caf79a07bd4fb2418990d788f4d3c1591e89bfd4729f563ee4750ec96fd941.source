"""Screenshot-structured Main.vi replica backed by the verified SIMULATE services.

The visual hierarchy follows the supplied four Main.vi captures: mirrored A/B
station panels on Main and Query, a parameter grid on Setup, and six mirrored
manual controls. Hardware adapters are deliberately not used here.
"""
from __future__ import annotations

import csv
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Phase, StationId, Result
from .ateq import FakeAteq
from .config import Settings
from .journal import CycleJournal
from .license import LicenseVerifier
from .permissions import AuthSession, SecurityContext
from .plc import FakePlc, POINTS
from .printer import FakePrinter
from .repository import FakeRepository
from .station import StationController
from .calibration import Calibration
from .scanner import ScannerFramer, ScannerGuard
from .settings_service import ProductSettingsService
from .ui_theme import METRICS, PALETTE, UiTextCatalog, stylesheet

from PySide6.QtCore import Qt, QTime, QDateTime, QDate
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
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


class StationPanel(QFrame):
    def __init__(self, station, repository, printer, plc, journal, security,
                 product_provider, confirm_callback, changed_callback):
        super().__init__()
        self.station, self.repository, self.plc = station, repository, plc
        self.security, self.confirm_callback = security, confirm_callback
        self.changed_callback = changed_callback
        self._error_key = None
        self.controller = StationController(station, repository, printer, FakeAteq(), journal,
            safe_stop=plc, license_status=security.license_status, security=security)
        self.setObjectName(f"stationCard_{station.value}")
        self.setFrameShape(QFrame.Shape.Box)
        outer = QVBoxLayout(self); outer.setContentsMargins(14, 4, 14, 4); outer.setSpacing(2)
        self.station_title = QLabel(f"工位 {station.value} / Station {station.value}"); self.station_title.setObjectName("stationTitle"); outer.addWidget(self.station_title)
        top = QGridLayout(); top.setHorizontalSpacing(10); top.setVerticalSpacing(2); top.setObjectName(f"stationTopControls_{station.value}")
        self.mode_button = QPushButton(f"Single Test / 单测 {station.value}"); self.mode_button.setObjectName(f"single_dual_{station.value}"); self.mode_button.setCheckable(True); top.addWidget(QLabel(f"Single/Dual {station.value}"), 0, 0); top.addWidget(self.mode_button, 1, 0)
        self.code_input = QLineEdit(); self.code_input.setObjectName(f"main_code_{station.value}"); self.code = self.code_input; top.addWidget(QLabel(f"2D Code {station.value}"), 0, 1); top.addWidget(self.code_input, 1, 1)
        self.total_today = QSpinBox(); self.total_today.setObjectName(f"total_today_{station.value}"); self.total_today.setReadOnly(True); self.total_today.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons); top.addWidget(QLabel(f"Total Today {station.value}"), 0, 2); top.addWidget(self.total_today, 1, 2)
        self.ok_today = QSpinBox(); self.ok_today.setObjectName(f"ok_today_{station.value}"); self.ok_today.setReadOnly(True); self.ok_today.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons); top.addWidget(QLabel(f"OK Today {station.value}"), 0, 3); top.addWidget(self.ok_today, 1, 3)
        self.ateq_no = QLineEdit("SIM"); self.ateq_no.setObjectName(f"ateq_no_{station.value}"); top.addWidget(QLabel(f"ATEQ No. {station.value}"), 0, 4); top.addWidget(self.ateq_no, 1, 4)
        self.part_no = QLineEdit(); self.part_no.setObjectName(f"part_no_{station.value}"); self.part_no.setPlaceholderText("Part No."); top.addWidget(QLabel(f"Part No. {station.value}"), 0, 5); top.addWidget(self.part_no, 1, 5)
        self.staff = QComboBox(); self.staff.setObjectName(f"staff_{station.value}"); self.staff.addItems(["", "Operator", "管理员"]); top.addWidget(QLabel(f"Staff {station.value}"), 0, 6); top.addWidget(self.staff, 1, 6)
        for column in range(7):
            top.setColumnMinimumWidth(column, 0); top.setColumnStretch(column, 1)
        for index in range(top.count()):
            child = top.itemAt(index).widget()
            if child is not None:
                child.setMinimumWidth(0)
                if isinstance(child, QLabel):
                    child.setWordWrap(True); child.setMaximumWidth(110)
        outer.addLayout(top)
        alerts = QHBoxLayout(); alerts.setSpacing(8); self.scanner_indicator = QLabel("●"); self.scanner_indicator.setObjectName(f"scanner_indicator_{station.value}"); self.scanner_indicator.setProperty("state", "ng"); alerts.addWidget(QLabel("Scanner")); alerts.addWidget(self.scanner_indicator); self.reprint = QPushButton("标签重打 / Reprint"); self.reprint.setObjectName(f"reprint_{station.value}"); self.reprint.setProperty("compact", True); self.reprint.setFixedHeight(32); self.reprint.clicked.connect(self.reprint_label); alerts.addWidget(self.reprint); alerts.addWidget(QLabel("看门狗 / Watchdog")); self.watchdog = QSpinBox(); self.watchdog.setObjectName(f"watchdog_{station.value}"); self.watchdog.setProperty("compact", True); self.watchdog.setRange(0, 999); self.watchdog.setValue(0); self.watchdog.setFixedHeight(30); alerts.addWidget(self.watchdog); alerts.addStretch(1); outer.addLayout(alerts)
        self.table = self._table(f"{station.value}List")
        self.list_title = QLabel(f"{station.value} List"); self.list_title.setObjectName("pageTitle"); self.list_title.setVisible(False); outer.addWidget(self.list_title); outer.addWidget(self.table, 1)
        self._outer_layout = outer
        self.error_summary = QLabel(); self.error_summary.setObjectName(f"error_summary_{station.value}"); self.error_summary.setWordWrap(True); self.error_summary.setMinimumWidth(0); self.error_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); self.error_summary.setVisible(False); self.error_summary.setProperty("state", "ng"); outer.addWidget(self.error_summary)
        bottom = QGroupBox(); bottom.setObjectName(f"bottomIndicators_{station.value}"); bottom_layout = QVBoxLayout(bottom); bottom_layout.setContentsMargins(2, 2, 2, 2); bottom_layout.setSpacing(2)
        indicator_row = QHBoxLayout(); indicator_row.setSpacing(2); self._indicator_row = indicator_row
        self.indicators = {}
        self.indicator_labels = {}
        for signal, label in INDICATOR_NAMES:
            box = QVBoxLayout(); box.setContentsMargins(2, 2, 2, 2); box.setSpacing(1); line = QVBoxLayout(); line.setContentsMargins(0, 0, 0, 0); line.setSpacing(0); led = QLabel("●"); led.setAlignment(Qt.AlignmentFlag.AlignCenter); led.setObjectName(f"{signal}_{station.value}"); led.setProperty("state", "ok"); self.indicators[signal] = led; line.addWidget(led); text_label = QLabel(INDICATOR_DISPLAY_LABELS["base"][len(self.indicator_labels)]); text_label.setAlignment(Qt.AlignmentFlag.AlignCenter); text_label.setWordWrap(True); text_label.setMinimumWidth(0); text_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred); self.indicator_labels[signal] = text_label; line.addWidget(text_label); box.addLayout(line); button = QPushButton("OK"); button.setMinimumWidth(0); button.setMinimumHeight(30); button.setMaximumHeight(32); button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed); button.setObjectName(f"{('start_' + station.value) if signal == 'start_validation' else (signal + '_button_' + station.value)}"); button.setProperty("primary", True); button.clicked.connect(lambda _=False, s=signal: self._indicator_action(s)); box.addWidget(button)
            self._fit_indicator_label(text_label); box.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize); indicator_row.addLayout(box)
        self.next_action = QLabel(); self.next_action.setObjectName(f"next_action_{station.value}"); self.next_action.setWordWrap(True); self.next_action.setMinimumWidth(0); self.next_action.setMaximumWidth(110); indicator_row.addWidget(self.next_action, 1)
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
        self.refresh()

    @staticmethod
    def _configure_table(table):
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(52)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setTextElideMode(Qt.TextElideMode.ElideNone)
        header.setFixedHeight(max(40, METRICS.table_header_height))
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setWordWrap(True)
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
        label.setMinimumHeight(label.sizeHint().height())
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    @staticmethod
    def _table(name):
        table = QTableWidget(30, 10); table.setObjectName(name); table.setHorizontalHeaderLabels(DISPLAY_HEADERS["base"]); table.setAlternatingRowColors(True); table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed); table.verticalHeader().setDefaultSectionSize(METRICS.table_row_height); table.verticalHeader().setMinimumSectionSize(METRICS.table_row_height); StationPanel._configure_table(table); [table.setRowHeight(i, METRICS.table_row_height) for i in range(30)]; table.setMinimumHeight(340); return table

    def _point_read(self, signal):
        byte, bit = POINTS[signal][self.station]; return self.plc.read_bit(byte, bit)

    def _indicator_action(self, signal):
        if signal == "start_validation":
            self.manual_action("start", self.indicators[signal])

    def _m(self, value, field):
        return "" if value is None else f"{getattr(value, field):g}"

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
        code = self.code_input.text() if code is None else code; part_no = part_no or self.part_no.text()
        try:
            self.controller.scan(code, part_no, self.staff.currentText())
            byte, bit = POINTS["scan_ok"][self.station]; self.plc.write_bit(byte, bit, True); self._error_key = None; self.refresh(); self.changed_callback(); return True
        except Exception:
            self._error_key = "scan_error"; self.refresh(); return False

    def first(self):
        try: self.controller.test_first(); self._error_key = None
        except Exception: self._error_key = "first_error"
        self.refresh(); self.changed_callback()

    def second(self):
        try: self.controller.test_second(); self._error_key = None
        except Exception: self._error_key = "second_error"
        self.refresh(); self.changed_callback()

    def label(self):
        try: self.controller.label(); self._error_key = None
        except Exception: self._error_key = "label_error"
        self.refresh(); self.changed_callback()

    def reprint_label(self):
        try:
            self.security.require("reprint")
            if self.controller.record is None: raise RuntimeError("没有可重打周期")
            self.controller.label(); self._error_key = None
        except Exception: self._error_key = "reprint_denied"
        self.refresh(); self.changed_callback()

    def reset(self):
        try: self.controller.reset(); self.plc.safe_stop(f"UI reset {self.station.value}"); self._error_key = None
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
        return [r for r in self.repository.records.values() if r.station is self.station]

    def refresh(self):
        c = self.controller; rows = self._records(); self.total_today.setValue(len(rows)); self.ok_today.setValue(sum(1 for r in rows if r.second and r.second.result is Result.OK)); self.scanner_indicator.setProperty("state", "ok" if c.phase is Phase.READY else "ng"); self.scanner_indicator.style().unpolish(self.scanner_indicator); self.scanner_indicator.style().polish(self.scanner_indicator)
        for row in range(30):
            values = ["", "", "", "", "", "", "", "", "", ""]
            if row < len(rows):
                record = rows[row]; values = [record.created_at.astimezone().strftime("%H:%M:%S"), record.serial_no, record.code_2d, self._m(record.first, "pressure"), self._m(record.first, "leakage"), self._m(record.second, "pressure"), self._m(record.second, "leakage"), (record.second or record.first).result.value if (record.second or record.first) else "", record.part_no, record.person]
            for col, value in enumerate(values): self.table.setItem(row, col, QTableWidgetItem(str(value)))
        for signal, _ in INDICATOR_NAMES:
            plc_signal = "start" if signal == "start_validation" else signal
            value = self._point_read(plc_signal) if plc_signal in POINTS else False; self.indicators[signal].setText("●"); self.indicators[signal].setProperty("state", "ok" if value else "info"); self.indicators[signal].style().unpolish(self.indicators[signal]); self.indicators[signal].style().polish(self.indicators[signal])
        language = getattr(self.window(), "_language", "中文")
        prompts = {"中文": {Phase.IDLE: "扫码", Phase.READY: "一测", Phase.WAIT_2: "二测", Phase.LABELING: "贴标", Phase.COMPLETE: "复位或查询", Phase.FAULT: "管理员恢复"}, "English": {Phase.IDLE: "Scan", Phase.READY: "Test 1", Phase.WAIT_2: "Test 2", Phase.LABELING: "Label", Phase.COMPLETE: "Reset or query", Phase.FAULT: "Admin recovery"}, "Français": {Phase.IDLE: "Scanner", Phase.READY: "Test 1", Phase.WAIT_2: "Test 2", Phase.LABELING: "Étiqueter", Phase.COMPLETE: "Réinitialiser ou requête", Phase.FAULT: "Récupération admin"}}
        narrow_error = bool(self._error_key) and self.window().width() < 1600
        self._move_recovery_widgets(narrow_error)
        self.error_details.setVisible(narrow_error)
        self.next_action.setVisible(not narrow_error)
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
    def __init__(self, language: str = "中文"):
        super().__init__(); self.setWindowTitle("Leak Test 2 Channels / 气密检测"); self.resize(METRICS.canonical_width, METRICS.canonical_height); self.setMinimumSize(1100, 700)
        app = QApplication.instance(); family = "Segoe UI"
        for font_path in (Path(r"C:\Windows\Fonts\Noto Sans SC (TrueType).otf"), Path(r"C:\Windows\Fonts\simsun.ttc")):
            if font_path.exists():
                fid = QFontDatabase.addApplicationFont(str(font_path)); families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
                if families: family = families[0]; break
        if app: app.setFont(QFont(family))
        self.setStyleSheet(stylesheet())
        config_path = Path(__file__).parents[1] / "config" / "default.toml"
        self.settings = Settings.from_toml(config_path) if config_path.exists() else Settings()
        # Setup.ini is read-only characterization evidence.  It is used only
        # when both A/B keys validate; otherwise the UI shows BLOCKED instead
        # of presenting sample COM values as production truth.
        self.config_warning = ""
        try:
            if self.settings.setup_path.exists():
                self.settings = Settings.from_file(self.settings.setup_path)
            else:
                self.config_warning = "Setup.ini 未找到 / BLOCKED"
        except Exception as exc:
            self.config_warning = f"配置未确认 / BLOCKED: {exc}"
            self.settings = Settings(config_source="blocked", ports_confirmed=False)
        self.repository, self.printer, self.plc = FakeRepository(self.settings), FakePrinter(), FakePlc(); self.calibration = Calibration(); self.security = SecurityContext(AuthSession(demo=True), LicenseVerifier(simulator=True).verify(b"SIMULATE", b"SIMULATE-SIGNATURE")); self.product_settings = ProductSettingsService(self.security); self.scanner_framers = {s: ScannerFramer() for s in StationId}; self.scanner_guards = {s: ScannerGuard() for s in StationId}; self.confirmation_callback = self._confirm_output; self._setup_values = {"customer_no":"", "ateq_no":"SIM", "staff":""}; self.journal_dir = Path(tempfile.mkdtemp(prefix="LeakTest2Channels-replica-")); self.tabs = CompatibilityTabs(); self._language = language if language in UiTextCatalog.LANGUAGES else "中文"; self._i18n_widgets = []
        self.cards = [StationPanel(s, self.repository, self.printer, self.plc, CycleJournal(self.journal_dir / f"{s.value}.json"), self.security, self.product_settings.current_product, lambda station, signal, requested, current: self.confirmation_callback(station, signal, requested, current), self.refresh_all) for s in StationId]
        self._build_main(); self._build_setup(); self._build_query(); self._build_manual(); self.setCentralWidget(self.tabs)
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

    def _build_main(self):
        page = QWidget(); page.setObjectName("page"); root = QVBoxLayout(page); root.setContentsMargins(METRICS.page_margin, 8, METRICS.page_margin, 8); root.setSpacing(METRICS.station_gap); ports = "/".join(self.settings.ateq_ports) if self.settings.ports_confirmed else "BLOCKED/未确认"; self.system_status = QLabel(f"模式：SIMULATE | PLC：{self.settings.plc_ip} | ATEQ：{ports} | Fake services"); self.system_status.setObjectName("systemStatusBar"); self.system_status.setVisible(False); root.addWidget(self.system_status); top = QHBoxLayout(); top.setSpacing(METRICS.station_gap); top.addWidget(self.cards[0], 1); top.addWidget(self.cards[1], 1); root.addLayout(top, 1)
        # Match Main.vi footer hierarchy: A indicators | login | B indicators
        # | calibration countdown.  The indicator groups are still bound to
        # each StationController and PLC readback.
        footer = QHBoxLayout(); footer.setSpacing(METRICS.station_gap); footer.addWidget(self.cards[0].bottom_indicators, 4); footer.addWidget(self._login_panel(), 3); footer.addWidget(self.cards[1].bottom_indicators, 4); self.calibration_countdown = QSpinBox(); self.calibration_countdown.setObjectName("calibration_countdown"); self.calibration_countdown.setRange(0,999); self.calibration_label = QLabel("校准倒计时 / Calibration Countdown"); self.calibration_label.setObjectName("calibration_countdown_label"); self.calibration_label.setWordWrap(True); self.calibration_label.setMaximumWidth(120); self.calibration_label.setMinimumWidth(0); footer.addWidget(self.calibration_label); footer.addWidget(self.calibration_countdown); root.addLayout(footer)
        for widget in (self.cards[0].bottom_indicators, self.cards[1].bottom_indicators): widget.setMaximumHeight(METRICS.footer_max_height)
        scanner = QHBoxLayout(); scanner.setSpacing(8); self.scanner_input = QLineEdit(); self.scanner_input.setObjectName("scanner_input"); self.scanner_input.setPlaceholderText("Scanner frame / 扫码帧"); scanner.addWidget(self.scanner_input); self.scanner_status = QLabel("Scanner ready / 扫码器就绪"); self.scanner_status.setObjectName("scannerStatus"); scanner.addWidget(self.scanner_status)
        for s in StationId:
            b = QPushButton(f"扫码到 {s.value}"); b.setMinimumWidth(0); b.setProperty("primary", True); b.setObjectName(f"scanner_route_{s.value}"); b.clicked.connect(lambda _=False, station=s: self.route_scanner_text(station)); scanner.addWidget(b)
        for index in range(scanner.count()):
            child = scanner.itemAt(index).widget()
            if child is not None:
                child.setMinimumWidth(0)
        self.scanner_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.scanner_input.setMinimumHeight(METRICS.input_height); self.scanner_status.setMinimumHeight(METRICS.scanner_height); root.addLayout(scanner); self.tabs.addTab(page, "测试/Main/Principale")

    def _login_panel(self):
        box = QGroupBox("登录 / Login / Connexion"); box.setObjectName("loginPanel"); layout = QVBoxLayout(box); layout.setContentsMargins(10, 12, 10, 8); layout.setSpacing(6)
        credentials = QHBoxLayout(); credentials.setSpacing(6); self.username = QLineEdit(); self.username.setObjectName("login_username"); self.username.setPlaceholderText("登录 Role"); self.password = QLineEdit(); self.password.setObjectName("login_password"); self.password.setPlaceholderText("密码 Password"); self.password.setEchoMode(QLineEdit.EchoMode.Password); credentials.addWidget(self.username); credentials.addWidget(self.password); layout.addLayout(credentials)
        actions = QHBoxLayout(); actions.setSpacing(6); login = QPushButton("确定 / Confirm"); login.setProperty("primary", True); login.setObjectName("login_button"); login.clicked.connect(self.login); exit_b = QPushButton("退出 / Exit"); exit_b.setObjectName("exit_button"); exit_b.setProperty("destructive", True); exit_b.clicked.connect(self.controlled_exit); actions.addWidget(login); actions.addWidget(exit_b); self.login_status = QLabel("未登录 / operator"); self.login_status.setProperty("state", "info"); actions.addWidget(self.login_status, 1); layout.addLayout(actions); box.setMaximumHeight(METRICS.footer_max_height); return box

    def _build_setup(self):
        page = QWidget(); page.setObjectName("page"); root = QHBoxLayout(page); root.setContentsMargins(METRICS.page_margin, 12, METRICS.page_margin, 12); root.setSpacing(METRICS.station_gap); left = QVBoxLayout(); title = QLabel("参数设置 / Setup / Coup monté"); title.setObjectName("pageTitle"); left.addWidget(title); self.setup_table = QTableWidget(40,4); self.setup_table.setObjectName("setupParameterTable"); self.setup_table.setHorizontalHeaderLabels(["Part No.", "Customer No.", "ATEQ No.", "Staff"]); self.setup_table.verticalHeader().setDefaultSectionSize(METRICS.table_row_height); self.setup_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); StationPanel._configure_table(self.setup_table); left.addWidget(self.setup_table,1); root.addLayout(left, 62)
        right_card = QFrame(); right_card.setObjectName("settingsCard"); right = QVBoxLayout(right_card); right.setContentsMargins(20, 20, 20, 20); right.setSpacing(10); self.product_edit = QLineEdit(self.product_settings.current_product()); self.product_edit.setObjectName("product_draft"); self.product_edit.setPlaceholderText("Part No. draft"); right.addWidget(QLabel("Part No. / 产品型号")); right.addWidget(self.product_edit); self.language_selector = QComboBox(); self.language_selector.setObjectName("language_selector"); self.language_selector.addItems(list(UiTextCatalog.LANGUAGES)); self.language_selector.currentTextChanged.connect(self.language_changed); right.addWidget(QLabel("Language")); right.addWidget(self.language_selector); self.setup_scanner = QLabel("● Scanner"); self.setup_scanner.setObjectName("setup_scanner_indicator"); self.setup_scanner.setProperty("state", "ng"); right.addWidget(self.setup_scanner)
        port_items = list(self.settings.ateq_ports) if self.settings.ports_confirmed else ["BLOCKED/未确认"]
        self.ateq_a = QComboBox(); self.ateq_a.setObjectName("ateq_com_A"); self.ateq_a.addItems(port_items); self.ateq_a.setCurrentText(self.settings.ateq_ports[0] if self.settings.ports_confirmed else "BLOCKED/未确认"); self.ateq_a.setEnabled(self.settings.ports_confirmed); self.ateq_com_A = self.ateq_a
        self.ateq_b = QComboBox(); self.ateq_b.setObjectName("ateq_com_B"); self.ateq_b.addItems(port_items); self.ateq_b.setCurrentText(self.settings.ateq_ports[1] if self.settings.ports_confirmed else "BLOCKED/未确认"); self.ateq_b.setEnabled(self.settings.ports_confirmed); self.ateq_com_B = self.ateq_b
        right.addWidget(QLabel("ATEQ F620 A (Restart Software to Active)")); right.addWidget(self.ateq_a); right.addWidget(QLabel("ATEQ F620 B (Restart Software to Active)")); right.addWidget(self.ateq_b); self.cal_period = QTimeEdit(QTime(0,0)); self.cal_period.setObjectName("calibration_period"); right.addWidget(QLabel("Calibration Period (Hours)")); right.addWidget(self.cal_period); save = QPushButton("保存设置 / Save Parameters"); save.setProperty("primary", True); save.setObjectName("save_settings"); save.clicked.connect(self.save_settings); right.addWidget(save); self.settings_status = QLabel("已提交：SIM-PART"); self.settings_status.setObjectName("settingsStatus"); right.addWidget(self.settings_status); self.calibration_status = QLabel("等待 NG 样件"); self.calibration_status.setObjectName("calibrationStatus"); right.addWidget(self.calibration_status); right.addStretch(1); root.addWidget(right_card,38); self.tabs.addTab(page, "设置/Setup/Coup Monté")

    def _build_query(self):
        page = QWidget(); page.setObjectName("page"); root = QVBoxLayout(page); root.setContentsMargins(METRICS.page_margin, 12, METRICS.page_margin, 12); root.setSpacing(METRICS.station_gap); filters = QHBoxLayout(); filters.setSpacing(METRICS.station_gap); self.query_fields = {}
        for s in StationId:
            group = QGroupBox(f"{s.value} List Search"); group.setObjectName(f"queryFilters_{s.value}"); grid = QGridLayout(group); grid.setContentsMargins(10, 16, 10, 10); grid.setVerticalSpacing(6)
            for row, (key, label) in enumerate((("start", "Start Time"),("finish", "Finish Time"),("code", "2D Code"),("result", "Result"))):
                if key in {"start", "finish"}:
                    widget = QDateTimeEdit(QDateTime.currentDateTime()); widget.setCalendarPopup(True); widget.setDisplayFormat("yyyy-MM-dd HH:mm:ss"); widget.setDateTime(QDateTime.currentDateTime().addDays(-1 if key == "start" else 1))
                else:
                    widget = QLineEdit()
                widget.setObjectName(f"query_{key}_{s.value}"); self.query_fields[(s,key)] = widget; grid.addWidget(QLabel(f"{label} {s.value}"),row,0); grid.addWidget(widget,row,1)
            status = QLabel(); status.setObjectName(f"query_status_{s.value}"); self.query_status = getattr(self, "query_status", {}); self.query_status[s] = status; grid.addWidget(status,5,0,1,2)
            search = QPushButton(f"Search {s.value}"); search.setObjectName(f"query_search_{s.value}"); search.clicked.connect(self.refresh_query); download = QPushButton(f"Download {s.value}"); download.setObjectName(f"query_download_{s.value}"); download.clicked.connect(lambda _=False, station=s: self.download_query(station)); grid.addWidget(search,4,0); grid.addWidget(download,4,1); filters.addWidget(group)
        root.addLayout(filters); tables = QHBoxLayout(); tables.setSpacing(METRICS.station_gap); self.query_tables = {}
        for s in StationId:
            table = QTableWidget(30,10); table.setObjectName(f"query_table_{s.value}"); table.setHorizontalHeaderLabels(DISPLAY_HEADERS["base"]); table.setAlternatingRowColors(True); table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed); table.verticalHeader().setDefaultSectionSize(METRICS.table_row_height); StationPanel._configure_table(table); [table.setRowHeight(i, METRICS.table_row_height) for i in range(30)]; self.query_tables[s] = table; tables.addWidget(table, 1)
        root.addLayout(tables,1); self.query_edit = self.query_fields[(StationId.A,"code")]; self.table = self.query_tables[StationId.A]; self.tabs.addTab(page, "查询/Query/Requête")

    def _build_manual(self):
        page = QWidget(); page.setObjectName("page"); root = QHBoxLayout(page); root.setContentsMargins(METRICS.page_margin, 12, METRICS.page_margin, 12); root.setSpacing(METRICS.station_gap)
        self._manual_extensions = []
        for s in StationId:
            group = QGroupBox(f"工位 {s.value} / Station {s.value}"); group.setObjectName(f"manualGroup_{s.value}"); group.setProperty("stationTitle", True); layout = QVBoxLayout(group); layout.setContentsMargins(16, 20, 16, 16); layout.setSpacing(10)
            for signal, title, states in MANUAL_NAMES:
                row = QHBoxLayout(); row.setSpacing(8); label_widget = QLabel(f"{s.value}{title}"); label_widget.setObjectName(f"manual_label_{signal}_{s.value}"); label_widget.setMinimumWidth(220); row.addWidget(label_widget, 1)
                # The legacy unsuffixed button remains the safe toggle entry
                # point for existing callers; explicit target buttons below
                # make the intended PLC state unambiguous to operators.
                button = QPushButton("状态切换 / Toggle"); button.setObjectName(f"manual_{signal}_{s.value}"); button.clicked.connect(lambda _=False, station=s, sig=signal, b=button: self.manual_output(b, station, sig)); row.addWidget(button)
                if signal in {"clamp", "transfer", "block", "stamp"}:
                    for state, text in ((False, UiTextCatalog.action(self._language, "back")), (True, UiTextCatalog.action(self._language, "forward"))):
                        target = QPushButton(text); target.setObjectName(f"manual_{signal}_{s.value}_{'forward' if state else 'back'}"); target.clicked.connect(lambda _=False, station=s, sig=signal, value=state, b=target: self._manual_target_action(station, sig, value, b)); row.addWidget(target)
                elif signal == "door_disable":
                    for state, key in ((False, "enable"), (True, "disable")):
                        target = QPushButton(UiTextCatalog.action(self._language, key)); target.setObjectName(f"manual_{signal}_{s.value}_{key}"); target.clicked.connect(lambda _=False, station=s, sig=signal, value=state, b=target: self._manual_target_action(station, sig, value, b)); row.addWidget(target)
                else:
                    for state, key in ((False, "automatic"), (True, "manual")):
                        target = QPushButton(UiTextCatalog.action(self._language, key)); target.setObjectName(f"manual_{signal}_{s.value}_{key}"); target.clicked.connect(lambda _=False, station=s, sig=signal, value=state, b=target: self._manual_target_action(station, sig, value, b)); row.addWidget(target)
                readback = QLabel("● OFF"); readback.setObjectName(f"manual_readback_{signal}_{s.value}"); readback.setProperty("state", "info"); readback.setAlignment(Qt.AlignmentFlag.AlignCenter); row.addWidget(readback)
                layout.addLayout(row)
            # Compatibility aliases for the diagnostic pressure/start points;
            # they remain hidden so the operator page has exactly six rows.
            for signal, title in (("pressure", "正/负压 / Pressure"), ("start", "启动 / Start")):
                alias = QPushButton(f"{title}: OFF"); alias.setObjectName(f"manual_{signal}_{s.value}"); alias.clicked.connect(lambda _=False, station=s, sig=signal, b=alias: self.manual_output(b, station, sig)); alias.setVisible(False); self._manual_extensions.append(alias); layout.addWidget(alias)
            root.addWidget(group, 1)
        recovery = QGroupBox("管理员恢复 / Recovery"); recovery.setObjectName("recoveryPanel"); recovery.setVisible(False); self._manual_recovery = recovery; rl = QVBoxLayout(recovery); self.recovery_reason = QLineEdit(); self.recovery_reason.setObjectName("recovery_reason"); self.recovery_reason.setPlaceholderText("处理原因 / reason"); rl.addWidget(self.recovery_reason); self.recovery_status = QLabel("无恢复操作"); self.recovery_status.setObjectName("recoveryStatus"); rl.addWidget(self.recovery_status)
        for s in StationId:
            b = QPushButton(f"归档工位 {s.value}"); b.setObjectName(f"resolve_recovery_{s.value}"); b.clicked.connect(lambda _=False, station=s: self.resolve_recovery(station, self.recovery_reason.text() or "UI 人工确认")); rl.addWidget(b)
        root.addWidget(recovery); toggle = QPushButton("显示扩展 / Extensions"); toggle.setObjectName("manual_extension_toggle"); toggle.clicked.connect(self.toggle_manual_extension); root.addWidget(toggle); self.tabs.addTab(page, "手动/Manual/Manuelle")

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
        self.setup_table.setHorizontalHeaderLabels({"中文": ["产品型号", "客户编号", "ATEQ 编号", "人员"], "English": ["Part No.", "Customer No.", "ATEQ No.", "Staff"], "Français": ["N° pièce", "N° client", "N° ATEQ", "Personnel"]}[value])
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
                card.indicator_labels[signal].setText(label_text); StationPanel._fit_indicator_label(card.indicator_labels[signal])
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
        self.calibration_label.setText({"中文": "校准倒计时", "English": "Calibration countdown", "Français": "Compte à rebours calibration"}[value])
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
        try:
            committed = self.product_settings.save(self.product_edit.text())
            self.settings_status.setText({"中文": f"已提交：{committed.product_no}", "English": f"Committed: {committed.product_no}", "Français": f"Validé : {committed.product_no}"}[self._language])
            self._setup_values.update(ateq_no=self.ateq_a.currentText(), staff=self.setup_table.item(0,3).text() if self.setup_table.item(0,3) else "")
        except Exception: self.settings_status.setText({"中文": "设置拒绝：权限不足", "English": "Settings denied: permission required", "Français": "Paramètres refusés : autorisation requise"}[self._language])
    def route_scanner_code(self, station, code):
        if not self.scanner_guards[station].accept(code): raise ValueError({"中文": "扫码为空或重复", "English": "Empty or duplicate scan", "Français": "Scan vide ou dupliqué"}[self._language])
        card = self.cards[0 if station is StationId.A else 1]; previous = card.code_input.text(); card.code_input.setText(code); ok = card.scan(code, self.product_settings.current_product())
        if not ok or card.controller.phase is not Phase.READY or not card.controller.record or card.controller.record.code_2d != code.strip():
            card.code_input.setText(previous)
            raise RuntimeError({"中文": "扫码未进入就绪状态", "English": "Scan did not enter READY", "Français": "Le scan n'est pas passé à PRÊT"}[self._language])
        self.scanner_status.setText({"中文": f"已路由到工位 {station.value}", "English": f"Routed to station {station.value}", "Français": f"Routé vers le poste {station.value}"}[self._language])
    def route_scanner_text(self, station):
        try:
            frames = self.scanner_framers[station].feed((self.scanner_input.text()+"\r\n").encode("utf-8"));
            if len(frames) != 1: raise ValueError({"中文": "扫码帧不完整", "English": "Incomplete scanner frame", "Français": "Trame scanner incomplète"}[self._language])
            self.route_scanner_code(station, frames[0])
        except Exception as exc: self.scanner_status.setText({"中文": f"扫码错误：{exc}", "English": f"Scanner error: {exc}", "Français": f"Erreur scanner : {exc}"}[self._language])
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
        return [r for r in rows if start <= r.created_at.astimezone(start.tzinfo) <= finish and (not needle or needle in r.code_2d.lower()) and (not result or result in ((r.second or r.first).result.value.lower() if (r.second or r.first) else ""))]
    def refresh_query(self):
        for station, table in self.query_tables.items():
            rows = self._query_records(station); table.setRowCount(30)
            for i in range(30):
                vals = ["", "", "", "", "", "", "", "", "", ""]
                if i < len(rows):
                    r=rows[i]; vals=[r.created_at.astimezone().strftime("%H:%M:%S"),r.serial_no,r.code_2d,StationPanel._m(self,r.first,"pressure") if r.first else "",StationPanel._m(self,r.first,"leakage") if r.first else "",StationPanel._m(self,r.second,"pressure") if r.second else "",StationPanel._m(self,r.second,"leakage") if r.second else "",(r.second or r.first).result.value if (r.second or r.first) else "",r.part_no,r.person]
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
    def calibration_sample(self, result):
        phase = self.calibration.sample(result)
        self.calibration_countdown.setValue(self.calibration.countdown)
        phase_text = {
            "中文": {"等待NG样件": "等待NG样件", "等待OK样件": "等待OK样件", "校准完成": "校准完成"},
            "English": {"等待NG样件": "Waiting for NG sample", "等待OK样件": "Waiting for OK sample", "校准完成": "Calibration complete"},
            "Français": {"等待NG样件": "En attente de l'échantillon NG", "等待OK样件": "En attente de l'échantillon OK", "校准完成": "Calibration terminée"},
        }[self._language][phase.value]
        if self.calibration.sample_demand:
            demand = {"中文": f"等待{self.calibration.sample_demand}样件", "English": f"Waiting for {self.calibration.sample_demand} sample", "Français": f"En attente de l'échantillon {self.calibration.sample_demand}"}[self._language]
        else:
            demand = phase_text
        return f"{phase_text} | {self.calibration.countdown} | {demand}"
    def on_calibration_sample(self, result):
        try: self.calibration_status.setText(self.calibration_sample(result))
        except Exception: self.calibration_status.setText({"中文": "校准错误：样件顺序无效", "English": "Calibration error: invalid sample order", "Français": "Erreur de calibration : ordre d'échantillon invalide"}[self._language])
    def resolve_recovery(self, station, reason="UI 人工确认"):
        try:
            self.security.require("recovery_resolve"); card=self.cards[0 if station is StationId.A else 1]; card.controller.resolve_recovery(reason); card.refresh(); self.recovery_status.setText({"中文": f"工位 {station.value} 已审计归档", "English": f"Station {station.value} archived", "Français": f"Poste {station.value} archivé"}[self._language])
        except Exception: self.recovery_status.setText({"中文": "恢复拒绝：权限不足", "English": "Recovery denied: permission required", "Français": "Récupération refusée : autorisation requise"}[self._language])


StationCard = StationPanel
