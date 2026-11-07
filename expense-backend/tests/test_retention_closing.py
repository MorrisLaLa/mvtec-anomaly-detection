"""使用者 2026-11-07（會計確認）：保存 5 年從「年度決算完成後」起算。
以支出日期（無則發票日期、再無則申報日）所屬年度為準；決算完成期限預設為次年 06-30（設定可調）。"""
from datetime import datetime, timezone

import pytest

from app import maintenance


@pytest.mark.parametrize("day,until", [
    ("2026-01-15", "2032-06-30"), ("2026-12-31", "2032-06-30"), ("2027-01-01", "2033-06-30")])
def test_keep_until_is_closing_of_next_year_plus_five(day, until):
    assert maintenance.keep_until(day, years=5, closing="06-30") == until


def test_closing_deadline_is_configurable():
    assert maintenance.keep_until("2026-05-01", years=5, closing="05-31") == "2032-05-31"


def _submit(client, cid, expense_date=None):
    data = {"client_id": cid, "amount": "100", "doc_type": "無單據", "category": "餐飲"}
    if expense_date:
        data["expense_date"] = expense_date
    assert client.post("/api/v1/submit", data=data).status_code == 200


def _at(y, m, d):
    return datetime(y, m, d, 4, 0, tzinfo=timezone.utc)  # 台北中午


def test_purge_uses_expense_year_and_closing(client, db):
    _submit(client, "Y2020", "2020-03-01")   # 保存到 2026-06-30
    _submit(client, "Y2021", "2021-12-31")   # 保存到 2027-06-30
    assert maintenance.purge_expired(client.app, now=_at(2026, 6, 30))["deleted"] == 0
    assert maintenance.purge_expired(client.app, now=_at(2026, 7, 1))["deleted"] == 1
    assert [r[0] for r in db.execute("SELECT client_id FROM expenses")] == ["Y2021"]


def test_purge_falls_back_to_created_day_when_no_expense_date(client, db):
    _submit(client, "NODATE")
    created = db.execute("SELECT created_day FROM expenses").fetchone()[0]
    y = int(created[:4])
    assert maintenance.purge_expired(client.app, now=_at(y + 6, 6, 30))["deleted"] == 0
    assert maintenance.purge_expired(client.app, now=_at(y + 6, 7, 1))["deleted"] == 1


def test_settings_load_retention_years_and_closing(tmp_path, monkeypatch):
    from app.config import load_settings
    p = tmp_path / "s.yaml"
    p.write_text("retention:\n  retention_years: 7\n  closing_deadline: '05-31'\n", encoding="utf-8")
    monkeypatch.setenv("EXPENSE_CONFIG", str(p))
    s = load_settings()
    assert (s.retention_years, s.retention_closing_deadline) == (7, "05-31")
