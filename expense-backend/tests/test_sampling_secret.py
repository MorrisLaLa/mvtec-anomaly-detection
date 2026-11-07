"""S2：抽查不可由用戶端預測。抽樣以伺服器端秘密（自動產生、存在資料目錄、不進備份）做 HMAC。"""
import os
import stat

from fastapi.testclient import TestClient

from app import admin, database
from app.config import Settings
from app.main import create_app
from app.review import is_sampled


def _app(tmp_path, **over):
    s = Settings(db_path=str(tmp_path / "t.db"), storage_dir=str(tmp_path / "receipts"), review_sample_percent=50, **over)
    database.init_db(s.db_path, [])
    c = database.connect(s.db_path)
    key = admin.create_device(c, "王志明", "submitter")["key"]
    c.close()
    return create_app(s), key, s


def test_secret_changes_sampling():
    ids = [f"id-{i}" for i in range(200)]
    a = [is_sampled(1, i, 10, secret=b"A" * 32) for i in ids]
    b = [is_sampled(1, i, 10, secret=b"B" * 32) for i in ids]
    assert a != b and 5 < sum(a) < 40


def test_secret_is_generated_once_persisted_and_private(tmp_path):
    app1, _, s = _app(tmp_path)
    secret = app1.state.sampling_secret
    assert isinstance(secret, bytes) and len(secret) >= 32
    path = tmp_path / "sampling.key"
    assert path.exists() and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert create_app(s).state.sampling_secret == secret  # 重啟後沿用同一把


def test_secret_not_inside_storage_dir(tmp_path):
    """備份會帶走資料庫與收據目錄；秘密不得放在收據目錄裡。"""
    app, _, s = _app(tmp_path)
    assert not (tmp_path / "receipts").exists() or not list((tmp_path / "receipts").rglob("sampling.key"))


def test_submissions_are_sampled_with_the_server_secret_not_predictable_without_it(tmp_path):
    """攻擊：攻擊者知道演算法但不知道秘密，自己算出『不會被抽到』的 client_id。期望：伺服器的結果與攻擊者算的不同。"""
    app, key, _ = _app(tmp_path)
    with TestClient(app, headers={"Authorization": f"Bearer {key}"}) as c:
        cids = [f"C{i}" for i in range(20)]
        for cid in cids:
            r = c.post("/api/v1/submit", data={"client_id": cid, "amount": "100", "doc_type": "無單據", "category": "餐飲"})
            assert r.status_code == 200 and r.json()["review_status"] == "approved", r.text
        conn = app.state.db_factory()
        rows = dict(conn.execute("SELECT client_id, sampled FROM expenses").fetchall())
        dev = conn.execute("SELECT device_id FROM expenses LIMIT 1").fetchone()[0]
        conn.close()
    secret = app.state.sampling_secret
    assert all(rows[c] == int(is_sampled(dev, c, 50, secret=secret)) for c in cids)
    assert any(rows[c] != int(is_sampled(dev, c, 50)) for c in cids)  # 沒有秘密就猜不準
