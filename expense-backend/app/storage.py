import re
from abc import ABC, abstractmethod
from pathlib import Path

_SAFE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def is_safe_name(name: str) -> bool:
    return bool(_SAFE.fullmatch(name))


class StorageBackend(ABC):
    @abstractmethod
    def save(self, case_no: str, kind: str, data: bytes, suffix: str = ".png", namespace: str | None = None) -> str:
        """儲存檔案並回傳其識別路徑。namespace 用來隔離不同裝置的相同 client_id。"""


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: str):
        self.base = Path(base_dir)

    def save(self, case_no: str, kind: str, data: bytes, suffix: str = ".png", namespace: str | None = None) -> str:
        if (not is_safe_name(case_no) or not is_safe_name(kind) or not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", suffix)
                or (namespace is not None and not is_safe_name(namespace))):
            raise ValueError("unsafe storage name")
        d = (self.base / namespace / case_no) if namespace else (self.base / case_no)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{kind}{suffix}"
        p.write_bytes(data)
        return str(p)


    def delete(self, path: str) -> bool:
        """刪除儲存區內的檔案（路徑必須在儲存根目錄之下），並清掉空目錄。"""
        p = Path(path).resolve()
        if self.base.resolve() not in p.parents:
            raise ValueError("path outside storage")
        if not p.exists():
            return False
        p.unlink()
        for parent in (p.parent, p.parent.parent):
            try:
                if parent != self.base.resolve():
                    parent.rmdir()
            except OSError:
                break
        return True
