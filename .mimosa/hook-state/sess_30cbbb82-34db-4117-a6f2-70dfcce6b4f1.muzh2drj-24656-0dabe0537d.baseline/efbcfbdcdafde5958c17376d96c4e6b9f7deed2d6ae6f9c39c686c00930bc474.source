import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.scanner_tcp import TcpScanner


class _FakeConnection:
    def __init__(self):
        self.sent = []
        self.closed = False

    def sendall(self, payload):
        self.sent.append(bytes(payload))

    def shutdown(self, _how):
        pass

    def close(self):
        self.closed = True


def test_scan_enable_disable_commands_use_line_terminator():
    scanner = TcpScanner("127.0.0.1", 9004, terminator=b"\r\n")
    connection = _FakeConnection()
    scanner._conn = connection
    scanner._connected.set()

    assert scanner.set_scan_enabled(True) is True
    assert scanner.set_scan_enabled(False) is True
    assert connection.sent == [b"LON\r\n", b"LOFF\r\n"]


def test_scan_command_is_queued_until_reconnect():
    scanner = TcpScanner("127.0.0.1", 9004, terminator=b"\r\n")

    assert scanner.set_scan_enabled(False) is False
    assert scanner._desired_command == b"LOFF\r\n"

    connection = _FakeConnection()
    scanner._conn = connection
    scanner._connected.set()
    assert scanner.set_scan_enabled(True) is True
    assert scanner._desired_command == b"LON\r\n"
    assert connection.sent == [b"LON\r\n"]


def test_raw_command_does_not_duplicate_existing_terminator():
    scanner = TcpScanner("127.0.0.1", 9004, terminator=b"\r\n")
    connection = _FakeConnection()
    scanner._conn = connection
    scanner._connected.set()

    assert scanner.send_command(b"LOFF\r\n") is True
    assert connection.sent == [b"LOFF\r\n"]
