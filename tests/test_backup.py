import sqlite3
from datetime import date

from tridata import cli
from tridata.models import DailyStats
from tridata.storage import DataStore


def _store(tmp_path):
    store = DataStore(tmp_path / "data.sqlite3")
    store.save_daily_stats(DailyStats(stat_date=date(2026, 2, 1), steps=10000))
    return store


def test_backup_copies_and_verifies(tmp_path):
    store = _store(tmp_path)
    dest = tmp_path / "bk" / "copy.sqlite3"
    counts = store.backup(dest)
    assert counts["daily_stats"] == 1
    assert dest.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(dest) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_stats").fetchone()[0] == 1


def test_cmd_backup_copies_extras_and_rotates(tmp_path, monkeypatch):
    store = _store(tmp_path)
    root = tmp_path / "backups"
    for stamp in ("2026-01-01T000000", "2026-01-02T000000", "2026-01-03T000000"):
        (root / stamp).mkdir(parents=True)
    diary = tmp_path / "diary.md"
    diary.write_text("fictional diary")

    assert cli._cmd_backup(store, root, keep=2, extras=[diary]) == 0

    kept = sorted(p.name for p in root.iterdir())
    assert len(kept) == 2 and kept[0] == "2026-01-03T000000"
    newest = root / kept[1]
    assert (newest / "data.sqlite3").exists()
    assert (newest / "diary.md").read_text() == "fictional diary"
    assert root.stat().st_mode & 0o777 == 0o700


def test_cmd_backup_rejects_missing_extra(tmp_path):
    store = _store(tmp_path)
    assert cli._cmd_backup(store, tmp_path / "b", keep=2, extras=[tmp_path / "nope.md"]) == 1
    assert not (tmp_path / "b").exists()


def test_exports_do_not_need_garmin_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "DataStore", lambda: _store(tmp_path))
    assert cli.main(["export-metrics", "--out", str(tmp_path / "m.md")]) == 0
    assert cli.main(["sync"]) == 1
