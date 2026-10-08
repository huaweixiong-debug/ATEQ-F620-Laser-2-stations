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
