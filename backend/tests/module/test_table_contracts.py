from dataclasses import fields
from pathlib import Path

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.memory.contracts import DailyLogRecord, DailyLogRow
from module.settings.contracts import AppSettingRecord, AppSettingRow


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "pet.db")
    migrate(connection)
    yield connection
    connection.close()


def column_names(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_daily_log_row_matches_columns(conn):
    assert {field.name for field in fields(DailyLogRow)} == column_names(conn, "daily_log")


def test_daily_log_record_covers_every_writable_column(conn):
    generated = column_names(conn, "daily_log") - {field.name for field in fields(DailyLogRecord)}
    assert generated == {"id"}


def test_app_setting_row_matches_columns(conn):
    assert {field.name for field in fields(AppSettingRow)} == column_names(conn, "app_setting")


def test_app_setting_record_matches_columns(conn):
    assert {field.name for field in fields(AppSettingRecord)} == column_names(conn, "app_setting")
