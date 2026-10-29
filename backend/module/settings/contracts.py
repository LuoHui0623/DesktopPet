from dataclasses import dataclass


@dataclass(frozen=True)
class AppSettingRow:
    key: str
    value: str
    updated_at: str


@dataclass(frozen=True)
class AppSettingRecord:
    key: str
    value: str
    updated_at: str
