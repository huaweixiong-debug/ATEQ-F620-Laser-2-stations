"""UI entry point backed only by service-layer permissions."""
from __future__ import annotations

from pathlib import Path

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:  # pragma: no cover
    QApplication = None


def _install_crash_log() -> None:
    """Write native faults, slot exceptions and thread errors to a file.

    控制台窗口随 UI 退出一起消失，崩溃现场必须落盘才能远程诊断：
    faulthandler 覆盖段错误/硬崩溃，excepthook 覆盖 Python 异常。
    """
    import faulthandler
    import sys
    import threading
    import traceback
    from datetime import datetime

    path = Path(r"D:\ATEQ\ui_crash.log")
    try:
        stream = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return

    def _hook(kind, value, tb):
        try:
            stream.write(f"\n=== {datetime.now().isoformat()} {kind.__name__}: {value}\n")
            traceback.print_exception(kind, value, tb, file=stream)
        except Exception:
            pass

    try:
        faulthandler.enable(stream)
    except Exception:
        pass
    sys.excepthook = _hook
    threading.excepthook = lambda args: _hook(args.exc_type, args.exc_value, args.exc_traceback)


def launch_ui(*, live: bool = False, live_config: Path | None = None,
              preflight_passed: bool = False) -> int:
    if QApplication is None:
        raise RuntimeError("PySide6 未安装；请运行 pip install PySide6")
    _install_crash_log()
    app = QApplication.instance() or QApplication([])
    try:
        from .ui_replica import MainWindow
        window = MainWindow(live=live, config_path=live_config,
                            preflight_passed=preflight_passed)
    except Exception as exc:
        message = f"实时硬件 UI 启动阻断：{type(exc).__name__}: {exc}"
        print(message)
        QMessageBox.critical(None, "UI 无法启动", message)
        return 2
    window.showMaximized()
    return app.exec()


# Screenshot-structured Main.vi replica.  It retains the same service gates
# but presents the four pages and field/table hierarchy from the references.
try:  # pragma: no cover - exercised by Qt tests
    from .ui_replica import MainWindow, StationCard
except ImportError:
    pass
