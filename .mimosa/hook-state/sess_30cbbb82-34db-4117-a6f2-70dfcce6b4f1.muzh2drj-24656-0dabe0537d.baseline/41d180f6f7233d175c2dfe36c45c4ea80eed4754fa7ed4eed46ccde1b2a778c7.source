"""LIVE startup gate regressions: preflight result must reach the UI launcher."""
import pytest

import app.live_preflight
import app.main
import app.ui


class _Report:
    def __init__(self, passed: bool) -> None:
        self.passed = passed

    def format(self) -> str:
        return "PREFLIGHT PASS" if self.passed else "PREFLIGHT BLOCKED"


@pytest.fixture()
def launcher(monkeypatch):
    calls: list[dict] = []

    def fake_launch_ui(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(app.ui, "launch_ui", fake_launch_ui)
    monkeypatch.setattr(app.main, "_acquire_ui_instance_lock", lambda station: True)
    return calls


def test_live_ui_starts_after_passing_preflight(tmp_path, monkeypatch, launcher):
    monkeypatch.setattr(app.live_preflight, "run_preflight",
                        lambda settings: _Report(True))
    config = tmp_path / "missing-live.toml"
    rc = app.main.main(["--live-ui", "--preflight", "--live-config", str(config)])
    assert rc == 0
    assert launcher == [{"live": True, "live_config": config,
                         "preflight_passed": True}]


def test_live_ui_not_started_when_preflight_fails(tmp_path, monkeypatch, launcher):
    monkeypatch.setattr(app.live_preflight, "run_preflight",
                        lambda settings: _Report(False))
    rc = app.main.main(["--live-ui", "--preflight",
                        "--live-config", str(tmp_path / "missing-live.toml")])
    assert rc == 2
    assert launcher == []


def test_preflight_only_exits_without_ui(tmp_path, monkeypatch, launcher):
    monkeypatch.setattr(app.live_preflight, "run_preflight",
                        lambda settings: _Report(True))
    rc = app.main.main(["--preflight", "--config", str(tmp_path / "missing.toml")])
    assert rc == 0
    assert launcher == []
