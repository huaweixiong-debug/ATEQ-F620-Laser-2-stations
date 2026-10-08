"""Role gate for operator and administrator actions."""
from __future__ import annotations
from enum import Enum
from pathlib import Path

class Role(str, Enum):
    OPERATOR = "operator"
    ADMIN = "admin"

def allows(role: Role, action: str) -> bool:
    safe = {"scan", "test", "reset", "query"}
    dangerous = {"manual_output", "settings", "reprint", "shutdown", "recovery_resolve",
                 "calibration_cancel"}
    return action in safe or (action in dangerous and role is Role.ADMIN)

class AuthSession:
    """Authentication provider.

    Demo credentials (admin/simulate-admin) stay valid for the simulator.
    When a password file (e.g. D:\\data\\管理员.txt) is provided, the admin
    account also accepts the first line of that file so the field machine
    can use its own password without code changes.
    """
    def __init__(self, demo: bool = True, password_file=None) -> None:
        self.role = Role.OPERATOR; self.demo = demo; self.username = ""
        self.password_file = password_file

    def _field_password(self) -> str:
        try:
            return Path(self.password_file).read_text(encoding="utf-8").splitlines()[0].strip() if self.password_file else ""
        except (OSError, IndexError):
            return ""

    def login(self, username: str, password: str) -> bool:
        if username != "admin":
            return False
        if password == "simulate-admin" or (self._field_password() and password == self._field_password()):
            self.role = Role.ADMIN; self.username = username; return True
        return False


class SecurityContext:
    """Mandatory service-layer authorization, independent of Qt widgets."""
    def __init__(self, session: AuthSession | None = None, license_status=None) -> None:
        self.session = session or AuthSession(demo=False)
        self.license_status = license_status
        self.audit_events: list[dict[str, str]] = []

    @property
    def role(self) -> Role:
        return self.session.role

    def require(self, action: str) -> None:
        if not allows(self.role, action):
            raise PermissionError(f"权限不足: {action}")
        self.audit(action)

    def allow_new_cycle(self) -> None:
        if self.license_status is None:
            raise PermissionError("缺少许可证，不能开始新周期")
        if not self.license_status.valid:
            raise PermissionError("许可证无效，不能开始新周期")

    def login(self, username: str, password: str) -> bool:
        ok = self.session.login(username, password)
        self.audit("login_success" if ok else "login_failure", username=username)
        return ok

    def audit(self, action: str, **extra: str) -> None:
        from datetime import datetime, timezone
        event = {"action": action, "actor": self.session.username or self.role.value,
                 "timestamp": datetime.now(timezone.utc).isoformat()}
        event.update({key: str(value) for key, value in extra.items()})
        self.audit_events.append(event)
