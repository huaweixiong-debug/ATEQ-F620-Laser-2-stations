"""Keep test runs away from production-side state files."""
import os
import tempfile

import pytest

# 校准状态持久化文件：测试会话使用独立临时文件，避免污染/依赖 D:\ATEQ 下的
# 生产状态。持久化代码读取 LEAKTEST_CAL_STATE 环境变量。
_STATE_DIR = tempfile.mkdtemp(prefix="leaktest-cal-state-")
os.environ["LEAKTEST_CAL_STATE"] = os.path.join(_STATE_DIR, "calibration_state.json")


@pytest.fixture(autouse=True)
def _fresh_calibration_state():
    """每个用例前清空校准状态文件，避免用例间顺序耦合。"""
    path = os.environ["LEAKTEST_CAL_STATE"]
    if os.path.exists(path):
        os.remove(path)
    yield
