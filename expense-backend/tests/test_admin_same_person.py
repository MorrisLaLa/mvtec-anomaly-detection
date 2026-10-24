"""S3（選項 2）：自審判斷靠裝置名稱。建立裝置時，若與「其他角色」的有效裝置名稱相近，要求管理員明確確認不是同一人；
建立主管／管理員裝置時列出現有申報人，方便核對。"""
from app import admin, database


def _db(tmp_path, *devices):
    db = str(tmp_path / "a.db")
    database.init_db(db)
    c = database.connect(db)
    for name, role in devices:
        admin.create_device(c, name, role)
    return db, c


def _count(c, name):
    return c.execute("SELECT COUNT(*) FROM devices WHERE name=?", (name,)).fetchone()[0]


def test_similar_names_only_across_roles_and_active(tmp_path):
    db, c = _db(tmp_path, ("王志明", "submitter"), ("李大華", "submitter"), ("陳主管", "reviewer"))
    admin.revoke(c, "李大華")
    assert admin.similar_names(c, "志明主任", "reviewer") == ["王志明"]
    assert admin.similar_names(c, "大華主任", "reviewer") == []  # 已撤銷的不算
    assert admin.similar_names(c, "王志明2", "submitter") == []  # 同角色不會自審
    assert admin.similar_names(c, "陳主管", "submitter") == ["陳主管"]
    assert admin.similar_names(c, "Wang Ming", "reviewer") == []
    assert admin.similar_names(c, "wang", "reviewer") == [] 


def test_latin_names_compared_case_insensitively(tmp_path):
    db, c = _db(tmp_path, ("Wang", "submitter"))
    assert admin.similar_names(c, "WANG boss", "reviewer") == ["Wang"]


def test_cli_refuses_similar_name_without_confirmation(tmp_path, capsys):
    db, c = _db(tmp_path, ("王志明", "submitter"))
    assert admin.main(["--db", db, "create-device", "--name", "志明主任", "--role", "reviewer"]) == 2
    err = capsys.readouterr().err
    assert "王志明" in err and "--confirm-different-person" in err
    assert _count(c, "志明主任") == 0


def test_cli_creates_after_explicit_confirmation(tmp_path, capsys):
    db, c = _db(tmp_path, ("王志明", "submitter"))
    assert admin.main(["--db", db, "create-device", "--name", "志明主任", "--role", "reviewer",
                       "--confirm-different-person"]) == 0
    assert _count(c, "志明主任") == 1


def test_cli_lists_existing_submitters_when_creating_reviewer(tmp_path, capsys):
    db, c = _db(tmp_path, ("王志明", "submitter"), ("李大華", "submitter"))
    assert admin.main(["--db", db, "create-device", "--name", "陳主管", "--role", "reviewer"]) == 0
    out = capsys.readouterr().out
    assert "王志明" in out and "李大華" in out and "同一人" in out and "也能申報" in out


def test_cli_submitter_similar_to_existing_reviewer_also_refused(tmp_path, capsys):
    db, c = _db(tmp_path, ("陳主管", "reviewer"))
    assert admin.main(["--db", db, "create-device", "--name", "陳主管手機", "--role", "submitter"]) == 2
    assert _count(c, "陳主管手機") == 0


def test_short_latin_overlap_is_not_similar(tmp_path):
    db, c = _db(tmp_path, ("Dan", "submitter"))
    assert admin.similar_names(c, "Wang", "reviewer") == []  # 只共用 "an"
