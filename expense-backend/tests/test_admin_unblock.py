"""M1 修訂 6（使用者 2026-10-24 要求）：管理員在手機上解除登入封鎖。
只有 admin 角色能呼叫；清除所有來源的失敗計數（Funnel 後可能所有人共用同一來源）。"""
import json
from pathlib import Path

import pytest

from app import admin, database
from tests.conftest import auth

URL = "/api/v1/admin/unblock"


@pytest.fixture
def settings(settings, tmp_path):
    settings.access_log_dir = str(tmp_path / "logs")  # conftest 預設寫到共用的 data/logs，這裡要隔離才能計數
    return settings


def _block(client, n=10):
    for _ in range(n):
        client.post("/api/v1/pair", json={"token": "pt_wrong"}, headers={"Authorization": ""})


def test_only_admin_can_unblock(client, keys):
    assert client.post(URL, headers={"Authorization": ""}).status_code == 401
    assert client.post(URL).status_code == 403  # submitter
    assert client.post(URL, headers=auth(keys["reviewer"])).status_code == 403
    assert client.post(URL, headers=auth(keys["admin"])).status_code == 200


def test_unblock_lets_pairing_work_again(client, keys, settings):
    _block(client)
    c = database.connect(settings.db_path)
    token = admin.create_pairing_code(c, "王志明")["token"]
    c.close()
    assert client.post("/api/v1/pair", json={"token": token}, headers={"Authorization": ""}).status_code == 429
    r = client.post(URL, headers=auth(keys["admin"]))
    assert r.status_code == 200 and r.json() == {"ok": True, "cleared": 1}
    assert client.post("/api/v1/pair", json={"token": token}, headers={"Authorization": ""}).status_code == 200


def test_unblock_when_nothing_blocked_reports_zero(client, keys):
    client.post("/api/v1/pair", json={"token": "pt_wrong"}, headers={"Authorization": ""})  # 1 次失敗，未達封鎖
    assert client.post(URL, headers=auth(keys["admin"])).json() == {"ok": True, "cleared": 0}


def test_unblock_is_logged_with_admin_device(client, keys, settings):
    client.post(URL, headers=auth(keys["admin"]))
    logs = [json.loads(l) for p in Path(settings.access_log_dir).glob("access-*.log")
            for l in p.read_text(encoding="utf-8").splitlines()]
    ev = [e for e in logs if e.get("event") == "unblock"]
    assert len(ev) == 1 and ev[0]["cleared"] == 0 and ev[0]["device_id"]
