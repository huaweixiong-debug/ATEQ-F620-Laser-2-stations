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
    """真实部署配置（live.toml 的键全集）必须能解析。"""
    body = Path("config/live.toml").read_text(encoding="utf-8")
    body = body.split("#", 1)[0] if False else body
    path = tmp_path / "live.toml"
    path.write_text(body, encoding="utf-8")
    settings = Settings.from_toml(path)
    assert settings.mode.value == "live"
    assert settings.points_confirmed is False  # 点位表尚未确认，LIVE 预检会拦截
