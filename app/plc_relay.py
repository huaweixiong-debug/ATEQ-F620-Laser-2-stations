"""PLC 中转：B 侧 TCP 服务 + A 侧远程客户端。

现场形态（240429）：一台 FX PLC 接在 B 电脑 COM3。B 电脑跑本工位实例时
同时开启中转服务；A 电脑实例用 RemoteFxPlc 经局域网读写 PLC（工位 A 的
激光启动位等写操作由 B 代为落到 PLC）。

协议：每行一个 JSON 对象（UTF-8，\\n 结尾）。
  客户端 -> 服务端：
    {"op":"hello", "token":"..."}
    {"op":"read_bit", "byte":0, "bit":1}
    {"op":"write_bit", "byte":0, "bit":1, "value":true}
    {"op":"read_word", "device":900}
    {"op":"write_word", "device":900, "value":123}
    {"op":"health"} / {"op":"outputs_energized"} / {"op":"safe_stop", "reason":"..."}
  服务端 -> 客户端：
    {"ok":true, ...} / {"ok":false, "error":"..."}

安全与互斥：
- token 不匹配立即断开（防误连别的工位）；
- 同一时刻只允许一个客户端：FX 编程口是独占串口，B 本机实例与 A 中转
  共用同一把锁，所有写都受 B 侧 enable_writes 门禁约束。
"""
from __future__ import annotations
from threading import Lock, Thread
import json
import socket

_MAX_LINE = 4096


class PlcRelayServer:
    """把本地 PLC 适配器按 JSON 行协议暴露给另一工位电脑。"""

    def __init__(self, plc, *, port: int = 9101, token: str = "",
                 host: str = "0.0.0.0") -> None:
        if not token:
            raise ValueError("PLC 中转必须配置非空 token")
        self.plc = plc
        self.host, self.port, self.token = host, int(port), str(token)
        self._server = None
        self._thread = None
        self._client_lock = Lock()      # 同一时刻至多一个客户端
        self._run = False
        self.last_error = ""

    def start(self) -> None:
        if self._thread is not None:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(1)
        server.settimeout(0.5)
        self._server = server
        self._run = True
        self._thread = Thread(target=self._serve, name="plc-relay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._run = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        self._server = None

    def _serve(self) -> None:
        while self._run:
            try:
                conn, addr = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            conn.settimeout(5)
            if not self._client_lock.acquire(blocking=False):
                # FX 编程口独占：已有客户端在服务时立即拒绝新连接。
                try:
                    self._send(conn, {"ok": False, "error": "中转已被其它工位占用"})
                except Exception:
                    pass
                conn.close()
                continue
            worker = Thread(target=self._serve_client, args=(conn,), daemon=True)
            worker.start()

    def _serve_client(self, conn: socket.socket) -> None:
        try:
            self._handle(conn)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            conn.close()
            self._client_lock.release()

    def _handle(self, conn: socket.socket) -> None:
        if not self._handshake(conn):
            return
        buffer = b""
        while self._run:
            chunk = conn.recv(_MAX_LINE)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                self._send(conn, self._dispatch(line))

    def _handshake(self, conn: socket.socket) -> bool:
        try:
            line = conn.recv(_MAX_LINE)
            hello = json.loads(line.decode("utf-8"))
        except Exception:
            self._send(conn, {"ok": False, "error": "握手失败"})
            return False
        if hello.get("op") != "hello" or hello.get("token") != self.token:
            self._send(conn, {"ok": False, "error": "token 不匹配"})
            return False
        self._send(conn, {"ok": True, "version": 1})
        return True

    def _dispatch(self, line: bytes) -> dict:
        try:
            request = json.loads(line.decode("utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"JSON 解析失败: {exc}"}
        op = request.get("op")
        try:
            if op == "read_bit":
                return {"ok": True, "value": bool(
                    self.plc.read_bit(int(request["byte"]), int(request["bit"])))}
            if op == "write_bit":
                self.plc.write_bit(int(request["byte"]), int(request["bit"]),
                                   bool(request["value"]))
                return {"ok": True}
            if op == "read_word":
                return {"ok": True, "value": int(self.plc.read_word(int(request["device"])))}
            if op == "write_word":
                self.plc.write_word(int(request["device"]), int(request["value"]))
                return {"ok": True}
            if op == "health":
                return {"ok": True, "value": bool(self.plc.health())}
            if op == "outputs_energized":
                return {"ok": True, "value": bool(self.plc.outputs_energized())}
            if op == "safe_stop":
                self.plc.safe_stop(str(request.get("reason", "remote safe stop")))
                return {"ok": True}
            return {"ok": False, "error": f"未知操作: {op!r}"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _send(conn: socket.socket, payload: dict) -> None:
        conn.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")


class RemoteFxPlc:
    """A 侧远程 PLC 客户端：与 FxSerialPlc 同一适配接口，写走中转。

    写门禁与本地适配器一致：默认禁止写，enable_writes(True) 后才放行
    （LIVE 预检通过后由装配层放开）。
    """

    def __init__(self, host: str, port: int, *, token: str = "",
                 timeout_s: float = 3.0) -> None:
        if not token:
            raise ValueError("远程 PLC 客户端必须配置非空 token")
        self.host, self.port, self.token = host, int(port), str(token)
        self.timeout_s = float(timeout_s)
        self.connected = False
        self._writes_enabled = False
        self.last_error = ""
        self._sock: socket.socket | None = None
        self._lock = Lock()
        self._next_id = 0

    def enable_writes(self, approved: bool) -> None:
        self._writes_enabled = bool(approved)

    def connect(self) -> None:
        with self._lock:
            if self._sock is not None:
                self.connected = True
                return
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout_s)
            try:
                sock.connect((self.host, self.port))
                self._send(sock, {"op": "hello", "token": self.token})
                reply = self._recv(sock)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                try:
                    sock.close()
                except OSError:
                    pass
                raise PermissionError(f"PLC 中转握手失败: {self.last_error}") from exc
            if not reply.get("ok"):
                sock.close()
                raise PermissionError(f"PLC 中转拒绝: {reply.get('error', '未知')}")
            self._sock = sock
            self.connected = True

    def disconnect(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
            self.connected = False
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    close = disconnect

    def _require_connection(self) -> None:
        if self._sock is None or not self.connected:
            raise RuntimeError("远程 PLC 未连接，先调用 connect()")

    def _call(self, payload: dict) -> dict:
        with self._lock:
            self._require_connection()
            try:
                self._send(self._sock, payload)
                return self._recv(self._sock)
            except OSError as exc:
                self.connected = False
                self.last_error = f"{type(exc).__name__}: {exc}"
                raise RuntimeError(f"PLC 中转通讯失败: {self.last_error}") from exc

    def read_bit(self, byte: int, bit: int) -> bool:
        reply = self._call({"op": "read_bit", "byte": int(byte), "bit": int(bit)})
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error", "read_bit 失败")))
        return bool(reply["value"])

    def write_bit(self, byte: int, bit: int, value: bool) -> None:
        if not self._writes_enabled:
            raise PermissionError("PLC 写入被 capability policy 拒绝")
        reply = self._call({"op": "write_bit", "byte": int(byte),
                            "bit": int(bit), "value": bool(value)})
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error", "write_bit 失败")))

    def read_word(self, device: int) -> int:
        reply = self._call({"op": "read_word", "device": int(device)})
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error", "read_word 失败")))
        return int(reply["value"])

    def write_word(self, device: int, value: int) -> None:
        if not self._writes_enabled:
            raise PermissionError("PLC 写入被 capability policy 拒绝")
        reply = self._call({"op": "write_word", "device": int(device),
                            "value": int(value)})
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error", "write_word 失败")))

    def health(self) -> bool:
        try:
            reply = self._call({"op": "health"})
            return bool(reply.get("ok") and reply.get("value"))
        except Exception:
            self.connected = False
            return False

    def safe_stop(self, reason: str) -> None:
        """仅停本机（A 侧）：关闭写门禁并断开中转客户端。

        ⚠️ 不把 safe_stop 转发给 B 的本地 PLC 适配器——A 工位的故障不得
        断开 B 的 PLC 连接/写门禁（否则 B 打码、A 自身经 relay 的读写、
        B 本机生产都会被连带破坏）。机器侧安全由 PLC 程序负责，本机写
        门禁已足够。
        """
        self._writes_enabled = False
        self.last_error = f"safe_stop: {reason}"
        self.disconnect()

    def outputs_energized(self) -> bool:
        try:
            reply = self._call({"op": "outputs_energized"})
            return bool(reply.get("ok") and reply.get("value"))
        except Exception:
            return False

    @staticmethod
    def _send(sock: socket.socket, payload: dict) -> None:
        sock.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")

    @staticmethod
    def _recv(sock: socket.socket) -> dict:
        buffer = b""
        while b"\n" not in buffer:
            if len(buffer) > _MAX_LINE:
                raise RuntimeError("PLC 中转应答超长")
            chunk = sock.recv(_MAX_LINE)
            if not chunk:
                raise RuntimeError("PLC 中转连接已断开")
            buffer += chunk
        return json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))
