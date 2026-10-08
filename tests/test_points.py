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


# ---------------------------------------------------------------------------
# FX 档位（240429 箱体气密封机，三菱 FX 编程口）
# ---------------------------------------------------------------------------

FX_POINTS_TOML = """
[meta]
profile = "fx"

# FX 位元件按 byte.bit 拆分（元件号 = byte*8+bit）：
#   M0.0=M0  M0.1=M1  M0.5=M5  M58.2=M466  M58.3=M467  M111.3=M891
[points]
start = "M0.0"
result_ok = "M0.1"
result_ng = "M0.5"
laser_start = "M30.0"
laser_done = "M30.1"
sample = "M58.3"
isolation_init = "M58.2"
shield_cylinder = "M111.3"
pressure_alarm = "M110.6"
"""


def test_fx_profile_parses_high_m_addresses(tmp_path):
    path = tmp_path / "points_fx.toml"
    path.write_text(FX_POINTS_TOML, encoding="utf-8")
    point_map = load_points(path)
    assert point_map.address("start") == (0, 0)
    assert point_map.address("result_ng") == (0, 5)
    assert point_map.address("shield_cylinder") == (111, 3)   # M891
    assert point_map.address("isolation_init") == (58, 2)     # M466
    assert point_map.address("pressure_alarm") == (110, 6)    # M886


def test_fx_profile_allows_up_to_m8191(tmp_path):
    path = tmp_path / "points_fx.toml"
    path.write_text(FX_POINTS_TOML + 'extra_unused = "M8191.7"\n', encoding="utf-8")
    # 未知信号仍然拒绝（extra_unused 不在 FX 信号表里）
    from app.points import PointMapError
    with pytest.raises(PointMapError):
        load_points(path)


def test_fx_profile_requires_core_signals(tmp_path):
    path = tmp_path / "points_fx.toml"
    path.write_text('[meta]\nprofile = "fx"\n\n[points]\nstart = "M0.0"\n', encoding="utf-8")
    from app.points import PointMapError
    with pytest.raises(PointMapError) as excinfo:
        load_points(path)
    assert "result_ok" in str(excinfo.value)


def test_s7_profile_still_caps_m_area_at_31(tmp_path):
    path = tmp_path / "points_s7.toml"
    path.write_text('''
[meta]
profile = "s7"

[points]
start = "M16.0"
reset = "M0.3"
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
laser_start = "M20.0"
''', encoding="utf-8")
    from app.points import PointMapError
    load_points(path)  # 合法
    bad = tmp_path / "points_bad.toml"
    bad.write_text('[meta]\nprofile = "s7"\n\n[points]\nstart = "M100.0"\n', encoding="utf-8")
    with pytest.raises(PointMapError):
        load_points(bad)
