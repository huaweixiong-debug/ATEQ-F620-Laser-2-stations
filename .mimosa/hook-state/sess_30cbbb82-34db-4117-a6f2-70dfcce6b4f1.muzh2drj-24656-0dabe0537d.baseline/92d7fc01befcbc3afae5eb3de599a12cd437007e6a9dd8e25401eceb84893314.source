"""Shared-scanner, per-station model and PLC-handshake production coordinator."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import RLock

from .barcode_rules import BarcodeRuleEngine, LabelPayload, route_shared_scan
from .models import Phase, StationId, StationSelection
from .plc import POINTS, signal_scan_and_wait_clear
from .scanner import ScannerGuard


class ProductionCoordinator:
    def __init__(self, engine: BarcodeRuleEngine, controllers: dict[StationId, object], plc,
                 handshake_timeout_s: float = 5.0) -> None:
        if set(controllers) != set(StationId):
            raise ValueError("生产协调器必须同时提供 A/B 控制器")
        self.engine, self.controllers, self.plc = engine, controllers, plc
        self.handshake_timeout_s = handshake_timeout_s
        self.payloads: dict[StationId, LabelPayload] = {}
        self.people: dict[StationId, str] = {}
        self.modes: dict[StationId, str] = {}
        self.guard = ScannerGuard()
        self._lock = RLock()
        # A/B start handling is deliberately independent.  The PLC owns the
        # M16.0/M16.1 bits; this coordinator only observes false -> true edges
        # and never writes or clears either start bit.
        self._station_locks = {station: RLock() for station in StationId}
        self._start_levels: dict[StationId, bool] | None = None
        self._start_executor = ThreadPoolExecutor(max_workers=len(StationId),
                                                  thread_name_prefix="plc-start")
        self._start_futures: dict[StationId, Future] = {}
        self._start_errors: dict[StationId, str] = {}
        self._pending_start_errors: dict[StationId, str] = {}
        self._closed = False

    def configure_station(self, station: StationId, product: str, person: str,
                          test_mode: str) -> LabelPayload:
        with self._lock:
            controller = self.controllers[station]
            if controller.phase not in (Phase.IDLE, Phase.WAIT_SCAN, Phase.COMPLETE):
                raise RuntimeError(f"工位 {station.value} 周期中，禁止修改型号/人员")
            if not person.strip() or test_mode not in ("single", "dual"):
                raise ValueError("人员或检测模式无效")
            payload = self.engine.generate(product, station)
            self.engine.publish(payload)
            self.payloads[station] = payload
            self.people[station] = person.strip()
            self.modes[station] = test_mode
            return payload

    def handle_scan(self, code: str) -> StationId:
        with self._lock:
            if set(self.payloads) != set(StationId):
                raise RuntimeError("A/B 均须先完成型号和二维码准备")
            busy = {station for station, controller in self.controllers.items()
                    if controller.phase not in (Phase.IDLE, Phase.WAIT_SCAN, Phase.COMPLETE)}
            station = route_shared_scan(code,
                                        {item: payload.barcode_text for item, payload in self.payloads.items()},
                                        busy)
            if not self.guard.accept(code):
                raise ValueError("重复扫码")
            payload = self.payloads[station]
            selection = StationSelection(
                station, payload.product_id, self.people[station], self.modes[station],
                payload.serial_no, payload.barcode_text, payload.customer_model,
                str(payload.ateq_program), str(payload.template_path))
            controller = self.controllers[station]
            if controller.phase is Phase.COMPLETE:
                controller.reset()
            controller.scan_selection(selection)
        try:
            signal_scan_and_wait_clear(self.plc, station, self.handshake_timeout_s)
        except Exception as exc:
            controller.fault(str(exc))
            raise
        return station

    def handle_plc_start_edge(self, station: StationId):
        """Run the next test for one PLC start edge.

        M16.0/M16.1 are PLC-owned output bits.  The caller must only invoke
        this method after observing a rising edge; this method itself performs
        no PLC write.  A READY station performs its first test and a WAIT_2
        station performs its second test.  Any other phase is rejected and
        faulted so a stale or unsolicited PLC pulse cannot start a cycle.
        """
        if not isinstance(station, StationId):
            raise ValueError("无效工位")
        with self._station_locks[station]:
            controller = self.controllers[station]
            if controller.phase is Phase.READY:
                return controller.test_first()
            if controller.phase is Phase.WAIT_2:
                return controller.test_second()
            reason = (f"工位 {station.value} PLC启动上升沿时状态为 "
                      f"{controller.phase.value}，拒绝启动")
            controller.fault(reason)
            raise RuntimeError(reason)

    def poll_plc_start_edges(self) -> tuple[StationId, ...]:
        """Observe M16.0/M16.1 and dispatch newly-risen A/B edges.

        The first poll only establishes the current PLC levels.  This avoids
        treating a bit that was already high when the application started as
        a new command.  A and B are submitted to separate workers, so a
        blocking ATEQ response on one station does not block the other.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("生产协调器已关闭")
            current = {
                station: bool(self.plc.read_bit(*POINTS["start"][station]))
                for station in StationId
            }
            previous = self._start_levels
            self._start_levels = current
            if previous is None:
                return ()
            edges = tuple(
                station for station in StationId
                if current[station] and not previous[station]
            )
            for station in edges:
                future = self._start_futures.get(station)
                if future is not None and not future.done():
                    reason = (f"工位 {station.value} PLC启动上升沿时上一测试仍在执行，"
                              "拒绝重复启动")
                    self._start_errors[station] = reason
                    # Do not mutate the controller while its ATEQ worker is
                    # changing phase.  The worker applies this pending fault
                    # immediately after its transaction returns.
                    self._pending_start_errors[station] = reason
                    continue
                self._start_errors.pop(station, None)
                self._start_futures[station] = self._start_executor.submit(
                    self._run_plc_start, station)
            return edges

    def wait_for_plc_start(self, station: StationId, timeout: float | None = None):
        """Wait for the most recently dispatched edge on ``station``.

        This is useful to an integration loop or a deterministic test.  It
        returns the controller's measurement result and re-raises any test
        exception from the worker.
        """
        with self._lock:
            future = self._start_futures.get(station)
        if future is None:
            return None
        return future.result(timeout=timeout)

    def plc_start_error(self, station: StationId) -> str:
        """Return the latest asynchronous start error for one station."""
        with self._lock:
            return self._start_errors.get(station, "")

    def close(self) -> None:
        """Stop the edge dispatcher after active A/B tests have returned."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._start_executor.shutdown(wait=True)

    def __enter__(self) -> "ProductionCoordinator":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _run_plc_start(self, station: StationId):
        try:
            return self.handle_plc_start_edge(station)
        except Exception as exc:
            with self._lock:
                self._start_errors[station] = str(exc)
            raise
        finally:
            with self._lock:
                pending = self._pending_start_errors.pop(station, "")
            if pending:
                self.controllers[station].fault(pending)
