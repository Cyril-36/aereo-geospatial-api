"""Schema migrations, tested against a database written by the real first release."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app import migrations
from app.database import Base, make_engine
from app.migrations import LATEST, V1_COLUMNS, V2_COLUMNS, V3_COLUMNS, MigrationError, migrate

PHASE1_DUMP = Path(__file__).parent / "fixtures" / "phase1_database.sql"


def phase1_database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(PHASE1_DUMP.read_text())
    connection.close()
    return path


def rows(path: Path, table: str, columns) -> list[tuple]:
    connection = sqlite3.connect(path)
    cols = ", ".join(f'"{c}"' for c in sorted(columns))
    result = connection.execute(f"SELECT {cols} FROM {table} ORDER BY id").fetchall()  # noqa: S608
    connection.close()
    return result


def columns(engine, table: str) -> dict[str, str]:
    return {c["name"]: str(c["type"]) for c in inspect(engine).get_columns(table)}


def test_fresh_database_gets_current_schema_and_all_versions(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'new.db'}")
    assert migrate(engine) == [1, 2, 3]
    assert migrations.applied_versions(engine) == [1, 2, 3]
    for table in ("files", "features"):
        assert set(columns(engine, table)) == set(Base.metadata.tables[table].c.keys())


def test_phase1_database_is_upgraded_without_losing_data(tmp_path):
    db = phase1_database(tmp_path / "phase1.db")
    before_files = rows(db, "files", V1_COLUMNS["files"])
    before_features = rows(db, "features", V1_COLUMNS["features"])
    assert len(before_files) == 4 and len(before_features) == 7  # the fixture's content

    engine = make_engine(f"sqlite:///{db}")
    assert migrate(engine) == [1, 2, 3]
    engine.dispose()

    # Every original value is unchanged, including statuses: EXTRACTED stays EXTRACTED.
    assert rows(db, "files", V1_COLUMNS["files"]) == before_files
    assert rows(db, "features", V1_COLUMNS["features"]) == before_features
    statuses = [r[0] for r in sqlite3.connect(db).execute("SELECT status FROM files")]
    assert sorted(statuses) == ["EXTRACTED", "EXTRACTED", "EXTRACTED", "FAILED"]
    # New columns exist and are NULL for legacy rows.
    for table, names in V2_COLUMNS.items():
        values = rows(db, table, names)
        assert values and all(v is None for row in values for v in row)


def test_upgraded_schema_matches_a_fresh_schema(tmp_path):
    upgraded = make_engine(f"sqlite:///{phase1_database(tmp_path / 'old.db')}")
    fresh = make_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    migrate(upgraded)
    migrate(fresh)
    for table in ("files", "features"):
        assert columns(upgraded, table) == columns(fresh, table)


def test_migration_is_idempotent(tmp_path):
    engine = make_engine(f"sqlite:///{phase1_database(tmp_path / 'p1.db')}")
    migrate(engine)
    assert migrate(engine) == []
    assert migrations.applied_versions(engine) == [1, 2, 3]
    assert LATEST == 3


def test_failed_migration_leaves_the_database_unchanged(tmp_path, monkeypatch):
    db = phase1_database(tmp_path / "p1.db")
    engine = make_engine(f"sqlite:///{db}")
    real_add = migrations._add_columns

    def add_first_then_fail(conn, cols):
        real_add(conn, {"files": cols["files"][:1]})  # one ALTER succeeds ...
        raise RuntimeError("simulated failure during migration")  # ... then it fails

    monkeypatch.setattr(migrations, "_add_columns", add_first_then_fail)
    with pytest.raises(RuntimeError):
        migrate(engine)
    # The ALTER was rolled back with the version row: still exactly schema version 1.
    assert set(columns(engine, "files")) == V1_COLUMNS["files"]
    assert migrations.applied_versions(engine) == [1]

    monkeypatch.setattr(migrations, "_add_columns", real_add)
    assert migrate(engine) == [2, 3]
    assert set(columns(engine, "files")) == V1_COLUMNS["files"] | set(V2_COLUMNS["files"])


def test_unrecognised_unversioned_schema_is_refused(tmp_path):
    db = tmp_path / "other.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE files (id TEXT PRIMARY KEY, something_else TEXT)")
    connection.execute("CREATE TABLE features (id INTEGER PRIMARY KEY)")
    connection.close()
    engine = make_engine(f"sqlite:///{db}")
    with pytest.raises(MigrationError, match="does not match schema version 1"):
        migrate(engine)
    assert set(columns(engine, "files")) == {"id", "something_else"}  # untouched


def test_v3_backfills_search_columns(tmp_path):
    db = phase1_database(tmp_path / "p1.db")
    engine = make_engine(f"sqlite:///{db}")
    migrate(engine)
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            'SELECT "index", source_id, display_name, sort_name, search_text, warning_count '
            "FROM features"
        ).fetchall()
    assert len(rows) == 7
    for _, _, name, sort_name, text, warnings in rows:
        assert name and sort_name == sort_name.casefold() and name.casefold() in text
        assert warnings >= 0
    assert set(V3_COLUMNS["features"]) <= set(columns(engine, "features"))


def test_v2_database_upgrades_to_v3(tmp_path):
    db = phase1_database(tmp_path / "p1.db")
    engine = make_engine(f"sqlite:///{db}")
    real = migrations.MIGRATIONS
    migrations.MIGRATIONS = real[:2]
    try:
        assert migrate(engine) == [1, 2]
    finally:
        migrations.MIGRATIONS = real
    assert migrate(engine) == [3]
    assert migrations.applied_versions(engine) == [1, 2, 3]
