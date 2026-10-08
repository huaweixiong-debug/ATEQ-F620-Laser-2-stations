"""Modern Windows-11 operator dashboard for the two-channel simulator.

This module is imported by :mod:`app.ui` after the compatibility UI. Keeping
the dashboard separate makes the safety/service layer unchanged and gives the
operator a visible Main.vi-equivalent surface in SIMULATE only.
"""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from .models import Phase, StationId

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
        QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
        QPushButton, QScrollArea, QTabWidget, QTableWidget, QTableWidgetItem,
        QVBoxLayout, QWidget)
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
except ImportError:  # pragma: no cover
    QApplication = None

MAINVI_INDICATORS = (("scan_ok", "扫码 OK"), ("calibration_due", "校准到期"),
                     ("ng_sample", "NG 样件需求"), ("ok_sample", "OK 样件需求"),
                     ("door_disable", "安全门旁路"), ("pressure", "正/负压"),
                     ("manual", "手动模式"))
MAINVI_ACTUATORS = (("manual", "手动模式"), ("transfer", "移载"),
                    ("block", "封堵"), ("clamp", "夹紧"),
                    ("stamp", "盖章"), ("pressure", "正/负压"),
                    ("start", "启动 / Start"))


class StationCard(QFrame):
    """Persistent A/B dashboard card and safe simulator controls."""
    def __init__(self, station: StationId, repository: FakeRepository,
                 printer: FakePrinter, plc: FakePlc, journal: CycleJournal,
                 security: SecurityContext, part_no_provider=None,
                 confirm_callback=None) -> None:
        super().__init__()
        self.station, self.plc, self.security = station, plc, security
        self.controller = StationController(station, repository, printer, FakeAteq(), journal,
            safe_stop=plc, license_status=security.license_status, security=security)
        self.part_no_provider = part_no_provider or (lambda: "")
        self.confirm_callback = confirm_callback or self._dialog_confirmation
        self.setObjectName(f"stationCard_{station.value}")
        self.setStyleSheet("QFrame#stationCard_A,QFrame#stationCard_B{background:white;border:1px solid #d7e0ee;border-radius:14px;} QLabel#result_A,QLabel#result_B{font-size:26px;font-weight:700;padding:10px;border-radius:10px;background:#edf3ff;}")
        layout = QVBoxLayout(self)
        title = QLabel(f"工位 {station.value} / Station {station.value}"); title.setObjectName(f"stationTitle_{station.value}"); title.setStyleSheet("font-size:22px;font-weight:650;color:#172b4d;"); layout.addWidget(title)
        self.status = QLabel(); self.status.setObjectName(f"state_{station.value}"); layout.addWidget(self.status)
        self.result = QLabel("等待扫码 / Scan required"); self.result.setObjectName(f"result_{station.value}"); self.result.setAlignment(Qt.AlignmentFlag.AlignCenter); self.result.setMinimumHeight(58); layout.addWidget(self.result)
        self.next_action = QLabel(); self.next_action.setObjectName(f"next_action_{station.value}"); self.next_action.setStyleSheet("color:#315b88;font-weight:600;"); layout.addWidget(self.next_action)
        identity = QGridLayout(); self.serial_label = QLabel("—"); self.serial_label.setObjectName(f"serial_{station.value}"); self.stage1_label = QLabel("—"); self.stage1_label.setObjectName(f"stage1_{station.value}"); self.stage2_label = QLabel("—"); self.stage2_label.setObjectName(f"stage2_{station.value}")
        for row, (label, value) in enumerate((("二维码/序列号", self.serial_label), ("阶段 1 压力/泄漏/判定", self.stage1_label), ("阶段 2 压力/泄漏/判定", self.stage2_label))): identity.addWidget(QLabel(label), row, 0); identity.addWidget(value, row, 1)
        layout.addLayout(identity)
        self.code = QLineEdit(); self.code.setObjectName(f"code_input_{station.value}"); self.code.setPlaceholderText("扫码或输入二维码 / Scan QR"); layout.addWidget(self.code)
        indicator_box = QGroupBox("Main.vi 状态 / Signals"); indicator_box.setObjectName(f"indicators_{station.value}"); indicators = QGridLayout(indicator_box); self.indicators = {}
        for index, (signal, text) in enumerate(MAINVI_INDICATORS):
            label = QLabel(); label.setObjectName(f"{signal}_{station.value}"); self.indicators[signal] = label; indicators.addWidget(QLabel(text), index // 2, (index % 2) * 2); indicators.addWidget(label, index // 2, (index % 2) * 2 + 1)
        layout.addWidget(indicator_box)
        actions = QHBoxLayout()
        for text, callback, name in (("扫码", self.scan, "scan"), ("一测", self.first, "test1"), ("二测", self.second, "test2"), ("贴标", self.label, "label"), ("复位", self.reset, "reset")):
            button = QPushButton(text); button.setObjectName(f"{name}_{station.value}"); button.setMinimumHeight(38); button.clicked.connect(callback); actions.addWidget(button)
        start_button = QPushButton("启动 / Start：OFF"); start_button.setObjectName(f"start_{station.value}"); start_button.setMinimumHeight(38); start_button.clicked.connect(lambda _checked=False, b=start_button: self.manual_action("start", b)); actions.addWidget(start_button)
        layout.addLayout(actions)
        actuator_box = QGroupBox("操作员快捷手动 / Actuators (SIMULATE readback)"); actuator_box.setObjectName(f"dashboardActuators_{station.value}"); actuator_layout = QGridLayout(actuator_box); self.actuator_buttons = {}
        for index, (signal, text) in enumerate(MAINVI_ACTUATORS):
            button = QPushButton(f"{text}：OFF"); button.setObjectName(f"dashboard_{signal}_{station.value}"); button.clicked.connect(lambda _checked=False, s=signal, b=button: self.manual_action(s, b)); self.actuator_buttons[signal] = button; actuator_layout.addWidget(button, index // 3, index % 3)
        layout.addWidget(actuator_box); self.refresh()

    def _point(self, signal): return POINTS[signal][self.station]
    def _read(self, signal):
        byte, bit = self._point(signal); return bool(self.plc.read_bit(byte, bit))
    def manual_action(self, signal, button):
        try:
            self.security.require("manual_output")
            current = self._read(signal); requested = not current
            if not self.confirm_callback(self.station, signal, requested, current):
                button.setText(f"{dict(MAINVI_ACTUATORS)[signal]}：已取消"); return
            byte, bit = self._point(signal); self.plc.write_bit(byte, bit, requested); actual = self._read(signal); button.setText(f"{dict(MAINVI_ACTUATORS)[signal]}：{'ON' if actual else 'OFF'}"); self.refresh()
        except Exception as exc:
            button.setText(f"{dict(MAINVI_ACTUATORS)[signal]}：拒绝"); self.result.setText(f"错误 / ERROR：{exc}")
    def refresh(self):
        c = self.controller; self.status.setText(f"● {c.phase.value} | 打印 {c.print_state.value}" + (f" | {c.error}" if c.error else ""))
        if c.record:
            self.serial_label.setText(c.record.code_2d or "—"); self.stage1_label.setText(self._m(c.record.first)); self.stage2_label.setText(self._m(c.record.second))
            if c.phase is Phase.COMPLETE:
                result = c.record.second or c.record.first; self.result.setText((result.result.value if result else "未知") or "完成 / COMPLETE")
            elif c.phase is Phase.FAULT: self.result.setText("故障 / FAULT · 需人工恢复")
            elif c.phase is Phase.READY: self.result.setText("就绪 / READY")
            else: self.result.setText(c.phase.value)
        else:
            self.serial_label.setText("—"); self.stage1_label.setText("—"); self.stage2_label.setText("—"); self.result.setText("等待扫码 / Scan required" if c.phase is not Phase.FAULT else "故障 / FAULT · 需人工恢复")
        for signal, _text in MAINVI_INDICATORS:
            value = self._read(signal); self.indicators[signal].setText("ON / 是" if value else "OFF / 否"); self.indicators[signal].setStyleSheet("color:#b42318;font-weight:600;" if value else "color:#52606d;")
        for signal, text in MAINVI_ACTUATORS: self.actuator_buttons[signal].setText(f"{text}：{'ON' if self._read(signal) else 'OFF'}")
        self.next_action.setText({Phase.IDLE:"下一步：扫码 / Next: scan", Phase.WAIT_SCAN:"下一步：扫码 / Next: scan", Phase.READY:"下一步：开始一测 / Next: test 1", Phase.WAIT_2:"下一步：开始二测 / Next: test 2", Phase.LABELING:"下一步：贴标 / Next: label", Phase.COMPLETE:"下一步：复位或查询 / Next: reset or query", Phase.FAULT:"下一步：管理员恢复并核对 / Next: admin recovery"}.get(c.phase, "下一步：按屏幕提示操作"))
    @staticmethod
    def _m(value): return "—" if value is None else f"压力 {value.pressure:g}  泄漏 {value.leakage:g}  {value.result.value or '未知'}"
    def scan(self):
        try:
            self.controller.scan(self.code.text(), self.part_no_provider())
            byte, bit = self._point("scan_ok"); self.plc.write_bit(byte, bit, True)
            self.refresh(); return True
        except Exception as exc:
            self.result.setText(f"错误 / ERROR：{exc}"); self.refresh(); return False

    @staticmethod
    def _dialog_confirmation(station, signal, requested, current):
        answer = QMessageBox.question(None, "确认危险输出 / Confirm output",
            f"工位 {station.value} / Station {station.value}\n"
            f"信号 {signal}\n请求 {'ON' if requested else 'OFF'}，当前回读 {'ON' if current else 'OFF'}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes
    def first(self):
        try: self.controller.test_first()
        except Exception as exc: self.result.setText(f"错误 / ERROR：{exc}")
        self.refresh()
    def second(self):
        try: self.controller.test_second()
        except Exception as exc: self.result.setText(f"错误 / ERROR：{exc}")
        self.refresh()
    def label(self):
        try: self.controller.label()
        except Exception as exc: self.result.setText(f"错误 / ERROR：{exc}")
        self.refresh()
    def reset(self):
        try: self.controller.reset(); self.plc.safe_stop(f"UI reset {self.station.value}")
        except Exception as exc: self.result.setText(f"错误 / ERROR：{exc}")
        self.refresh()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("双通道气密检测 / Leak Test 2 Channels · SIMULATE"); self.resize(1366, 768)
        # Prefer a CJK-capable Windows font; the English fallback remains
        # useful on stripped-down/offline images without Microsoft YaHei.
        app = QApplication.instance()
        if app is not None:
            # Qt's offscreen/plugin environments may expose no system font
            # families even when Windows has them; register a local CJK font
            # explicitly so Chinese labels do not become tofu boxes.
            family = "Segoe UI"
            for font_path in (Path(r"C:\Windows\Fonts\Noto Sans SC (TrueType).otf"), Path(r"C:\Windows\Fonts\simsun.ttc")):
                if font_path.exists():
                    font_id = QFontDatabase.addApplicationFont(str(font_path))
                    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
                    if families:
                        family = families[0]
                        break
            app.setFont(QFont(family))
        self.setStyleSheet("QMainWindow{background:#f3f6fa;}QTabBar::tab{padding:12px 24px;font-family:'Microsoft YaHei','SimSun','Segoe UI';}QPushButton{border-radius:8px;padding:7px 12px;background:#ffffff;border:1px solid #c8d4e3;}QPushButton:hover{background:#e9f2ff;}QGroupBox{font-weight:600;border:1px solid #d5deea;border-radius:10px;margin-top:8px;padding-top:10px;}QLineEdit{padding:7px;border:1px solid #c8d4e3;border-radius:7px;}")
        config_path = Path(__file__).parents[1] / "config" / "default.toml"; self.settings = Settings.from_toml(config_path) if config_path.exists() else Settings(); self.repository, self.printer, self.plc = FakeRepository(self.settings), FakePrinter(), FakePlc(); self.calibration = Calibration(); self.security = SecurityContext(AuthSession(demo=True), LicenseVerifier(simulator=True).verify(b"SIMULATE", b"SIMULATE-SIGNATURE")); self.product_settings = ProductSettingsService(self.security); self.scanner_framers = {s: ScannerFramer() for s in StationId}; self.scanner_guards = {s: ScannerGuard() for s in StationId}; self.confirmation_callback = StationCard._dialog_confirmation; journal_dir = Path(tempfile.mkdtemp(prefix="LeakTest2Channels-sim-")); self.tabs = QTabWidget()
        test = QWidget(); test_layout = QVBoxLayout(test); self.system_status = QLabel("模式：SIMULATE（仅 Fake）  |  PLC：192.168.2.1  |  ATEQ：A=COM6  B=COM7  |  数据库：FakeRepository"); self.system_status.setObjectName("systemStatusBar"); self.system_status.setStyleSheet("background:#17365d;color:white;padding:9px;border-radius:7px;font-weight:600;"); test_layout.addWidget(self.system_status)
        scanner_bar = QHBoxLayout(); self.scanner_input = QLineEdit(); self.scanner_input.setObjectName("scanner_input"); self.scanner_input.setPlaceholderText("扫码帧 / Scanner frame（选择工位后确认）"); scanner_bar.addWidget(QLabel("扫码路由 / Scanner:")); scanner_bar.addWidget(self.scanner_input, 1); self.scanner_status = QLabel("扫码器就绪 / scanner ready"); self.scanner_status.setObjectName("scannerStatus"); scanner_bar.addWidget(self.scanner_status)
        for station_id in StationId:
            button = QPushButton(f"扫码到 {station_id.value}"); button.setObjectName(f"scanner_route_{station_id.value}"); button.clicked.connect(lambda _checked=False, s=station_id: self.route_scanner_text(s)); scanner_bar.addWidget(button)
        test_layout.addLayout(scanner_bar); cards_host = QWidget(); cards_grid = QGridLayout(cards_host); self.cards = [StationCard(s, self.repository, self.printer, self.plc, CycleJournal(journal_dir / f"{s.value}.json"), self.security, self.product_settings.current_product, lambda station, signal, requested, current: self.confirmation_callback(station, signal, requested, current)) for s in StationId]; cards_grid.addWidget(self.cards[0], 0, 0); cards_grid.addWidget(self.cards[1], 0, 1); cards_grid.setColumnStretch(0, 1); cards_grid.setColumnStretch(1, 1); scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(cards_host); test_layout.addWidget(scroll, 1); self.tabs.addTab(test, "测试")
        settings = QWidget(); sl = QVBoxLayout(settings); sl.addWidget(QLabel("设置 / Settings（草稿不会影响周期，认证保存后才提交）")); self.product_edit = QLineEdit(self.product_settings.current_product()); self.product_edit.setObjectName("product_draft"); self.product_edit.setPlaceholderText("产品型号 / Part No.（草稿）"); sl.addWidget(self.product_edit); self.part_edit = self.product_edit; save = QPushButton("保存设置 / Save（需管理员）"); save.setObjectName("save_settings"); save.clicked.connect(self.save_settings); sl.addWidget(save); self.settings_status = QLabel("已提交：SIM-PART"); self.settings_status.setObjectName("settingsStatus"); sl.addWidget(self.settings_status); calibration_box = QGroupBox("校准 / Calibration（NG→OK，按状态提示取样）"); calibration_box.setObjectName("calibrationPanel"); calibration_row = QHBoxLayout(calibration_box); self.calibration_status = QLabel("等待 NG 样件"); self.calibration_status.setObjectName("calibrationStatus"); calibration_row.addWidget(self.calibration_status)
        for result in ("NG", "OK"):
            button = QPushButton(f"校准样件 {result}"); button.setObjectName(f"calibration_{result}"); button.clicked.connect(lambda _checked=False, r=result: self.on_calibration_sample(r)); calibration_row.addWidget(button)
        sl.addWidget(calibration_box); sl.addStretch(1); self.tabs.addTab(settings, "设置")
        query = QWidget(); ql = QVBoxLayout(query); row = QHBoxLayout(); self.query_edit = QLineEdit(); self.query_edit.setObjectName("query_filter"); self.query_edit.setPlaceholderText("二维码/序列号筛选"); row.addWidget(self.query_edit); refresh = QPushButton("刷新"); refresh.setObjectName("query_refresh"); refresh.clicked.connect(self.refresh_query); row.addWidget(refresh); export = QPushButton("导出 CSV"); export.setObjectName("query_export"); export.clicked.connect(self.export_csv); row.addWidget(export); ql.addLayout(row); self.table = QTableWidget(0, 5); self.table.setObjectName("queryTable"); self.table.setHorizontalHeaderLabels(["工位", "二维码", "第一次", "第二次", "结果"]); ql.addWidget(self.table); self.tabs.addTab(query, "查询")
        manual = QWidget(); ml = QVBoxLayout(manual); ml.addWidget(QLabel("手动控制 / Manual（管理员权限；每次操作显示 Fake PLC 回读）")); login_row = QHBoxLayout(); self.username = QLineEdit(); self.username.setObjectName("login_username"); self.username.setPlaceholderText("用户名"); self.password = QLineEdit(); self.password.setObjectName("login_password"); self.password.setPlaceholderText("密码"); self.password.setEchoMode(QLineEdit.EchoMode.Password); login_row.addWidget(self.username); login_row.addWidget(self.password); login = QPushButton("登录 / Login"); login.setObjectName("login_button"); login.clicked.connect(self.login); login_row.addWidget(login); ml.addLayout(login_row); self.login_status = QLabel("未登录 / operator"); self.login_status.setObjectName("loginStatus"); ml.addWidget(self.login_status); groups = QHBoxLayout()
        for station_id in StationId: groups.addWidget(self._manual_group(station_id))
        ml.addLayout(groups); recovery = QGroupBox("管理员恢复 / Recovery（先核对现场，再填写原因）"); recovery.setObjectName("recoveryPanel"); rl = QHBoxLayout(recovery); self.recovery_reason = QLineEdit(); self.recovery_reason.setObjectName("recovery_reason"); self.recovery_reason.setPlaceholderText("处理原因 / reason"); rl.addWidget(self.recovery_reason); self.recovery_status = QLabel("无恢复操作"); self.recovery_status.setObjectName("recoveryStatus"); rl.addWidget(self.recovery_status)
        for station_id in StationId:
            button = QPushButton(f"归档工位 {station_id.value}"); button.setObjectName(f"resolve_recovery_{station_id.value}"); button.clicked.connect(lambda _checked=False, s=station_id: self.resolve_recovery(s, self.recovery_reason.text() or "UI 人工确认")); rl.addWidget(button)
        ml.addWidget(recovery); ml.addStretch(1); self.tabs.addTab(manual, "手动"); self.setCentralWidget(self.tabs)

    def _manual_group(self, station):
        box = QGroupBox(f"工位 {station.value} 执行器 / Actuators {station.value}"); box.setObjectName(f"manualGroup_{station.value}"); layout = QVBoxLayout(box)
        for signal, text in MAINVI_ACTUATORS:
            button = QPushButton(f"{text} {station.value}：OFF"); button.setObjectName(f"manual_{signal}_{station.value}"); button.clicked.connect(lambda _checked=False, s=signal, b=button: self.manual_output(b, station, s)); layout.addWidget(button)
        return box
    def save_settings(self):
        try: self.settings_status.setText(f"已提交：{self.product_settings.save(self.product_edit.text()).product_no}")
        except Exception as exc: self.settings_status.setText(f"设置拒绝：{exc}")
    def login(self): self.login_status.setText("已认证 / admin" if self.security.login(self.username.text(), self.password.text()) else "登录失败 / denied")
    def route_scanner_code(self, station, code):
        if not self.scanner_guards[station].accept(code): raise ValueError("扫码为空或重复")
        for card in self.cards:
            if card.controller.station is station:
                previous_code = card.code.text()
                card.code.setText(code)
                accepted = card.scan()
                if not accepted or card.controller.phase is not Phase.READY or not card.controller.record or card.controller.record.code_2d != code.strip():
                    card.code.setText(previous_code)
                    raise RuntimeError(f"工位 {station.value} 未进入 READY，扫码未接受")
                self.scanner_status.setText(f"已路由到工位 {station.value} / scan OK"); return
        raise ValueError(f"未知工位: {station}")
    def route_scanner_text(self, station):
        try:
            frames = self.scanner_framers[station].feed((self.scanner_input.text() + "\r\n").encode("utf-8"));
            if len(frames) != 1: raise ValueError("扫码帧不完整")
            self.route_scanner_code(station, frames[0])
        except Exception as exc: self.scanner_status.setText(f"扫码错误：{exc}")
    def resolve_recovery(self, station, reason="UI 人工确认"):
        try:
            self.security.require("recovery_resolve")
            for card in self.cards:
                if card.controller.station is station: card.controller.resolve_recovery(reason); card.refresh(); self.recovery_status.setText(f"工位 {station.value} 已审计归档"); return
            raise ValueError(f"未知工位: {station}")
        except Exception as exc: self.recovery_status.setText(f"恢复拒绝：{exc}")
    def calibration_sample(self, result): return f"{self.calibration.sample(result).value} / 倒计时={self.calibration.countdown} / 样件需求={self.calibration.sample_demand}"
    def on_calibration_sample(self, result):
        try: self.calibration_status.setText(self.calibration_sample(result))
        except Exception as exc: self.calibration_status.setText(f"校准错误：{exc}")
    def manual_output(self, button, station=StationId.A, signal="clamp"):
        try:
            self.security.require("manual_output"); byte, bit = POINTS[signal][station]; current = self.plc.read_bit(byte, bit); requested = not current
            if not self.confirmation_callback(station, signal, requested, current):
                button.setText(f"{dict(MAINVI_ACTUATORS)[signal]} {station.value}：已取消"); return
            self.plc.write_bit(byte, bit, requested); actual = self.plc.read_bit(byte, bit); button.setText(f"{dict(MAINVI_ACTUATORS)[signal]} {station.value}：{'ON' if actual else 'OFF'}"); [card.refresh() for card in self.cards]
        except Exception as exc: button.setText(f"拒绝：{exc}")
    def refresh_query(self):
        rows = self.repository.query(self.query_edit.text()); self.table.setRowCount(len(rows))
        for index, item in enumerate(rows):
            values = (item.station.value, item.code_2d, item.first.result.value if item.first else "—", item.second.result.value if item.second else "—", item.second.result.value if item.second else "待测")
            for col, value in enumerate(values): self.table.setItem(index, col, QTableWidgetItem(value))
    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", "query.csv", "CSV (*.csv)")
        if not path: return
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle); writer.writerow([self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())])
            for row in range(self.table.rowCount()): writer.writerow([self.table.item(row, col).text() if self.table.item(row, col) else "" for col in range(self.table.columnCount())])
