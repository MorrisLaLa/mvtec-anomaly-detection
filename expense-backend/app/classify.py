"""分類建議與修正回饋。規則只能經人工確認才會寫入 classify_rules。"""
from . import database

CORRECTION_THRESHOLD = 3


def _conn(conn):
    return conn or database.default_connection()


def suggest_category(store: str, conn=None) -> dict:
    c = _conn(conn)
    if not store:
        return {"category": None, "confidence": 0.0, "keyword": None}
    best = None
    for r in c.execute("SELECT keyword, category, confidence FROM classify_rules"):
        if r["keyword"].lower() in store.lower() and (best is None or len(r["keyword"]) > len(best["keyword"])):
            best = r
    if best is None:
        return {"category": None, "confidence": 0.0, "keyword": None}
    return {"category": best["category"], "confidence": best["confidence"], "keyword": best["keyword"]}


def record_correction(store: str, from_category, to_category: str, conn=None) -> bool:
    if not store or not to_category or from_category == to_category:
        return False
    c = _conn(conn)
    c.execute(
        "INSERT INTO classify_corrections(store, from_category, to_category) VALUES(?,?,?)",
        (store, from_category, to_category),
    )
    c.commit()
    return True


def pending_rule_suggestions(conn=None) -> list[dict]:
    """同店家修正 >=3 次且全部改成同一分類，且現行規則尚未給出該分類 → 建議（待人工確認）。"""
    c = _conn(conn)
    rows = c.execute(
        """SELECT store, MIN(to_category) AS category, COUNT(*) AS count
           FROM classify_corrections GROUP BY store
           HAVING COUNT(DISTINCT to_category) = 1 AND COUNT(*) >= ?
           ORDER BY store""",
        (CORRECTION_THRESHOLD,),
    ).fetchall()
    out = []
    for r in rows:
        if suggest_category(r["store"], conn=c)["category"] == r["category"]:
            continue
        out.append({"store": r["store"], "category": r["category"], "count": r["count"]})
    return out


def confirm_rule(store: str, category: str, conn=None) -> bool:
    """人工確認：僅當 (store, category) 正在待確認清單時才寫入規則。"""
    c = _conn(conn)
    if not any(s["store"] == store and s["category"] == category for s in pending_rule_suggestions(conn=c)):
        return False
    c.execute(
        """INSERT INTO classify_rules(keyword, category, confidence) VALUES(?,?,0.95)
           ON CONFLICT(keyword) DO UPDATE SET category=excluded.category, confidence=excluded.confidence""",
        (store, category),
    )
    c.commit()
    return True
