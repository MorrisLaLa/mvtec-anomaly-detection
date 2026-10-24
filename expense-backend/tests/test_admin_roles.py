"""M1 修訂 7（使用者 2026-10-24）：管理員最少 1 位、最多 2 位；可轉移、增加、減少。
主機指令與管理員手機都能操作；手機上不能拿掉「另一位」管理員（防手機被偷後把真正的管理員踢掉）。"""
import json
from pathlib import Path

import pytest

from app import admin, database
from tests.conftest import auth


@pytest.fixture
def settings(settings, tmp_path):
    settings.access_log_dir = str(tmp_path / "logs")
    return settings


def _conn(tmp_path, *devices):
    db = str(tmp_path / "r.db")
    database.init_db(db)
    c = database.connect(db)
    ids = {n: admin.create_device(c, n, r)["id"] for n, r in devices}
    return c, ids


def _role(c, name):
    return c.execute("SELECT role FROM devices WHERE name=? AND revoked_at IS NULL", (name,)).fetchone()[0]


# ---------- 規則（函式庫，CLI 與 HTTP 共用） ----------
def test_at_most_two_admins_on_create(tmp_path):
    c, _ = _conn(tmp_path, ("甲", "admin"), ("乙", "admin"))
    with pytest.raises(ValueError, match="最多 2 位"):
        admin.create_device(c, "丙", "admin")


def test_promote_up_to_two_and_never_three(tmp_path):
    c, ids = _conn(tmp_path, ("甲", "admin"), ("乙", "reviewer"), ("丙", "submitter"))
    admin.set_role(c, ids["乙"], "admin")
    assert _role(c, "乙") == "admin"
    with pytest.raises(ValueError, match="最多 2 位"):
        admin.set_role(c, ids["丙"], "admin")
    assert _role(c, "丙") == "submitter"


def test_cannot_demote_or_revoke_last_admin(tmp_path):
    c, ids = _conn(tmp_path, ("甲", "admin"), ("乙", "reviewer"))
    with pytest.raises(ValueError, match="至少要保留 1 位"):
        admin.set_role(c, ids["甲"], "reviewer")
    with pytest.raises(ValueError, match="至少要保留 1 位"):
        admin.revoke(c, "甲")
    assert _role(c, "甲") == "admin"


def test_demote_and_revoke_allowed_when_two(tmp_path):
    c, ids = _conn(tmp_path, ("甲", "admin"), ("乙", "admin"), ("丙", "reviewer"))
    admin.set_role(c, ids["乙"], "reviewer")
    assert _role(c, "乙") == "reviewer"
    admin.set_role(c, ids["乙"], "admin")
    admin.revoke(c, "乙")
    assert _role(c, "甲") == "admin"


def test_transfer_swaps_atomically(tmp_path):
    c, ids = _conn(tmp_path, ("甲", "admin"), ("乙", "submitter"))
    admin.transfer_admin(c, ids["甲"], ids["乙"])
    assert (_role(c, "甲"), _role(c, "乙")) == ("reviewer", "admin")


@pytest.mark.parametrize("case", ["from_not_admin", "to_already_admin", "to_revoked", "same"])
def test_transfer_rejects_bad_pairs(tmp_path, case):
    c, ids = _conn(tmp_path, ("甲", "admin"), ("乙", "admin"), ("丙", "reviewer"), ("丁", "submitter"))
    admin.revoke(c, "丁")
    a, b = {"from_not_admin": ("丙", "乙"), "to_already_admin": ("甲", "乙"), "to_revoked": ("甲", "丁"),
            "same": ("甲", "甲")}[case]
    with pytest.raises((ValueError, LookupError)):  # 找不到（已撤銷）是 LookupError，手機端對應 404
        admin.transfer_admin(c, ids[a], ids[b])
    assert (_role(c, "甲"), _role(c, "乙"), _role(c, "丙")) == ("admin", "admin", "reviewer")


def test_invalid_role_rejected(tmp_path):
    c, ids = _conn(tmp_path, ("甲", "admin"), ("乙", "reviewer"))
    with pytest.raises(ValueError):
        admin.set_role(c, ids["乙"], "root")


# ---------- 主機指令 ----------
def test_cli_set_role_transfer_and_refusals(tmp_path, capsys):
    c, _ = _conn(tmp_path, ("甲", "admin"), ("乙", "reviewer"), ("丙", "submitter"))
    db = str(tmp_path / "r.db")
    assert admin.main(["--db", db, "set-role", "--name", "乙", "--role", "admin"]) == 0
    assert admin.main(["--db", db, "set-role", "--name", "丙", "--role", "admin"]) == 2
    assert "最多 2 位" in capsys.readouterr().err
    assert admin.main(["--db", db, "set-role", "--name", "乙", "--role", "submitter"]) == 0
    assert admin.main(["--db", db, "transfer-admin", "--from", "甲", "--to", "丙"]) == 0
    assert (_role(c, "甲"), _role(c, "丙")) == ("reviewer", "admin")
    assert admin.main(["--db", db, "revoke", "--name", "丙"]) == 2  # 唯一的管理員
    assert "至少要保留 1 位" in capsys.readouterr().err


# ---------- 管理員手機（HTTP） ----------
def _ids(db):
    return {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM devices")}


def test_non_admin_cannot_touch_roles(client, keys):
    for k in (keys["submitter"], keys["reviewer"]):
        assert client.get("/api/v1/admin/devices", headers=auth(k)).status_code == 403
        assert client.post("/api/v1/admin/role", json={"device_id": 1, "role": "admin"}, headers=auth(k)).status_code == 403
        assert client.post("/api/v1/admin/transfer", json={"device_id": 1}, headers=auth(k)).status_code == 403


def test_device_list_has_no_secrets(client, keys):
    r = client.get("/api/v1/admin/devices", headers=auth(keys["admin"]))
    assert r.status_code == 200
    rows = r.json()
    assert {d["name"] for d in rows} == {"王志明", "陳主管", "管理員"}
    assert all(set(d) == {"id", "name", "role", "status", "is_me"} for d in rows)
    assert "ek_" not in r.text and "hash" not in r.text


def test_admin_promotes_and_limit_is_enforced(client, keys, db):
    ids, h = _ids(db), auth(keys["admin"])
    assert client.post("/api/v1/admin/role", json={"device_id": ids["陳主管"], "role": "admin"}, headers=h).status_code == 200
    r = client.post("/api/v1/admin/role", json={"device_id": ids["王志明"], "role": "admin"}, headers=h)
    assert r.status_code == 409 and "最多 2 位" in r.json()["detail"]


def test_phone_cannot_demote_the_other_admin(client, keys, db):
    ids, h = _ids(db), auth(keys["admin"])
    client.post("/api/v1/admin/role", json={"device_id": ids["陳主管"], "role": "admin"}, headers=h)
    r = client.post("/api/v1/admin/role", json={"device_id": ids["陳主管"], "role": "reviewer"}, headers=h)
    assert r.status_code == 403 and "主機" in r.json()["detail"]
    assert db.execute("SELECT role FROM devices WHERE id=?", (ids["陳主管"],)).fetchone()[0] == "admin"


def test_phone_can_demote_self_only_when_another_admin_exists(client, keys, db):
    ids, h = _ids(db), auth(keys["admin"])
    r = client.post("/api/v1/admin/role", json={"device_id": ids["管理員"], "role": "reviewer"}, headers=h)
    assert r.status_code == 409 and "至少要保留 1 位" in r.json()["detail"]
    client.post("/api/v1/admin/role", json={"device_id": ids["陳主管"], "role": "admin"}, headers=h)
    assert client.post("/api/v1/admin/role", json={"device_id": ids["管理員"], "role": "reviewer"}, headers=h).status_code == 200
    assert client.get("/api/v1/admin/devices", headers=h).status_code == 403  # 立即生效


def test_phone_transfer_own_admin(client, keys, db):
    ids, h = _ids(db), auth(keys["admin"])
    r = client.post("/api/v1/admin/transfer", json={"device_id": ids["王志明"]}, headers=h)
    assert r.status_code == 200
    assert dict(db.execute("SELECT name, role FROM devices").fetchall()) == {"王志明": "admin", "陳主管": "reviewer", "管理員": "reviewer"}
    assert client.get("/api/v1/admin/devices", headers=h).status_code == 403
    assert client.get("/api/v1/admin/devices", headers=auth(keys["submitter"])).status_code == 200


def test_phone_role_change_input_errors(client, keys, db):
    ids, h = _ids(db), auth(keys["admin"])
    assert client.post("/api/v1/admin/role", json={"device_id": ids["王志明"], "role": "root"}, headers=h).status_code == 422
    assert client.post("/api/v1/admin/role", json={"device_id": 9999, "role": "reviewer"}, headers=h).status_code == 404
    admin.revoke(db, "王志明")
    assert client.post("/api/v1/admin/role", json={"device_id": ids["王志明"], "role": "reviewer"}, headers=h).status_code == 404
    assert client.post("/api/v1/admin/transfer", json={"device_id": ids["王志明"]}, headers=h).status_code == 404


def test_role_changes_are_logged(client, keys, db, settings):
    ids, h = _ids(db), auth(keys["admin"])
    client.post("/api/v1/admin/role", json={"device_id": ids["陳主管"], "role": "admin"}, headers=h)
    client.post("/api/v1/admin/transfer", json={"device_id": ids["王志明"]}, headers=h)
    logs = [json.loads(l) for p in Path(settings.access_log_dir).glob("access-*.log") for l in p.read_text(encoding="utf-8").splitlines()]
    ev = [(e["event"], e["target_device_id"], e["to_role"]) for e in logs if e.get("event") in ("role_change", "admin_transfer")]
    assert ev == [("role_change", ids["陳主管"], "admin"), ("admin_transfer", ids["王志明"], "admin")]
