"""M4 單元 2：備份目的地介面（本機＋假物件儲存）、備份／還原、還原演練（驗收 6、11）。"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import backup, database
from app.backup import FakeObjectClient, LocalBackupTarget, ObjectStoreTarget
from app.notify import LogNotifier

NOW = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)


@pytest.fixture(params=["local", "object"])
def target(request, tmp_path):
    if request.param == "local":
        return LocalBackupTarget(str(tmp_path / "bk"))
    return ObjectStoreTarget(FakeObjectClient(), "bucket")


# ---------- 合約測試：本機與（假的）物件儲存實作都必須通過 ----------
def test_contract_put_get_roundtrip(target, tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"hello")
    target.put("files/a/b.png", str(src))
    out = tmp_path / "out.bin"
    target.get("files/a/b.png", str(out))
    assert out.read_bytes() == b"hello"


def test_contract_versions_are_kept(target, tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"v1")
    v1 = target.put("k", str(src))
    src.write_bytes(b"v2")
    v2 = target.put("k", str(src))
    assert v1 != v2 and target.versions("k") == [v1, v2]
    old, new = tmp_path / "old", tmp_path / "new"
    target.get("k", str(old), version=v1)
    target.get("k", str(new))
    assert old.read_bytes() == b"v1" and new.read_bytes() == b"v2"


def test_contract_keys_prefix_and_missing(target, tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"x")
    for k in ("files/1", "files/2", "db/x"):
        target.put(k, str(src))
    assert target.keys("files/") == ["files/1", "files/2"]
    with pytest.raises(KeyError):
        target.get("nope", str(tmp_path / "z"))


def test_local_target_rejects_path_escape(tmp_path):
    t = LocalBackupTarget(str(tmp_path / "bk"))
    src = tmp_path / "a.bin"
    src.write_bytes(b"x")
    with pytest.raises(ValueError):
        t.put("../evil", str(src))


# ---------- 備份／還原 ----------
@pytest.fixture
def world(settings, keys, tmp_path):
    """已有 3 筆申報與 3 個圖片檔的系統。"""
    conn = database.connect(settings.db_path)
    storage = Path(settings.storage_dir)
    for i in range(3):
        f = storage / "ns1" / f"UY{i}" / "receipt.png"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(f"image-{i}".encode() * 50)
        conn.execute("INSERT INTO expenses (device_id, client_id, payload_hash, amount, review_status, receipt_path) "
                     "VALUES (1, ?, 'h', ?, 'approved', ?)", (f"c{i}", 100 + i, str(f)))
    conn.commit()
    conn.close()
    settings.access_log_dir = str(tmp_path / "logs")   # 事件記錄寫到暫存目錄，不污染專案
    return settings


def tree_hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(root).rglob("*")) if p.is_file()}


def count(db):
    c = database.connect(db)
    try:
        return c.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
    finally:
        c.close()


def test_backup_then_restore_after_losing_everything(world, target, tmp_path):
    before = tree_hashes(world.storage_dir)
    assert backup.run_backup(world, target, NOW) is True
    # 刪光本機資料庫與圖片
    Path(world.db_path).unlink()
    import shutil
    shutil.rmtree(world.storage_dir)
    backup.restore(target, world.db_path, world.storage_dir)
    assert count(world.db_path) == 3
    assert tree_hashes(world.storage_dir) == before


def test_second_backup_uploads_only_new_files(world, tmp_path):
    t = LocalBackupTarget(str(tmp_path / "bk"))
    backup.run_backup(world, t, NOW)
    first = len(t.versions("files/ns1/UY0/receipt.png"))
    new = Path(world.storage_dir) / "ns1" / "UY9" / "receipt.png"
    new.parent.mkdir(parents=True)
    new.write_bytes(b"new image")
    backup.run_backup(world, t, NOW)
    assert len(t.versions("files/ns1/UY0/receipt.png")) == first == 1   # 沒變的不重傳
    assert len(t.versions("files/ns1/UY9/receipt.png")) == 1
    assert len(t.versions("db/expense.db")) == 2                        # 資料庫每次都有新版本


def test_restore_refuses_to_overwrite_existing_database(world, tmp_path):
    t = LocalBackupTarget(str(tmp_path / "bk"))
    backup.run_backup(world, t, NOW)
    with pytest.raises(FileExistsError):
        backup.restore(t, world.db_path, str(tmp_path / "other"))


def test_restore_detects_tampered_backup(world, tmp_path):
    t = LocalBackupTarget(str(tmp_path / "bk"))
    backup.run_backup(world, t, NOW)
    victim = next(Path(t.root).rglob("receipt.png*"))
    victim.write_bytes(b"tampered")
    with pytest.raises(backup.BackupError):
        backup.restore(t, str(tmp_path / "r.db"), str(tmp_path / "rs"))


# ---------- 事件與通知 ----------
class Boom(LocalBackupTarget):
    def put(self, key, src):
        raise OSError("儲存桶不存在")


def test_failed_backup_notifies_and_logs_event(world, tmp_path):
    n = LogNotifier(str(tmp_path / "n.log"))
    assert backup.run_backup(world, Boom(str(tmp_path / "bk")), NOW, notifier=n) is False
    assert n.sent[0]["kind"] == "backup_failed" and "儲存桶不存在" in n.sent[0]["body"]
    events = [json.loads(l).get("event") for p in Path(world.access_log_dir).glob("*.log") for l in p.read_text(encoding="utf-8").splitlines()]
    assert "backup_failed" in events and "backup_ok" not in events


def test_successful_backup_logs_ok_event_and_is_quiet(world, tmp_path):
    n = LogNotifier(str(tmp_path / "n.log"))
    assert backup.run_backup(world, LocalBackupTarget(str(tmp_path / "bk")), NOW, notifier=n) is True
    assert n.sent == []
    events = [json.loads(l).get("event") for p in Path(world.access_log_dir).glob("*.log") for l in p.read_text(encoding="utf-8").splitlines()]
    assert "backup_ok" in events


# ---------- 還原演練 ----------
def test_drill_passes_and_reports_counts(world, tmp_path):
    t = LocalBackupTarget(str(tmp_path / "bk"))
    backup.run_backup(world, t, NOW)
    rep = backup.drill(world, t, str(tmp_path / "drill"))
    assert rep["ok"] is True and rep["expenses_live"] == rep["expenses_restored"] == 3
    assert rep["files_live"] == rep["files_restored"] == 3 and rep["mismatched_files"] == []
    assert "通過" in rep["text"]
    assert list((tmp_path / "drill").glob("*")) == []          # 演練用的暫存資料已清除


def test_drill_fails_when_backup_misses_a_file(world, tmp_path):
    t = LocalBackupTarget(str(tmp_path / "bk"))
    backup.run_backup(world, t, NOW)
    next(Path(t.root).rglob("receipt.png*")).unlink()
    n = LogNotifier(str(tmp_path / "n.log"))
    rep = backup.drill(world, t, str(tmp_path / "drill"), notifier=n)
    assert rep["ok"] is False and "失敗" in rep["text"]
    assert n.sent and n.sent[0]["kind"] == "backup_failed"


def test_drill_fails_when_live_data_is_newer_than_backup_is_not_a_failure(world, tmp_path):
    """演練比對的是備份當下的狀態：備份之後新增的資料不算不一致。"""
    t = LocalBackupTarget(str(tmp_path / "bk"))
    backup.run_backup(world, t, NOW)
    c = database.connect(world.db_path)
    c.execute("INSERT INTO expenses (device_id, client_id, payload_hash, amount) VALUES (1, 'late', 'h', 5)")
    c.commit()
    c.close()
    assert backup.drill(world, t, str(tmp_path / "drill"))["ok"] is True


def test_cli_run_and_drill(world, tmp_path):
    world.backup_local_dir = str(tmp_path / "bk")
    world.notify_log_path = str(tmp_path / "n.log")
    assert backup.main(["run"], settings=world) == 0
    assert backup.main(["drill", str(tmp_path / "d")], settings=world) == 0
    assert backup.main(["bogus"], settings=world) == 2


def test_drill_fails_when_a_live_receipt_differs_from_its_backup(world, tmp_path):
    """收據圖片寫入後不應改變；備份後線上檔案內容不同＝損毀或遭竄改，演練要報失敗並列出檔名。"""
    t = LocalBackupTarget(str(tmp_path / "bk"))
    backup.run_backup(world, t, NOW)
    victim = Path(world.storage_dir) / "ns1" / "UY1" / "receipt.png"
    victim.write_bytes(b"corrupted")
    rep = backup.drill(world, t, str(tmp_path / "drill"))
    assert rep["ok"] is False and rep["mismatched_files"] == ["ns1/UY1/receipt.png"]
