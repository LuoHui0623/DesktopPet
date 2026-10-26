from pathlib import Path

from infra.store.db import connect, transaction
from infra.store.repositories import daily_log
from infra.store.repositories.daily_log import DailyLogRecord


class DbDailyLog:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def append(self, date: str, created_at: str, entries: list[tuple[str, str]]) -> None:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                for role, content in entries:
                    daily_log.insert(conn, date, role, content, created_at)
        finally:
            conn.close()

    def by_date(self, date: str) -> list[DailyLogRecord]:
        conn = connect(self._db_file)
        try:
            return daily_log.by_date(conn, date)
        finally:
            conn.close()

    def dates(self) -> list[str]:
        conn = connect(self._db_file)
        try:
            return daily_log.dates(conn)
        finally:
            conn.close()

    def search(
        self,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        newest_first: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[DailyLogRecord]:
        conn = connect(self._db_file)
        try:
            return daily_log.search(
                conn,
                keyword,
                date_from,
                date_to,
                newest_first=newest_first,
                limit=limit,
                offset=offset,
            )
        finally:
            conn.close()
