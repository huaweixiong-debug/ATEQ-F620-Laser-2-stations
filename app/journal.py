"""Atomic durable cycle journal with explicit recovery-required semantics."""
from __future__ import annotations
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .models import RecoveryRecord, StationId, Phase

class CycleJournal:
    def __init__(self, path: Path) -> None:
        self.path, self.archive = path, path.with_suffix(".archive.jsonl")
        self.audit_path = path.with_suffix(".audit.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, *, station: str, cycle_id: str, phase: str, **state: Any) -> None:
        payload = {"station": station, "cycle_id": cycle_id, "phase": phase,
                   "updated_at": datetime.now(timezone.utc).isoformat(), **state}
        # Legacy callers may still provide a small dictionary. New callers
        # should pass a complete RecoveryRecord through write_record().
        if "schema_version" not in payload:
            payload["schema_version"] = 1
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def write_record(self, record: RecoveryRecord) -> None:
        payload = record.to_dict()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def recover(self) -> dict[str, Any] | None:
        if not self.path.exists(): return None
        try: data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise RuntimeError("RECOVERY_REQUIRED: journal 损坏") from exc
        if not isinstance(data, dict) or not data.get("cycle_id") or not data.get("station"):
            raise RuntimeError("RECOVERY_REQUIRED: journal 字段不完整")
        return data

    def recover_record(self) -> RecoveryRecord | None:
        data = self.recover()
        if data is None:
            return None
        if int(data.get("schema_version", 1)) != RecoveryRecord.VERSION:
            raise RuntimeError("RECOVERY_REQUIRED: journal 不是完整版本化恢复记录")
        try:
            return RecoveryRecord.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("RECOVERY_REQUIRED: journal 字段校验失败") from exc

    def archive_and_clear(self, reason: str, audit: dict[str, Any] | None = None) -> None:
        if self.path.exists():
            original = self.recover()
            if audit is not None:
                payload = dict(audit)
                payload.setdefault("reason", reason)
                payload.setdefault("resolved_at", datetime.now(timezone.utc).isoformat())
                payload.setdefault("snapshot", original)
                payload.setdefault("snapshot_sha256", hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest())
                # Persist audit first. If this fails the source journal remains.
                with self.audit_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            with self.archive.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"reason": reason, "record": original}, ensure_ascii=False) + "\n")
            self.path.unlink()

    def audit_records(self) -> list[dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        return [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
