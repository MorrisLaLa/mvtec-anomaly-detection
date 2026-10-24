from app import classify
from tests.conftest import auth


def conn(client):
    return client.app.state.db_factory()


def test_suggest_hit_and_miss(client):
    c = conn(client)
    hit = classify.suggest_category("大都會計程車", conn=c)
    assert hit["category"] == "交通" and hit["confidence"] > 0
    miss = classify.suggest_category("神秘店", conn=c)
    assert miss["category"] is None and miss["confidence"] == 0


def test_longest_keyword_wins(client):
    c = conn(client)
    c.execute("INSERT INTO classify_rules(keyword,category,confidence) VALUES('大都會計程車','差旅',0.9)")
    c.commit()
    assert classify.suggest_category("大都會計程車", conn=c)["category"] == "差旅"


def test_two_corrections_no_suggestion(client, keys):
    c = conn(client)
    for _ in range(2):
        classify.record_correction("神秘店", None, "餐飲", conn=c)
    assert classify.pending_rule_suggestions(conn=c) == []
    assert client.get("/api/v1/pending-rule-suggestions", headers=auth(keys["reviewer"])).json() == []


def test_three_corrections_trigger_suggestion_not_auto_written(client, keys):
    c = conn(client)
    for _ in range(3):
        classify.record_correction("神秘店", None, "餐飲", conn=c)
    s = client.get("/api/v1/pending-rule-suggestions", headers=auth(keys["reviewer"])).json()
    assert s == [{"store": "神秘店", "category": "餐飲", "count": 3}]
    # 未經人工確認不得寫入規則
    assert classify.suggest_category("神秘店", conn=c)["category"] is None


def test_mixed_target_categories_no_suggestion(client):
    c = conn(client)
    for to in ["餐飲", "餐飲", "交通"]:
        classify.record_correction("神秘店", None, to, conn=c)
    assert classify.pending_rule_suggestions(conn=c) == []


def test_confirm_writes_rule_and_takes_effect(client, keys):
    c = conn(client)
    for _ in range(3):
        classify.record_correction("神秘店", None, "餐飲", conn=c)
    r = client.post("/api/v1/pending-rule-suggestions/confirm", json={"store": "神秘店", "category": "餐飲"}, headers=auth(keys["reviewer"]))
    assert r.status_code == 200
    assert classify.suggest_category("神秘店", conn=c)["category"] == "餐飲"
    assert any(x["keyword"] == "神秘店" for x in client.get("/api/v1/rules").json())
    assert client.get("/api/v1/pending-rule-suggestions", headers=auth(keys["reviewer"])).json() == []


def test_confirm_without_pending_rejected(client, keys):
    r = client.post("/api/v1/pending-rule-suggestions/confirm", json={"store": "沒這家", "category": "餐飲"}, headers=auth(keys["reviewer"]))
    assert r.status_code == 409


def test_corrections_endpoint(client):
    r = client.post("/api/v1/corrections", json={"store": "A", "from_category": "x", "to_category": "y"})
    assert r.status_code == 200
    assert client.app.state.db_factory().execute("SELECT COUNT(*) FROM classify_corrections").fetchone()[0] == 1


def test_noop_correction_ignored(client):
    c = conn(client)
    classify.record_correction("A", "x", "x", conn=c)
    assert c.execute("SELECT COUNT(*) FROM classify_corrections").fetchone()[0] == 0
