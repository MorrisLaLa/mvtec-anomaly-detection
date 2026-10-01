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


def load_settings() -> Settings:
    """讀 config/settings.yaml（可用 EXPENSE_CONFIG 指定），EXPENSE_DB_PATH / EXPENSE_STORAGE_DIR 可覆寫。"""
    path = Path(os.environ.get("EXPENSE_CONFIG", ROOT / "config" / "settings.yaml"))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    s = Settings(
        my_company_tax_id=str(raw.get("my_company_tax_id", "")),
        db_path=os.environ.get("EXPENSE_DB_PATH", raw.get("db_path", Settings.db_path)),
        storage_dir=os.environ.get("EXPENSE_STORAGE_DIR", raw.get("storage_dir", Settings.storage_dir)),
        default_rules=raw.get("default_rules", []),
    )
    for attr in ("db_path", "storage_dir"):
        p = Path(getattr(s, attr))
        if not p.is_absolute():
            setattr(s, attr, str(ROOT / p))
    return s
