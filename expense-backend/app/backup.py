"""M4：備份目的地介面、備份／還原、還原演練。
BackupTarget 有兩種實作：本機資料夾，以及通用的物件儲存（ObjectStoreTarget，搭配 GCS／S3 的薄轉接層；
轉接層需要雲端帳號與 SDK，待使用者提供後再接，這裡以 FakeObjectClient 做合約測試）。
備份內容含個資：儲存桶必須加密、限權、禁止公開（見 deploy/ 說明）。"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from .accesslog import AccessLog
from .notify import Notifier


class BackupError(Exception):
    pass


class BackupTarget(ABC):
    @abstractmethod
    def put(self, key: str, src: str) -> str:
        """上傳檔案，保留舊版本；回傳新版本代號。"""

    @abstractmethod
    def get(self, key: str, dest: str, version: str | None = None) -> None:
        """下載指定版本（預設最新）；不存在丟 KeyError。"""

    @abstractmethod
    def versions(self, key: str) -> list[str]:
        """由舊到新的版本代號。"""

    @abstractmethod
    def keys(self, prefix: str = "") -> list[str]: ...


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalBackupTarget(BackupTarget):
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str, version: str | None = None) -> Path:
        p = (self.root / (key if version is None else f"{key}@{version}")).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("key outside backup root")
        return p

    def versions(self, key):
        base = self._path(key)
        if not base.parent.exists():
            return []
        return sorted(p.name.split("@", 1)[1] for p in base.parent.glob(base.name + "@*") if p.is_file())

    def put(self, key, src):
        vs = self.versions(key)
        version = f"{(int(vs[-1]) + 1) if vs else 1:06d}"
        dest = self._path(key, version)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return version

    def get(self, key, dest, version=None):
        vs = self.versions(key)
        if not vs or (version is not None and version not in vs):
            raise KeyError(key)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key, version or vs[-1]), dest)

    def keys(self, prefix=""):
        if not self.root.exists():
            return []
        out = {str(p.relative_to(self.root)).rsplit("@", 1)[0] for p in self.root.rglob("*@*") if p.is_file()}
        return sorted(k for k in out if k.startswith(prefix))


class FakeObjectClient:
    """記憶體內的版本化物件儲存，用於合約測試；真實的 GCS／S3 轉接層需實作同樣四個方法。"""

    def __init__(self):
        self.objs: dict[tuple[str, str], list[bytes]] = {}

    def put_object(self, bucket, key, data: bytes) -> str:
        self.objs.setdefault((bucket, key), []).append(data)
        return str(len(self.objs[(bucket, key)]))

    def get_object(self, bucket, key, version=None) -> bytes:
        vs = self.objs.get((bucket, key))
        if not vs:
            raise KeyError(key)
        if version is None:
            return vs[-1]
        if not version.isdigit() or not 1 <= int(version) <= len(vs):
            raise KeyError(key)
        return vs[int(version) - 1]

    def list_versions(self, bucket, key) -> list[str]:
        return [str(i + 1) for i in range(len(self.objs.get((bucket, key), [])))]

    def list_keys(self, bucket, prefix="") -> list[str]:
        return sorted(k for (b, k) in self.objs if b == bucket and k.startswith(prefix))


class ObjectStoreTarget(BackupTarget):
    def __init__(self, client, bucket: str):
        self.client, self.bucket = client, bucket

    def put(self, key, src):
        return self.client.put_object(self.bucket, key, Path(src).read_bytes())

    def get(self, key, dest, version=None):
        data = self.client.get_object(self.bucket, key, version)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)

    def versions(self, key):
        return self.client.list_versions(self.bucket, key)

    def keys(self, prefix=""):
        return self.client.list_keys(self.bucket, prefix)


def make_target(settings) -> BackupTarget:
    return LocalBackupTarget(settings.backup_local_dir)


DB_KEY, MANIFEST_KEY = "db/expense.db", "manifests/latest.json"


def _event(settings, name, now, **f):
    try:
        AccessLog(settings.access_log_dir, settings.access_log_retention_days).event(name, now, **f)
    except OSError:
        pass


def _previous_manifest(target):
    try:
        with tempfile.TemporaryDirectory() as d:
            target.get(MANIFEST_KEY, f"{d}/m.json")
            return json.loads(Path(f"{d}/m.json").read_text(encoding="utf-8"))
    except KeyError:
        return {"files": {}}


def run_backup(settings, target: BackupTarget, now: datetime | None = None, notifier: Notifier | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "snap.db"
            src, dst = sqlite3.connect(settings.db_path), sqlite3.connect(snap)
            try:
                src.backup(dst)
                n = dst.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
            finally:
                src.close()
                dst.close()
            prev = _previous_manifest(target)["files"]
            manifest = {"created": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "expenses": n,
                        "db": {"sha256": _sha256(snap), "version": target.put(DB_KEY, str(snap))}, "files": {}}
            root = Path(settings.storage_dir)
            for p in sorted(root.rglob("*")) if root.exists() else []:
                if not p.is_file():
                    continue
                rel, sha = p.relative_to(root).as_posix(), _sha256(p)
                old = prev.get(rel)
                version = old["version"] if old and old["sha256"] == sha else target.put(f"files/{rel}", str(p))
                manifest["files"][rel] = {"sha256": sha, "version": version}
            mf = Path(tmp) / "m.json"
            mf.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            target.put(MANIFEST_KEY, str(mf))
    except Exception as e:  # noqa: BLE001 — 任何失敗都要通知，不能讓排程靜默失敗
        _event(settings, "backup_failed", now)
        if notifier:
            from .ops import notify_backup_failed
            notify_backup_failed(notifier, str(e))
        return False
    _event(settings, "backup_ok", now, files=len(manifest["files"]), expenses=manifest["expenses"])
    return True


def restore(target: BackupTarget, db_path: str, storage_dir: str, overwrite: bool = False) -> dict:
    """還原最新備份。每個檔案都比對雜湊，不符或缺漏丟 BackupError。回傳 manifest。"""
    if Path(db_path).exists() and not overwrite:
        raise FileExistsError(db_path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target.get(MANIFEST_KEY, f"{tmp}/m.json")
            m = json.loads(Path(f"{tmp}/m.json").read_text(encoding="utf-8"))
            target.get(DB_KEY, f"{tmp}/db", version=m["db"]["version"])
            if _sha256(f"{tmp}/db") != m["db"]["sha256"]:
                raise BackupError("資料庫備份雜湊不符")
            staged = Path(tmp) / "files"
            for rel, info in m["files"].items():
                out = staged / rel
                target.get(f"files/{rel}", str(out), version=info["version"])
                if _sha256(out) != info["sha256"]:
                    raise BackupError(f"檔案雜湊不符：{rel}")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f"{tmp}/db", db_path)
            for rel in m["files"]:
                dest = Path(storage_dir) / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staged / rel, dest)
            return m
    except KeyError as e:
        raise BackupError(f"備份缺少項目：{e}") from e


def drill(settings, target: BackupTarget, workdir: str, notifier: Notifier | None = None) -> dict:
    """還原演練：還原到暫存目錄，比對筆數與檔案雜湊，輸出報告；結束後清除暫存。"""
    live_db = sqlite3.connect(settings.db_path)
    try:
        live_n = live_db.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
    finally:
        live_db.close()
    root = Path(settings.storage_dir)
    live = {p.relative_to(root).as_posix(): _sha256(p) for p in root.rglob("*") if p.is_file()} if root.exists() else {}
    Path(workdir).mkdir(parents=True, exist_ok=True)
    rep = {"ok": False, "expenses_live": live_n, "expenses_restored": 0, "files_live": len(live), "files_restored": 0,
           "mismatched_files": []}
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        try:
            m = restore(target, f"{tmp}/r.db", f"{tmp}/files")
            c = sqlite3.connect(f"{tmp}/r.db")
            try:
                rep["expenses_restored"] = c.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
            finally:
                c.close()
            rep["files_restored"] = len(m["files"])
            rep["mismatched_files"] = sorted(r for r, i in m["files"].items() if r in live and live[r] != i["sha256"])
            rep["ok"] = rep["expenses_restored"] == m["expenses"] and not rep["mismatched_files"]
            rep["reason"] = "" if rep["ok"] else "筆數或檔案雜湊與備份不符"
        except (BackupError, OSError, sqlite3.Error) as e:
            rep["reason"] = str(e)
    rep["text"] = (f"還原演練{'通過' if rep['ok'] else '失敗'}：申報 {rep['expenses_restored']}／{rep['expenses_live']}（還原／目前）、"
                   f"檔案 {rep['files_restored']}／{rep['files_live']}、雜湊不符 {len(rep['mismatched_files'])}"
                   + ("" if rep["ok"] else f"；原因：{rep['reason']}"))
    if not rep["ok"] and notifier:
        from .ops import notify_backup_failed
        notify_backup_failed(notifier, rep["text"])
    return rep


def main(argv=None, settings=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in ("run", "drill") or (argv[0] == "run" and len(argv) != 1) or (argv[0] == "drill" and len(argv) != 2):
        print("用法：python -m app.backup run | drill <暫存目錄>", file=sys.stderr)
        return 2
    from .config import load_settings
    from .notify import make_notifier
    s = settings or load_settings()
    target, notifier = make_target(s), make_notifier(s)
    if argv[0] == "run":
        return 0 if run_backup(s, target, notifier=notifier) else 1
    rep = drill(s, target, argv[1], notifier=notifier)
    print(rep["text"])
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
