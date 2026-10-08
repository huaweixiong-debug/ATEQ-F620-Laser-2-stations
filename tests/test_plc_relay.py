"""PLC 中转（B 侧服务 + A 侧远程客户端）测试。

现场形态：一台 FX PLC 接在 B 电脑 COM3；A 电脑的工位信号（含激光启动位）
必须经 B 中转写 PLC。协议：JSON 行，先 hello(token)，再 read_bit/write_bit/
read_word/write_word/health/outputs_energized。同token单客户端独占。
"""
import socket
import time

import pytest

from app.plc import FakeFxPlc
from app.plc_relay import PlcRelayServer, RemoteFxPlc


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_relay_roundtrip():
    plc = FakeFxPlc()
    plc.write_bit(1, 0, True)
    plc.write_word(900, 4321)
    port = _free_port()
    server = PlcRelayServer(plc, port=port, token="secret")
    server.start()
    try:
        client = RemoteFxPlc("127.0.0.1", port, token="secret")
        client.connect()
        client.enable_writes(True)            # LIVE 装配通过预检后才放开
        assert client.read_bit(1, 0) is True
        assert client.read_word(900) == 4321
        client.write_bit(0, 0, True)          # A 侧激光启动位脉冲路径
        assert plc.read_bit(0, 0) is True
        client.write_word(901, 77)
        assert plc.read_word(901) == 77
        assert client.health() is True
        assert client.outputs_energized() is True
        client.close()
    finally:
        server.stop()


def test_relay_requires_token():
    plc = FakeFxPlc()
    port = _free_port()
    server = PlcRelayServer(plc, port=port, token="secret")
    server.start()
    try:
        with pytest.raises(PermissionError):
            client = RemoteFxPlc("127.0.0.1", port, token="wrong")
            client.connect()
        # 错误 token 之后合法客户端仍可连上（服务器未死）
        client = RemoteFxPlc("127.0.0.1", port, token="secret")
        client.connect()
        assert client.health() is True
        client.close()
    finally:
        server.stop()


def test_relay_client_write_gated_by_default():
    plc = FakeFxPlc()
    port = _free_port()
    server = PlcRelayServer(plc, port=port, token="t")
    server.start()
    try:
        client = RemoteFxPlc("127.0.0.1", port, token="t")
        client.connect()
        with pytest.raises(PermissionError):
            client.write_bit(0, 0, True)      # 未 enable_writes 前禁止写
        client.enable_writes(True)
        client.write_bit(0, 0, True)
        assert plc.read_bit(0, 0) is True
        client.close()
    finally:
        server.stop()


def test_relay_serializes_single_client():
    plc = FakeFxPlc()
    port = _free_port()
    server = PlcRelayServer(plc, port=port, token="t")
    server.start()
    try:
        first = RemoteFxPlc("127.0.0.1", port, token="t")
        first.connect()
        with pytest.raises((RuntimeError, PermissionError)):
            second = RemoteFxPlc("127.0.0.1", port, token="t")
            second.connect()                  # 独占：第二个客户端被拒
        first.close()
        # handler 线程退出并释放独占锁是异步的：轮询等待重连成功。
        again = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                again = RemoteFxPlc("127.0.0.1", port, token="t")
                again.connect()
                break
            except (PermissionError, RuntimeError):
                again = None
                time.sleep(0.1)
        assert again is not None, "独占锁释放后应可重连"
        again.close()
    finally:
        server.stop()


def test_remote_safe_stop_and_errors():
    plc = FakeFxPlc()
    port = _free_port()
    server = PlcRelayServer(plc, port=port, token="t")
    server.start()
    try:
        client = RemoteFxPlc("127.0.0.1", port, token="t")
        client.connect()
        client.safe_stop("测试")
        # A 侧故障安全停止不得停掉 B 的本地 PLC（不远程转发 safe_stop）
        assert plc.last_safe_stop == ""
        assert client.connected is False
        client.close()
    finally:
        server.stop()


def test_relay_write_independent_of_server_gate():
    """A/B 独立：B 侧 safe_stop 关闭本机写门禁后，A 经 relay 的写仍可用。

    2026-10-08 现场：B 保压异常安全停止 → B 本机门禁关闭 → A 打码脉冲被
    relay 拒绝 → A mark_error。relay 转发写必须走 force 路径，写权只由
    A 侧 capability 门禁把守。
    """
    plc = FakeFxPlc()
    plc.enable_writes(True)
    plc.safe_stop("B 保压异常安全停止")
    with pytest.raises(PermissionError):
        plc.write_bit(0, 0, True)            # B 本机写确实被门禁拒绝
    port = _free_port()
    server = PlcRelayServer(plc, port=port, token="t")
    server.start()
    try:
        client = RemoteFxPlc("127.0.0.1", port, token="t")
        client.connect()
        client.enable_writes(True)
        client.write_bit(1, 0, True)         # A 的正常写（如打码脉冲）不受影响
        assert plc.read_bit(1, 0) is True
        client.disconnect()
        again = RemoteFxPlc("127.0.0.1", port, token="t")
        again.connect()                      # 未 enable_writes
        again.force_write_bit(1, 1, True)    # A 侧故障终止脉冲必须仍能发出
        assert plc.read_bit(1, 1) is True
        again.disconnect()
    finally:
        server.stop()


def test_remote_client_rebuilds_stale_socket():
    """relay 链路抖动/B 侧重启后，connect() 必须重建 socket 而非翻标志复用。

    旧实现：旧 socket 未关闭时 connect() 只把 connected 置 True，读写在
    死连接上永远失败（2026-10-08 A 机 21:12 前长时间瘫痪根因）。
    """
    plc = FakeFxPlc()
    port = _free_port()
    server = PlcRelayServer(plc, port=port, token="t")
    server.start()
    try:
        client = RemoteFxPlc("127.0.0.1", port, token="t")
        client.connect()
        assert client.read_bit(0, 0) is False
        # 模拟对端关闭（服务端 5 秒空闲超时 / B 侧重启）：底层 socket 死掉
        client._sock.close()
        with pytest.raises(RuntimeError):
            client.read_bit(0, 0)            # 失败后 connected=False
        client.connect()                     # 修复点：重建连接
        assert client.read_bit(0, 0) is False
        client.disconnect()
    finally:
        server.stop()
