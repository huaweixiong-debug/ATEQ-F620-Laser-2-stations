"""Keep test runs away from production-side state files."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 项目根目录加入 sys.path，使 `from app...` 在任何工作目录下可用。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 校准状态持久化文件：测试会话使用独立临时文件，避免污染/依赖 D:\ATEQ 下的
# 生产状态。持久化代码读取 LEAKTEST_CAL_STATE 环境变量。
_STATE_DIR = tempfile.mkdtemp(prefix="laserleak-cal-state-")
os.environ["LEAKTEST_CAL_STATE"] = os.path.join(_STATE_DIR, "calibration_state.json")

# UI 测试统一离屏渲染。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _fresh_calibration_state():
    """每个用例前清空校准状态文件，避免用例间顺序耦合。"""
    path = os.environ["LEAKTEST_CAL_STATE"]
    if os.path.exists(path):
        os.remove(path)
    yield


@pytest.fixture
def station_parts(tmp_path):
    """Return (controller, repository, marker, plc, journal_path)."""
    from app.ateq import FakeAteq
    from app.config import Settings
    from app.journal import CycleJournal
    from app.laser import FakeMarker
    from app.models import StationId
    from app.plc import FakePlc
    from app.repository import FakeRepository
    from app.station import StationController
    from tests.helpers import make_security

    settings = Settings()
    repository = FakeRepository(settings)
    marker = FakeMarker()
    plc = FakePlc()
    journal_path = tmp_path / "journal.json"
    controller = StationController(StationId.A, repository, marker, FakeAteq(),
                                   CycleJournal(journal_path), safe_stop=plc,
                                   security=make_security(), mark_samples=False)
    return controller, repository, marker, plc, journal_path
