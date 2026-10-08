"""预检健壮性：设备探测失败必须生成 BLOCKED 报告，而不是抛栈崩溃。"""
import dataclasses

import pytest

import app.live_preflight as live_preflight
from app.config import Settings
from app.live_preflight import PreflightCheck
from app.models import RunMode, StationId


class _StubRepository:
    def __init__(self, **kwargs):
        pass

    def connect_and_verify(self) -> None:
        pass


def _live_settings(tmp_path):
    (tmp_path / "日期设置.ini").write_text(
        "[P1]\n日期=YYYYMMDD\nATEQ程序号=1\n", encoding="utf-8")
    credentials = tmp_path / "db.json"
    credentials.write_text('{"user": "u", "password": "p"}', encoding="utf-8")
    return dataclasses.replace(
        Settings(),
        mode=RunMode.LIVE,
        station=StationId.A,
        points_confirmed=False,
        ports_confirmed=False,
        weight_enabled=True,
        weight_com="COM6",
        data_dir=tmp_path,
        laser_dir=tmp_path,
        laser_filename="激光码信息.txt",
        credential_path=credentials,
        plc_profile="fx",
        plc_com="COM3",
        plc_relay_host="",
    )


def test_preflight_weight_probe_failure_blocks_instead_of_crashing(tmp_path, monkeypatch):
    settings = _live_settings(tmp_path)
    monkeypatch.setattr(live_preflight, "load_points", lambda path: {})
    monkeypatch.setattr(live_preflight, "_probe_plc_bits",
                        lambda settings: PreflightCheck("PLC 点位读回", True, "stub"))
    monkeypatch.setattr(live_preflight, "PyMySQLRepository", _StubRepository)

    def explode(settings):
        raise ValueError("称重响应长度 0 != 7")

    monkeypatch.setattr(live_preflight, "_probe_weight", explode)
    report = live_preflight.run_preflight(settings)
    assert report.passed is False
    weight = [check for check in report.checks if check.name == "称重"]
    assert len(weight) == 1
    assert weight[0].passed is False
    assert "称重响应长度" in weight[0].detail
    assert "BLOCKED 称重" in report.format()
