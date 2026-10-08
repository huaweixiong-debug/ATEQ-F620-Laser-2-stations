"""Windows 11-style operator UI backed only by service-layer permissions."""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from .models import Phase, PrintState, StationId
from .ui_theme import stylesheet

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
        QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QTabWidget,
        QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QMessageBox)
    from .ateq import FakeAteq
    from .config import Settings
    from .journal import CycleJournal
    from .license import LicenseVerifier
    from .permissions import AuthSession, SecurityContext
    from .plc import FakePlc
    from .printer import FakePrinter
    from .repository import FakeRepository
    from .station import StationController
    from .calibration import Calibration
    from .scanner import ScannerFramer, ScannerGuard
    from .settings_service import ProductSettingsService
except ImportError:  # pragma: no cover
    QApplication = None


def launch_ui() -> int:
    if QApplication is None:
        raise RuntimeError("PySide6 未安装；请运行 pip install PySide6")
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.showMaximized()
    return app.exec()


if QApplication is not None:
    class StationCard(QFrame):
        def __init__(self, station: StationId, repository: FakeRepository,
                     printer: FakePrinter, plc: FakePlc, journal: CycleJournal,
                     security: SecurityContext, part_no_provider=None) -> None:
            super().__init__()
            self.controller = StationController(station, repository, printer, FakeAteq(), journal,
                                                 safe_stop=plc,
                                                 license_status=security.license_status,
                                                 security=security)
            self.part_no_provider = part_no_provider or (lambda: "")
            self.setObjectName("stationCard")
            layout = QVBoxLayout(self)
            title = QLabel(f"工位 {station.value} / Station {station.value}")
            title.setObjectName("pageTitle")
            self.status = QLabel()
            self.result = QLabel("等待扫码 / Scan required")
            self.result.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.result.setMinimumHeight(70)
            self.result.setObjectName("resultBanner")
            self.measurements = QLabel("二维码 / QR：—\n第一次 / Test 1：—\n第二次 / Test 2：—")
            self.code = QLineEdit(); self.code.setPlaceholderText("扫码或输入二维码 / Scan QR")
            layout.addWidget(title); layout.addWidget(self.status); layout.addWidget(self.result); layout.addWidget(self.measurements); layout.addWidget(self.code)
            actions = QHBoxLayout()
            for text, callback in (("扫码", self.scan), ("一测", self.first), ("二测", self.second), ("贴标", self.label), ("复位", self.reset)):
                button = QPushButton(text); button.setMinimumHeight(42); button.clicked.connect(callback); actions.addWidget(button)
            layout.addLayout(actions)
            self.refresh()

        def refresh(self) -> None:
            c = self.controller
            self.status.setText(f"● {c.phase.value} | {c.print_state.value}" + (f" | {c.error}" if c.error else ""))
            if c.record:
                first = c.record.first
                second = c.record.second
                self.measurements.setText(
                    f"二维码 / QR：{c.record.code_2d}\n"
                    f"第一次 / Test 1：{self._m(first)}\n第二次 / Test 2：{self._m(second)}")
            if c.phase is Phase.FAULT:
                self.result.setText("故障 / FAULT")

        @staticmethod
        def _m(value) -> str:
            return "—" if value is None else f"压力 {value.pressure:g}  泄漏 {value.leakage:g}  {value.result.value or '未知'}"

        def scan(self) -> None:
            try: self.controller.scan(self.code.text(), self.part_no_provider()); self.result.setText("就绪 / READY")
            except Exception as exc: self.result.setText(f"错误 / ERROR：{exc}")
            self.refresh()
        def first(self) -> None:
            try: self.controller.test_first(); self.result.setText(self.controller.record.first.result.value or "未知")
            except Exception as exc: self.result.setText(f"错误 / ERROR：{exc}")
            self.refresh()
        def second(self) -> None:
            try: self.controller.test_second(); self.result.setText(self.controller.record.second.result.value or "未知")
            except Exception as exc: self.result.setText(f"错误 / ERROR：{exc}")
            self.refresh()
        def label(self) -> None:
            try: self.controller.label()
            except Exception as exc: self.result.setText(f"错误 / ERROR：{exc}")
            self.refresh()
        def reset(self) -> None:
            try:
                self.controller.reset(); self.result.setText("等待扫码 / Scan required")
                self.measurements.setText("二维码 / QR：—\n第一次 / Test 1：—\n第二次 / Test 2：—")
            except Exception as exc: self.result.setText(f"错误 / ERROR：{exc}")
            self.refresh()


    class MainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("双通道气密检测 / Leak Test 2 Channels · SIMULATE")
            self.resize(1366, 768)
            self.setStyleSheet(stylesheet())
            config_path = Path(__file__).parents[1] / "config" / "default.toml"
            self.settings = Settings.from_toml(config_path) if config_path.exists() else Settings()
            self.repository, self.printer, self.plc = FakeRepository(self.settings), FakePrinter(), FakePlc()
            self.calibration = Calibration()
            self.security = SecurityContext(AuthSession(demo=True), LicenseVerifier(simulator=True).verify(b"SIMULATE", b"SIMULATE-SIGNATURE"))
            self.product_settings = ProductSettingsService(self.security)
            self.scanner_framers = {s: ScannerFramer() for s in StationId}
            self.scanner_guards = {s: ScannerGuard() for s in StationId}
            journal_dir = Path(tempfile.gettempdir()) / "LeakTest2Channels-sim"
            self.tabs = QTabWidget()
            self.cards = [StationCard(s, self.repository, self.printer, self.plc, CycleJournal(journal_dir / f"{s.value}.json"), self.security, self.product_settings.current_product) for s in StationId]
            test = QWidget(); grid = QGridLayout(test); grid.addWidget(self.cards[0], 0, 0); grid.addWidget(self.cards[1], 0, 1)
            scanner_bar = QHBoxLayout(); self.scanner_input = QLineEdit(); self.scanner_input.setPlaceholderText("扫码帧 / Scanner frame"); scanner_bar.addWidget(self.scanner_input); self.scanner_status = QLabel("扫码器就绪 / scanner ready"); scanner_bar.addWidget(self.scanner_status)
            for station_id in StationId:
                button = QPushButton(f"扫码到 {station_id.value}"); button.clicked.connect(lambda _checked=False, s=station_id: self.route_scanner_text(s)); scanner_bar.addWidget(button)
            grid.addLayout(scanner_bar, 1, 0, 1, 2)
            self.tabs.addTab(test, "测试")
            settings = QWidget(); sl = QVBoxLayout(settings); sl.addWidget(QLabel("设置 / Settings（提交后才用于新周期）")); self.product_edit = QLineEdit(self.product_settings.current_product()); self.product_edit.setPlaceholderText("产品型号 / Part No."); sl.addWidget(self.product_edit); self.part_edit = self.product_edit; save = QPushButton("保存设置 / Save"); save.clicked.connect(self.save_settings); sl.addWidget(save); self.settings_status = QLabel("已提交：SIM-PART"); sl.addWidget(self.settings_status)
            calibration_row = QHBoxLayout(); calibration_row.addWidget(QLabel("校准 / Calibration")); self.calibration_status = QLabel("等待 NG 样件"); calibration_row.addWidget(self.calibration_status)
            for result in ("NG", "OK"):
                button = QPushButton(f"校准样件 {result}"); button.clicked.connect(lambda _checked=False, r=result: self.on_calibration_sample(r)); calibration_row.addWidget(button)
            sl.addLayout(calibration_row); self.tabs.addTab(settings, "设置")
            query = QWidget(); ql = QVBoxLayout(query); row = QHBoxLayout(); self.query_edit = QLineEdit(); self.query_edit.setPlaceholderText("二维码/序列号筛选"); row.addWidget(self.query_edit); refresh = QPushButton("刷新"); refresh.clicked.connect(self.refresh_query); row.addWidget(refresh); export = QPushButton("导出 CSV"); export.clicked.connect(self.export_csv); row.addWidget(export); ql.addLayout(row); self.table = QTableWidget(0, 5); self.table.setHorizontalHeaderLabels(["工位", "二维码", "第一次", "第二次", "结果"]); ql.addWidget(self.table); self.tabs.addTab(query, "查询")
            manual = QWidget(); ml = QVBoxLayout(manual); ml.addWidget(QLabel("手动控制 / Manual（管理员权限，显示 Fake 回读）")); login_row = QHBoxLayout(); self.username = QLineEdit(); self.username.setPlaceholderText("用户名"); self.password = QLineEdit(); self.password.setPlaceholderText("密码"); self.password.setEchoMode(QLineEdit.EchoMode.Password); login_row.addWidget(self.username); login_row.addWidget(self.password); login = QPushButton("登录 / Login"); login.clicked.connect(self.login); login_row.addWidget(login); ml.addLayout(login_row); self.login_status = QLabel("未登录 / operator"); ml.addWidget(self.login_status); action = QPushButton("夹紧 A / Clamp A"); action.clicked.connect(lambda: self.manual_output(action)); ml.addWidget(action)
            ml.addWidget(QLabel("恢复处理 / Recovery (admin)")); self.recovery_reason = QLineEdit(); self.recovery_reason.setPlaceholderText("处理原因 / reason"); ml.addWidget(self.recovery_reason); self.recovery_status = QLabel("无恢复操作"); ml.addWidget(self.recovery_status)
            for station_id in StationId:
                button = QPushButton(f"归档工位 {station_id.value}"); button.clicked.connect(lambda _checked=False, s=station_id: self.resolve_recovery(s, self.recovery_reason.text() or "UI 人工确认")); ml.addWidget(button)
            self.tabs.addTab(manual, "手动")
            self.setCentralWidget(self.tabs)

        def save_settings(self) -> None:
            try:
                committed = self.product_settings.save(self.product_edit.text())
                self.settings = Settings(mode=self.settings.mode, plc_ip=self.settings.plc_ip, plc_poll_ms=self.settings.plc_poll_ms, ateq_ports=self.settings.ateq_ports, database=self.settings.database)
                self.settings_status.setText(f"已提交：{committed.product_no}")
            except Exception as exc:
                self.settings_status.setText(f"设置拒绝：{exc}")

        def login(self) -> None:
            if self.security.login(self.username.text(), self.password.text()): self.login_status.setText("已认证 / admin")
            else: self.login_status.setText("登录失败 / denied")

        def route_scanner_code(self, station: StationId, code: str) -> None:
            """Route a validated scanner frame to exactly one station."""
            if not self.scanner_guards[station].accept(code):
                raise ValueError("扫码为空或重复")
            for card in self.cards:
                if card.controller.station is station:
                    card.code.setText(code); card.scan(); return
            raise ValueError(f"未知工位: {station}")

        def route_scanner_text(self, station: StationId) -> None:
            try:
                raw = (self.scanner_input.text() + "\r\n").encode("utf-8")
                frames = self.scanner_framers[station].feed(raw)
                if len(frames) != 1:
                    raise ValueError("扫码帧不完整")
                self.route_scanner_code(station, frames[0])
                self.scanner_status.setText(f"已路由到工位 {station.value}")
            except Exception as exc:
                self.scanner_status.setText(f"扫码错误：{exc}")

        def resolve_recovery(self, station: StationId, reason: str = "UI 人工确认") -> None:
            try:
                self.security.require("recovery_resolve")
                for card in self.cards:
                    if card.controller.station is station:
                        card.controller.resolve_recovery(reason); card.refresh(); self.recovery_status.setText(f"工位 {station.value} 已审计归档"); return
                raise ValueError(f"未知工位: {station}")
            except Exception as exc:
                self.recovery_status.setText(f"恢复拒绝：{exc}")

        def calibration_sample(self, result: str) -> str:
            phase = self.calibration.sample(result)
            return f"{phase.value} / countdown={self.calibration.countdown} / demand={self.calibration.sample_demand}"

        def on_calibration_sample(self, result: str) -> None:
            try: self.calibration_status.setText(self.calibration_sample(result))
            except Exception as exc: self.calibration_status.setText(f"校准错误：{exc}")

        def manual_output(self, button) -> None:
            try:
                self.security.require("manual_output")
                self.plc.write_bit(4, 4, True)
                button.setText("夹紧 A：" + ("已确认" if self.plc.read_bit(4, 4) else "未确认"))
            except Exception as exc: button.setText(f"拒绝：{exc}")

        def refresh_query(self) -> None:
            rows = self.repository.query(self.query_edit.text()); self.table.setRowCount(len(rows))
            for index, item in enumerate(rows):
                values = (item.station.value, item.code_2d, item.first.result.value if item.first else "—", item.second.result.value if item.second else "—", item.second.result.value if item.second else "待测")
                for col, value in enumerate(values): self.table.setItem(index, col, QTableWidgetItem(value))

        def export_csv(self) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", "query.csv", "CSV (*.csv)")
            if not path: return
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle); writer.writerow([self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())])
                for row in range(self.table.rowCount()): writer.writerow([self.table.item(row, col).text() if self.table.item(row, col) else "" for col in range(self.table.columnCount())])

# The dashboard is kept in a focused module so the historical compatibility
# implementation above remains importable for old callers.  New UI launches
# and tests use the Main.vi-equivalent dashboard.
try:  # pragma: no cover - exercised by Qt tests
    from .ui_dashboard import MainWindow, StationCard
except ImportError:
    pass

# Screenshot-structured Main.vi replica.  It retains the same service gates
# but presents the four pages and field/table hierarchy from the references.
try:  # pragma: no cover - exercised by Qt tests
    from .ui_replica import MainWindow, StationCard
except ImportError:
    pass
