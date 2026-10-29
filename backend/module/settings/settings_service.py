from datetime import datetime
from pathlib import Path

from module.settings.contracts import AppSettingRecord
from module.settings.repository import DbAppSettings


class SettingsService:
    def __init__(self, db_file: Path) -> None:
        self._rows = DbAppSettings(db_file)

    def get(self, key: str) -> str | None:
        row = self._rows.get(key)
        return None if row is None else row.value

    def put(self, key: str, value: str) -> None:
        updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._rows.put(AppSettingRecord(key=key, value=value, updated_at=updated_at))
