from pathlib import Path

import pytest

from app.config import Settings


def write_toml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_minimal_defaults(tmp_path):
    path = write_toml(tmp_path, 'mode = "simulate"\n')
    settings = Settings.from_toml(path)
    assert settings.mode.value == "simulate"
    assert settings.station.value == "A"
    assert settings.ateq_com == "COM6"
    assert settings.laser_clear_after_seconds == 10.0
    assert settings.laser_encoding == "gbk"
    assert settings.mark_samples is False


def test_station_b_and_laser_values(tmp_path):
    path = write_toml(tmp_path, '''
mode = "live"
station = "B"
plc_ip = "192.168.1.20"
ateq_com = "COM4"
ateq_slave = 1
ports_confirmed = true
points_confirmed = true
laser_dir = "C:\\\\laser"
laser_filename = "mark.txt"
laser_encoding = "utf-8"
laser_clear_after_seconds = 5.0
laser_wait_done = true
mark_samples = true
''')
    settings = Settings.from_toml(path)
    assert settings.station.value == "B"
    assert settings.plc_ip == "192.168.1.20"
    assert settings.ateq_com == "COM4"
    assert settings.ateq_slave == 1
    assert settings.ports_confirmed is True
    assert settings.points_confirmed is True
    assert settings.laser_file() == Path("C:\\laser\\mark.txt")
    assert settings.laser_clear_after_seconds == 5.0
    assert settings.laser_wait_done is True
    assert settings.mark_samples is True


def test_unknown_key_rejected(tmp_path):
    path = write_toml(tmp_path, 'mode = "simulate"\nscanner_ip = "1.2.3.4"\n')
    with pytest.raises(ValueError, match="未知关键配置"):
        Settings.from_toml(path)


def test_invalid_station_rejected(tmp_path):
    path = write_toml(tmp_path, 'station = "C"\n')
    with pytest.raises(ValueError, match="station"):
        Settings.from_toml(path)


def test_invalid_com_rejected(tmp_path):
    path = write_toml(tmp_path, 'ateq_com = "LPT1"\n')
    with pytest.raises(ValueError, match="COM"):
        Settings.from_toml(path)


def test_clear_after_out_of_range(tmp_path):
    path = write_toml(tmp_path, 'laser_clear_after_seconds = 10000.0\n')
    with pytest.raises(ValueError, match="laser_clear_after_seconds"):
        Settings.from_toml(path)


def test_invalid_encoding_rejected(tmp_path):
    path = write_toml(tmp_path, 'laser_encoding = "not-a-codec"\n')
    with pytest.raises(ValueError, match="laser_encoding"):
        Settings.from_toml(path)


def test_invalid_newline_rejected(tmp_path):
    path = write_toml(tmp_path, 'laser_newline = ";"\n')
    with pytest.raises(ValueError, match="newline"):
        Settings.from_toml(path)


def test_repo_config_keys_accepted(tmp_path):
    """真实部署配置（live.toml 的键全集）必须能解析。

    注意：现场机器上 live.toml 会被替换为对应工位模板（点位确认后
    points_confirmed=true），因此本测试只校验键全集可解析，不校验
    points_confirmed 的取值（预检门禁由 test_live_preflight 覆盖）。
    """
    body = Path("config/live.toml").read_text(encoding="utf-8")
    body = body.split("#", 1)[0] if False else body
    path = tmp_path / "live.toml"
    path.write_text(body, encoding="utf-8")
    settings = Settings.from_toml(path)
    assert settings.mode.value == "live"
    assert isinstance(settings.points_confirmed, bool)


# ---------------------------------------------------------------------------
# 240429 新增配置键：PLC 档位/串口、称重、中转、校准周期
# ---------------------------------------------------------------------------

def test_new_240429_keys_defaults(tmp_path):
    settings = Settings.from_toml(write_toml(tmp_path, 'mode = "simulate"\n'))
    assert settings.plc_profile == "s7"
    assert settings.plc_com == "COM3"
    assert settings.plc_baud == 9600
    assert settings.weight_enabled is False
    assert settings.weight_register == 40002
    assert settings.weight_plc_register == "D900"
    assert settings.plc_relay_host == ""      # 空 = 本机直连 PLC
    assert settings.relay_enabled is False    # B 侧中转服务默认关闭
    assert settings.relay_port == 9101


def test_fx_profile_and_serial_keys(tmp_path):
    path = write_toml(tmp_path, '''
mode = "live"
plc_profile = "fx"
plc_com = "COM3"
plc_baud = 9600
plc_parity = "E"
plc_databits = 7
plc_stopbits = 1
points_confirmed = true
ports_confirmed = true
''')
    settings = Settings.from_toml(path)
    assert settings.plc_profile == "fx"
    assert settings.plc_com == "COM3"
    assert settings.plc_databits == 7


def test_invalid_plc_profile_rejected(tmp_path):
    path = write_toml(tmp_path, 'plc_profile = "x"\n')
    with pytest.raises(ValueError, match="plc_profile"):
        Settings.from_toml(path)


def test_invalid_parity_rejected(tmp_path):
    path = write_toml(tmp_path, 'plc_parity = "X"\n')
    with pytest.raises(ValueError, match="parity"):
        Settings.from_toml(path)


def test_weight_keys(tmp_path):
    path = write_toml(tmp_path, '''
weight_enabled = true
weight_com = "COM6"
weight_slave = 2
weight_register = "40002"
weight_plc_register = "D900"
''')
    settings = Settings.from_toml(path)
    assert settings.weight_enabled is True
    assert settings.weight_slave == 2
    assert settings.weight_plc_register == "D900"


def test_invalid_weight_register_rejected(tmp_path):
    path = write_toml(tmp_path, 'weight_register = "30002"\n')
    with pytest.raises(ValueError, match="weight_register"):
        Settings.from_toml(path)


def test_invalid_weight_plc_register_rejected(tmp_path):
    path = write_toml(tmp_path, 'weight_plc_register = "M100"\n')
    with pytest.raises(ValueError, match="weight_plc_register"):
        Settings.from_toml(path)


def test_relay_client_keys(tmp_path):
    path = write_toml(tmp_path, '''
plc_relay_host = "192.168.1.20"
plc_relay_port = 9101
plc_relay_token = "xiezhong-240429"
''')
    settings = Settings.from_toml(path)
    assert settings.plc_relay_host == "192.168.1.20"
    assert settings.plc_relay_port == 9101


def test_relay_host_requires_token(tmp_path):
    path = write_toml(tmp_path, 'plc_relay_host = "192.168.1.20"\n')
    with pytest.raises(ValueError, match="plc_relay_token"):
        Settings.from_toml(path)


def test_relay_server_requires_token(tmp_path):
    path = write_toml(tmp_path, 'relay_enabled = true\n')
    with pytest.raises(ValueError, match="relay_token"):
        Settings.from_toml(path)


def test_relay_server_config_accepted(tmp_path):
    path = write_toml(tmp_path, '''
relay_enabled = true
relay_port = 9202
relay_token = "xiezhong-240429"
''')
    settings = Settings.from_toml(path)
    assert settings.relay_enabled is True
    assert settings.relay_port == 9202
    assert settings.relay_token == "xiezhong-240429"


def test_calibration_period_hours_default_8():
    assert Settings().calibration_period_hours == 8


def test_calibration_period_hours_validation(tmp_path):
    path = write_toml(tmp_path, 'calibration_period_hours = 0\n')
    with pytest.raises(ValueError, match="calibration_period_hours"):
        Settings.from_toml(path)
