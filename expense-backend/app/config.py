import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    my_company_tax_id: str = ""
    db_path: str = "data/expense.db"
    storage_dir: str = "data/receipts"
    default_rules: list = field(default_factory=list)
    cors_origins: list = field(default_factory=list)
    device_key_default_days: int = 365
    auth_fail_limit: int = 10
    auth_fail_window_s: int = 900
    device_rate_per_min: int = 60
    daily_submit_limit: int = 100
    daily_upload_limit_bytes: int = 200 * 1024 * 1024
    max_file_bytes: int = 5 * 1024 * 1024
    max_request_bytes: int = 15 * 1024 * 1024
    access_log_dir: str = "data/logs"
    access_log_retention_days: int = 90
    retention_days: int = 1825


def load_settings() -> Settings:
    """讀 config/settings.yaml（可用 EXPENSE_CONFIG 指定），EXPENSE_DB_PATH / EXPENSE_STORAGE_DIR 可覆寫。"""
    path = Path(os.environ.get("EXPENSE_CONFIG", ROOT / "config" / "settings.yaml"))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    s = Settings(
        my_company_tax_id=str((raw.get("company") or {}).get("tax_id") or raw.get("my_company_tax_id", "")),
        db_path=os.environ.get("EXPENSE_DB_PATH", raw.get("db_path", Settings.db_path)),
        storage_dir=os.environ.get("EXPENSE_STORAGE_DIR", raw.get("storage_dir", Settings.storage_dir)),
        default_rules=raw.get("default_rules", []),
        cors_origins=list(raw.get("cors_allowed_origins") or []),
        device_key_default_days=int((raw.get("auth") or {}).get("device_key_default_days", 365)),
        auth_fail_limit=int((raw.get("auth") or {}).get("fail_limit", 10)),
        auth_fail_window_s=int((raw.get("auth") or {}).get("fail_window_seconds", 900)),
        device_rate_per_min=int((raw.get("auth") or {}).get("device_rate_per_min", 60)),
        daily_submit_limit=int((raw.get("limits") or {}).get("daily_submit_limit", 100)),
        daily_upload_limit_bytes=int((raw.get("limits") or {}).get("daily_upload_limit_mb", 200)) * 1024 * 1024,
        max_file_bytes=int((raw.get("limits") or {}).get("max_file_mb", 5)) * 1024 * 1024,
        max_request_bytes=int((raw.get("limits") or {}).get("max_request_mb", 15)) * 1024 * 1024,
        access_log_dir=os.environ.get("EXPENSE_LOG_DIR", (raw.get("logging") or {}).get("access_log_dir", "data/logs")),
        access_log_retention_days=int((raw.get("logging") or {}).get("retention_days", 90)),
        retention_days=int((raw.get("retention") or {}).get("retention_days", 1825)),
    )
    for attr in ("db_path", "storage_dir", "access_log_dir"):
        p = Path(getattr(s, attr))
        if not p.is_absolute():
            setattr(s, attr, str(ROOT / p))
    return s
