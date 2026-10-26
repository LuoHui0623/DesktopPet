from collections.abc import Sequence
from pathlib import Path

from infra.store.db import connect, transaction
from infra.store.repositories import core_memory


class DbCoreMemory:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def ids(self) -> list[str]:
        conn = connect(self._db_file)
        try:
            return core_memory.ids(conn)
        finally:
            conn.close()

    def insert(self, memory_id: str, sort_order: int) -> None:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                core_memory.insert(conn, memory_id, sort_order)
        finally:
            conn.close()

    def remove(self, memory_id: str) -> bool:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                return core_memory.remove(conn, memory_id)
        finally:
            conn.close()

    def rewrite(self, ordered_ids: Sequence[str]) -> None:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                core_memory.rewrite(conn, ordered_ids)
        finally:
            conn.close()
