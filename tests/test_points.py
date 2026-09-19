import pytest

from app.points import PointMapError, load_points, parse_address, sim_point_map


def test_parse_address_valid():
    assert parse_address("M16.0") == (16, 0)
    assert parse_address("m0.7") == (0, 7)
    assert parse_address(" M31.7 ") == (31, 7)


def test_parse_address_invalid():
    for value in ("16.0", "M16.8", "M32.0", "MX.1", "M1", "M1."):
        with pytest.raises(PointMapError):
            parse_address(value)


def test_sim_point_map_complete():
    from app.points import REQUIRED_SIGNALS, OPTIONAL_SIGNALS
    point_map = sim_point_map()
    for signal in REQUIRED_SIGNALS + OPTIONAL_SIGNALS:
        assert point_map.has(signal)
    assert point_map.address("laser_start") == (20, 0)
    assert point_map.address("laser_done") == (20, 1)


def test_load_points_valid(tmp_path):
    path = tmp_path / "points.toml"
    path.write_text('''
[points]
start = "M0.0"
reset = "M0.1"
calibration = "M1.0"
ng_sample = "M1.2"
ok_sample = "M1.3"
manual = "M2.0"
block = "M4.2"
stamp = "M4.6"
clamp = "M4.4"
transfer = "M4.0"
pressure = "M0.5"
door_disable = "M0.6"
laser_start = "M10.3"
''', encoding="utf-8")
    point_map = load_points(path)
    assert point_map.address("laser_start") == (10, 3)
    assert not point_map.has("laser_done")


def test_load_points_missing_required(tmp_path):
    path = tmp_path / "points.toml"
    path.write_text('[points]\nstart = "M0.0"\n', encoding="utf-8")
    with pytest.raises(PointMapError, match="缺少必填信号"):
        load_points(path)


def test_load_points_unknown_signal(tmp_path):
    path = tmp_path / "points.toml"
    path.write_text('''
[points]
start = "M0.0"
reset = "M0.1"
calibration = "M1.0"
ng_sample = "M1.2"
ok_sample = "M1.3"
manual = "M2.0"
block = "M4.2"
stamp = "M4.6"
clamp = "M4.4"
transfer = "M4.0"
pressure = "M0.5"
door_disable = "M0.6"
laser_start = "M10.3"
scan_ok = "M0.1"
''', encoding="utf-8")
    with pytest.raises(PointMapError, match="未知信号"):
        load_points(path)


def test_load_points_missing_file(tmp_path):
    with pytest.raises(PointMapError, match="不存在"):
        load_points(tmp_path / "nope.toml")


def test_repo_points_toml_loads():
    """仓库自带的占位点位表必须能加载（LIVE 由 points_confirmed 把关）。"""
    from pathlib import Path
    point_map = load_points(Path("config/points.toml"))
    assert point_map.has("laser_start")
