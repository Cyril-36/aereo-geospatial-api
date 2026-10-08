"""Versioned, forward-only, non-destructive schema migrations.

``Base.metadata.create_all`` never alters existing tables, so a database created by an earlier
release would silently lack new columns. Instead:

- a fresh database gets the current schema and every version is recorded as applied;
- an unversioned database whose tables match the first release's schema is recorded as
  version 1, then upgraded;
- each pending version runs in one transaction with its version row, so a failure leaves
  the database exactly as it was.

Migrations only add things; no row or column is ever dropped or rewritten. Legacy data policy:
files stored by the extraction-only first release keep status EXTRACTED and NULL measurement
fields. They are not relabelled COMPLETED, because they were never measured; re-uploading the
original file measures it.

Run ``python -m app.migrations`` to upgrade explicitly; the application also upgrades on start.
"""

import datetime as dt
import logging
from collections.abc import Callable

from sqlalchemy import Connection, Engine, inspect, text

from app.database import Base
from app.models import FeatureRecord, FileRecord  # noqa: F401  (registers the tables)

logger = logging.getLogger("aereo")
VERSION_TABLE = "schema_migrations"
_CREATE_VERSIONS = (
    "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
    "description TEXT NOT NULL, applied_at TEXT NOT NULL)"
)
_SELECT_VERSIONS = "SELECT version FROM schema_migrations"
_INSERT_VERSION = (
    "INSERT INTO schema_migrations (version, description, applied_at) VALUES (:v, :d, :t)"
)

# Columns of the first release (schema version 1), used to recognise an unversioned database.
V1_COLUMNS = {
    "files": {
        "id", "original_filename", "format", "storage_path", "size_bytes", "sha256", "status",
        "crs_status", "crs", "crs_wkt", "crs_origin", "feature_count", "warnings",
        "error_code", "error_message", "created_at", "processed_at",
    },
    "features": {
        "id", "file_id", "index", "source_id", "geometry_type", "source_geometry",
        "source_crs", "properties", "folder_path", "issue_code", "issue_detail", "warnings",
    },
}  # fmt: skip

# Columns added by version 2 (measurement results). All are nullable: existing rows keep NULL.
V2_COLUMNS = {
    "files": ["source_crs_input", "transformation", "status_counts"],
    "features": [
        "status", "reason_code", "reason", "area_m2", "length_m", "measurement_method",
        "measurement_crs", "geodesic_area_m2", "geodesic_length_m", "relative_difference",
        "wgs84_geometry", "transformation", "generated_vertices",
    ],
}  # fmt: skip


class MigrationError(RuntimeError):
    pass


def _add_columns(conn: Connection, columns: dict[str, list[str]]) -> None:
    for table, names in columns.items():
        existing = {c["name"] for c in inspect(conn).get_columns(table)}
        for name in names:
            if name in existing:
                raise MigrationError(f"{table}.{name} already exists; refusing to guess state")
            column = Base.metadata.tables[table].c[name]
            if not column.nullable:
                raise MigrationError(f"{table}.{name} must be nullable to add to existing rows")
            ddl_type = column.type.compile(dialect=conn.dialect)
            conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN "{name}" {ddl_type}')


def _v2_measurements(conn: Connection) -> None:
    _add_columns(conn, V2_COLUMNS)


MIGRATIONS: list[tuple[int, str, Callable[[Connection], None] | None]] = [
    (1, "files and features (extraction)", None),  # baseline, created by the first release
    (2, "feature measurement results", _v2_measurements),
]
LATEST = MIGRATIONS[-1][0]


def _record(conn: Connection, version: int, description: str) -> None:
    conn.execute(
        text(_INSERT_VERSION),
        {"v": version, "d": description, "t": dt.datetime.now(dt.UTC).isoformat()},
    )


def applied_versions(engine: Engine) -> list[int]:
    with engine.connect() as conn:
        if VERSION_TABLE not in inspect(conn).get_table_names():
            return []
        return [row[0] for row in conn.execute(text(_SELECT_VERSIONS))]


def migrate(engine: Engine) -> list[int]:
    """Bring the database to the latest version. Returns the versions applied by this call."""
    applied_now: list[int] = []
    with engine.begin() as conn:
        tables = set(inspect(conn).get_table_names())
        if VERSION_TABLE not in tables:
            conn.exec_driver_sql(_CREATE_VERSIONS)
        done = {row[0] for row in conn.execute(text(_SELECT_VERSIONS))}

        if not done and not tables & set(V1_COLUMNS):
            # Fresh database: create the current schema directly.
            Base.metadata.create_all(conn)
            for version, description, _ in MIGRATIONS:
                _record(conn, version, description)
            return [version for version, _, _ in MIGRATIONS]

        if not done:
            # Unversioned database from the first release: verify before adopting it.
            for table, expected in V1_COLUMNS.items():
                found = {c["name"] for c in inspect(conn).get_columns(table)}
                if found != expected:
                    raise MigrationError(
                        f"Unversioned table '{table}' does not match schema version 1 "
                        f"(missing {sorted(expected - found)}, unexpected "
                        f"{sorted(found - expected)}); not migrating."
                    )
            _record(conn, 1, MIGRATIONS[0][1])
            done.add(1)
            applied_now.append(1)

    for version, description, upgrade in MIGRATIONS:
        if version in done:
            continue
        with engine.begin() as conn:  # the change and its version row commit together
            if upgrade is not None:
                upgrade(conn)
            _record(conn, version, description)
        logger.info("database migrated to schema version %d (%s)", version, description)
        applied_now.append(version)
    return applied_now


if __name__ == "__main__":
    from app.config import get_settings
    from app.database import make_engine

    applied = migrate(make_engine(get_settings().resolved_database_url))
    print(f"applied: {applied or 'nothing (already at version ' + str(LATEST) + ')'}")
