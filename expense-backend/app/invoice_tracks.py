"""統一發票字軌比對（降低偽造 QR 的風險，見 docs/security-review.md S1）。
財政部每兩個月一期（1–2 月、3–4 月…）公告可用字軌；字軌資料一律來自官方公告，不得自行編造。
期別以西元「YYYY-MM」表示，MM 為該期的第一個月（奇數月）。"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_NO = re.compile(r"[A-Z]{2}[0-9]{8}")
_TRACK = re.compile(r"[A-Z]{2}")
_PERIOD = re.compile(r"[0-9]{4}-(01|03|05|07|09|11)")


def period_of(iso_date: str) -> str:
    y, m = int(iso_date[:4]), int(iso_date[5:7])
    return f"{y:04d}-{m - (m + 1) % 2:02d}"


def load_tracks(path: str) -> dict[str, set[str]]:
    """讀字軌檔；檔案不存在＝沒有資料（回傳空 dict）；格式不對直接報錯，不默默忽略。"""
    p = Path(path)
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    periods = raw.get("periods") or {}
    if not isinstance(periods, dict):
        raise ValueError("字軌檔的 periods 必須是 {期別: [字軌, …]}")
    out: dict[str, set[str]] = {}
    for period, tracks in periods.items():
        if not isinstance(period, str) or not _PERIOD.fullmatch(period):
            raise ValueError(f"期別格式不對（需為 YYYY-奇數月）：{period!r}")
        if not isinstance(tracks, list) or not all(isinstance(t, str) and _TRACK.fullmatch(t) for t in tracks):
            raise ValueError(f"{period} 的字軌必須是兩個大寫英文字母的清單")
        out[period] = set(tracks)
    return out


def check_track(invoice_no, invoice_date: str, tracks: dict[str, set[str]]) -> str:
    """回傳 ok（字軌屬於該期）、mismatch（不屬於或號碼格式不對）、unknown（該期沒有公告資料）。"""
    if not isinstance(invoice_no, str) or not _NO.fullmatch(invoice_no):
        return "mismatch"
    allowed = tracks.get(period_of(invoice_date))
    if allowed is None:
        return "unknown"
    return "ok" if invoice_no[:2] in allowed else "mismatch"
