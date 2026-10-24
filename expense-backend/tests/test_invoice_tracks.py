"""S1 降低風險：統一發票字軌須屬於發票日期那一期（財政部每兩個月一期公告）。
本檔只測讀取與比對；字軌資料一律來自財政部公告，測試用的字軌是虛構的。"""
import pytest

from app.invoice_tracks import check_track, load_tracks, period_of


@pytest.mark.parametrize("iso,period", [("2026-01-05", "2026-01"), ("2026-02-28", "2026-01"), ("2026-09-01", "2026-09"),
                                        ("2026-10-31", "2026-09"), ("2026-12-31", "2026-11"), ("2027-01-01", "2027-01")])
def test_period_is_two_months_starting_on_odd_month(iso, period):
    assert period_of(iso) == period


TRACKS = {"2026-09": {"ZZ", "ZY"}, "2026-11": {"ZX"}}


def test_track_in_period_ok():
    assert check_track("ZZ12345678", "2026-10-15", TRACKS) == "ok"


def test_track_from_another_period_is_mismatch():
    assert check_track("ZX12345678", "2026-10-15", TRACKS) == "mismatch"


@pytest.mark.parametrize("no", ["zz12345678", "Z112345678", "ZZ1234567", "ZZ123456789", "", None])
def test_malformed_invoice_no_is_mismatch(no):
    assert check_track(no, "2026-10-15", TRACKS) == "mismatch"


def test_period_without_published_tracks_is_unknown():
    assert check_track("ZZ12345678", "2027-03-01", TRACKS) == "unknown"
    assert check_track("ZZ12345678", "2026-10-15", {}) == "unknown"


def test_load_tracks_file(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("source: 測試\nperiods:\n  '2026-09': [ZZ, ZY]\n  '2026-11': [ZX]\n", encoding="utf-8")
    assert load_tracks(str(p)) == TRACKS


def test_missing_file_means_no_data(tmp_path):
    assert load_tracks(str(tmp_path / "none.yaml")) == {}


@pytest.mark.parametrize("body", [
    "periods:\n  '2026-10': [ZZ]\n",      # 期別必須從奇數月開始
    "periods:\n  '2026-9': [ZZ]\n",
    "periods:\n  '2026-09': [Zz]\n",      # 字軌必須是兩個大寫英文字母
    "periods:\n  '2026-09': [ZZZ]\n",
    "periods:\n  '2026-09': ZZ\n",        # 必須是清單
    "periods: [1, 2]\n",
])
def test_malformed_track_file_is_refused(tmp_path, body):
    p = tmp_path / "t.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        load_tracks(str(p))
