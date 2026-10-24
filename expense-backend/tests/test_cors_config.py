import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.main import create_app

ORIGIN = "https://pwa.example.com"


def make(tmp_path, origins):
    s = Settings(my_company_tax_id="22099131", db_path=str(tmp_path / "c.db"),
                 storage_dir=str(tmp_path / "r"), cors_origins=origins)
    return TestClient(create_app(s))


def test_allowed_origin_gets_cors_header(tmp_path):
    c = make(tmp_path, [ORIGIN])
    r = c.get("/api/v1/health", headers={"Origin": ORIGIN})
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_other_origin_gets_no_cors_header(tmp_path):
    c = make(tmp_path, [ORIGIN])
    r = c.get("/api/v1/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_preflight_allowed_and_denied(tmp_path):
    c = make(tmp_path, [ORIGIN])
    h = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,authorization"}
    ok = c.options("/api/v1/submit", headers={**h, "Origin": ORIGIN})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == ORIGIN
    bad = c.options("/api/v1/submit", headers={**h, "Origin": "https://evil.example.com"})
    assert bad.status_code == 400


def test_default_no_origins_means_no_cors(tmp_path):
    r = make(tmp_path, []).get("/api/v1/health", headers={"Origin": ORIGIN})
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("bad", ["*", "https://*.example.com"])
def test_wildcard_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        make(tmp_path, [bad])


def test_load_settings_reads_company_tax_id_and_origins(tmp_path, monkeypatch):
    f = tmp_path / "s.yaml"
    f.write_text('company:\n  tax_id: "04541302"\ncors_allowed_origins:\n  - "https://pwa.example.com"\n', encoding="utf-8")
    monkeypatch.setenv("EXPENSE_CONFIG", str(f))
    s = load_settings()
    assert s.my_company_tax_id == "04541302" and s.cors_origins == ["https://pwa.example.com"]
