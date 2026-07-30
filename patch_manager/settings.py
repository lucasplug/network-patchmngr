from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    backup_dir: Path
    database_path: Path
    session_secure: bool
    backup_schedule_hour: int
    backup_retention_daily: int


def get_settings() -> Settings:
    data_dir = Path(os.getenv("PATCH_DATA_DIR", "./data")).resolve()
    backup_dir = Path(os.getenv("PATCH_BACKUP_DIR", "./backups")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=data_dir,
        backup_dir=backup_dir,
        database_path=data_dir / "patch-manager.db",
        session_secure=os.getenv("PATCH_SESSION_SECURE", "false").lower() == "true",
        backup_schedule_hour=max(0, min(23, int(os.getenv("PATCH_BACKUP_SCHEDULE_HOUR", "3")))),
        backup_retention_daily=max(1, int(os.getenv("PATCH_BACKUP_RETENTION_DAILY", "14"))),
    )
