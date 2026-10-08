"""B 电脑专用：等待 relay 就绪后，经 SSH 触发 A 电脑桌面上的现场 UI。

- A 的 UI 通过交互式计划任务 ``ATEQ_A_UI`` 启动（Task Scheduler /IT），
  只有这样才能显示在 A 的桌面会话里，而不是 SSH 的后台会话。
- SSH 免密：B 的私钥 ``C:\\Users\\dell\\.ssh\\id_ed25519_ateq``，
  公钥需加入 A 的 ``C:\\ProgramData\\ssh\\administrators_authorized_keys``
  （一次性配置见 docs/部署手册.md）。
"""
from __future__ import annotations

import socket
import subprocess
import time

A_HOST = "192.168.7.10"
A_USER = "dell"
A_TASK = "ATEQ_A_UI"
A_KEY = r"C:\Users\dell\.ssh\id_ed25519_ateq"
RELAY_HOST = "127.0.0.1"
RELAY_PORT = 9101
RELAY_TIMEOUT_S = 240.0


def relay_ready(timeout: float = RELAY_TIMEOUT_S) -> bool:
    """等待本机 relay 端口进入监听（B 的 live UI 初始化时启动）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((RELAY_HOST, RELAY_PORT), timeout=1.0):
                return True
        except OSError:
            time.sleep(2.0)
    return False


def trigger_a_ui() -> int:
    cmd = [
        "ssh", "-i", A_KEY,
        "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
        f"{A_USER}@{A_HOST}", f"schtasks /run /tn {A_TASK}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    print(f"ssh rc={result.returncode}")
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    return 0 if result.returncode == 0 else 2


def main() -> int:
    print("WAIT relay ...")
    if not relay_ready():
        print("RELAY_NOT_READY：放弃触发 A 机 UI（可稍后在 A 机桌面双击 run_live_ui.bat）")
        return 1
    # relay 刚监听时 B 的 UI 仍在初始化，给 A 的预检留出握手余量
    time.sleep(3.0)
    print("TRIGGER A UI ...")
    return trigger_a_ui()


if __name__ == "__main__":
    raise SystemExit(main())
