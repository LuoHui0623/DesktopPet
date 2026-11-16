"""`data/settings.json` 的所有者：载入、校验、合并、持久化与版本号。

设计要点（`D12`）：
- 真源是各域的领域状态，本文件是持久化与统一读源；写入永远先改领域状态再由协调器投射。
- 初始化三种情况：文件缺失 → 默认值建文件（并吸收旧库配置）；解析失败 → 备份
  `settings.json.corrupt-{时间戳}` 后以默认值重建；段或字段缺失 → 补默认值。
- 未知段与段内未知字段丢弃并记 warning，不阻断启动。
- `scheduler.sent` 等运行时台账不进快照，保留在 `runtime_state` 表。
"""

import json
import logging
import sqlite3
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from app.settings.sections import SECTIONS, defaults, is_section, normalize
from infra.store.db import connect, transaction
from infra.store.kv import TABLE as RUNTIME_TABLE

logger = logging.getLogger("pet.settings")

CORRUPT_SUFFIX = "corrupt"
# 旧库配置键：`T6` 前存在 `app_setting` 表；迁移后这些键整体搬入快照并删除。
LEGACY_MEMORY_KEY = "memory.temporary_mode"
LEGACY_AVATAR_PREFIX = "avatar.active."
LEGACY_LLM_PREFIX = "llm.active."


def _legacy_bool(raw: str) -> object:
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def _legacy_int(raw: str) -> object:
    return int(raw.strip())


def _legacy_names(raw: str) -> object:
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, list) else []


def _legacy_text(raw: str) -> object:
    return raw.strip()


def _legacy_object(raw: str) -> object:
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


class _LegacyField:
    """旧键 → 段字段的读写口径；`parse` 返回 None 表示该值不可迁移。"""

    def __init__(self, section: str, name: str, parse: Callable[[str], object]) -> None:
        self.section = section
        self.name = name
        self._parse = parse

    def parse(self, raw: str) -> object | None:
        try:
            return self._parse(raw)
        except (TypeError, ValueError):
            return None


# 十七个旧配置键逐项登记，便于升级时逐一断言类型（`R7`）。
LEGACY_FIELDS: dict[str, _LegacyField] = {
    "perception.enabled": _LegacyField("perception", "enabled", _legacy_bool),
    "perception.foreground_enabled": _LegacyField("perception", "foreground_enabled", _legacy_bool),
    "perception.idle_away_minutes": _LegacyField("perception", "idle_away_minutes", _legacy_int),
    "perception.sit_minutes": _LegacyField("perception", "sit_minutes", _legacy_int),
    "perception.process_whitelist": _LegacyField("perception", "process_whitelist", _legacy_names),
    "perception.process_muted": _LegacyField("perception", "process_muted", _legacy_names),
    "scheduler.enabled": _LegacyField("scheduler", "enabled", _legacy_bool),
    "scheduler.min_interval_minutes": _LegacyField(
        "scheduler", "min_interval_minutes", _legacy_int
    ),
    "scheduler.max_per_day": _LegacyField("scheduler", "max_per_day", _legacy_int),
    "scheduler.dnd_start": _LegacyField("scheduler", "dnd_start", _legacy_text),
    "scheduler.dnd_end": _LegacyField("scheduler", "dnd_end", _legacy_text),
    "persona.active": _LegacyField("persona", "active", _legacy_text),
    "interaction.config": _LegacyField("interaction", "config", _legacy_object),
}


def _is_legacy_key(key: str) -> bool:
    return (
        key in LEGACY_FIELDS
        or key == LEGACY_MEMORY_KEY
        or key.startswith(LEGACY_AVATAR_PREFIX)
        or key.startswith(LEGACY_LLM_PREFIX)
    )


def import_legacy(db_file: Path) -> dict[str, str]:
    """取出并删除 `runtime_state` 里的旧配置键（幂等；无表时返回空）。

    读取与删除在同一事务内完成：配置只可能被搬进快照一次，重启不会重复导入。
    """
    try:
        conn = connect(db_file)
    except sqlite3.Error as exc:
        logger.warning("读取旧配置失败，跳过导入：%s", exc)
        return {}
    try:
        rows = conn.execute(f"SELECT key, value FROM {RUNTIME_TABLE}").fetchall()
        legacy = {row[0]: row[1] for row in rows if _is_legacy_key(row[0])}
        if not legacy:
            return {}
        with transaction(conn):
            conn.executemany(
                f"DELETE FROM {RUNTIME_TABLE} WHERE key = ?", [(key,) for key in legacy]
            )
        return legacy
    except sqlite3.Error as exc:
        logger.warning("迁移旧配置失败，本次以默认值启动：%s", exc)
        return {}
    finally:
        conn.close()


class SettingsDocument:
    """快照文档：内存态 + 原子落盘；版本号供 `If-Match` 乐观锁使用。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._version = 0
        self._updated_at = ""
        self._sections: dict[str, dict[str, object]] = defaults()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def version(self) -> int:
        return self._version

    def section(self, name: str) -> Mapping[str, object]:
        """段快照（只读副本）；未知段返回空映射。"""
        return deepcopy(self._sections.get(name, {}))

    def snapshot(self) -> dict[str, object]:
        return {
            "version": self._version,
            "updated_at": self._updated_at,
            **{name: deepcopy(self._sections[name]) for name in SECTIONS},
        }

    def load(self, legacy: Mapping[str, str] | None = None) -> None:
        """载入快照；文件缺失时吸收旧库配置并创建。"""
        if self._path.exists():
            self._load_existing()
            return
        self._sections = defaults()
        if legacy:
            self._apply_legacy(legacy)
        self.persist()

    def replace(self, name: str, values: object) -> bool:
        """归一化后替换整段；未变化返回 False。"""
        if not is_section(name):
            raise KeyError(name)
        normalized = normalize(name, values)
        if normalized == self._sections[name]:
            return False
        self._sections[name] = normalized
        return True

    def persist(self) -> None:
        """版本号加一后原子写回；写文件是配置落盘的唯一出口。"""
        self._version += 1
        self._updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        body: dict[str, object] = {
            "version": self._version,
            "updated_at": self._updated_at,
            **{name: self._sections[name] for name in SECTIONS},
        }
        text = json.dumps(body, ensure_ascii=False, indent=2) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self._path)

    def _load_existing(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("快照根节点必须是对象")
        except (OSError, ValueError) as exc:
            self._rebuild(f"读取失败：{exc}")
            return
        version = raw.get("version")
        self._version = (
            version
            if isinstance(version, int) and not isinstance(version, bool) and version >= 0
            else 0
        )
        updated_at = raw.get("updated_at")
        self._updated_at = updated_at if isinstance(updated_at, str) else ""
        unknown = [
            key for key in raw if key not in SECTIONS and key not in {"version", "updated_at"}
        ]
        if unknown:
            logger.warning("设置快照含未知段，已丢弃：%s", "、".join(sorted(unknown)))
        self._sections = {name: normalize(name, raw.get(name)) for name in SECTIONS}
        expected = {
            "version": self._version,
            "updated_at": self._updated_at,
            **self._sections,
        }
        if raw != expected:
            logger.warning("设置快照与当前 schema 不一致，已补默认值并丢弃未知字段")
            self.persist()

    def _rebuild(self, reason: str) -> None:
        self._backup()
        logger.warning("设置快照%s，已备份并重建默认值", reason)
        self._sections = defaults()
        self._version = 0
        self._updated_at = ""
        self.persist()

    def _backup(self) -> None:
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
        target = self._path.with_name(f"{self._path.name}.{CORRUPT_SUFFIX}-{stamp}")
        try:
            self._path.replace(target)
        except OSError as exc:
            logger.warning("设置快照备份失败：%s", exc)

    def _apply_legacy(self, legacy: Mapping[str, str]) -> None:
        partial: dict[str, dict[str, object]] = {}
        for key, raw in legacy.items():
            if key == LEGACY_MEMORY_KEY:
                # 临时模式已无读取方（`T6`），迁移时直接丢弃。
                logger.info("丢弃已废弃的配置键：%s", key)
                continue
            if key.startswith(LEGACY_AVATAR_PREFIX):
                persona = key[len(LEGACY_AVATAR_PREFIX) :].strip()
                if not persona:
                    continue
                active = partial.setdefault("avatar", {}).setdefault("active", {})
                if isinstance(active, dict):
                    active[persona] = raw
                continue
            if key.startswith(LEGACY_LLM_PREFIX):
                capability = key[len(LEGACY_LLM_PREFIX) :].strip()
                provider, _, model = raw.partition("/")
                if capability and provider and model:
                    selected = partial.setdefault("llm", {}).setdefault("active", {})
                    if isinstance(selected, dict):
                        selected[capability] = {"provider": provider, "model": model}
                continue
            field = LEGACY_FIELDS.get(key)
            if field is None:
                logger.warning("未知的旧配置键，已丢弃：%s", key)
                continue
            parsed = field.parse(raw)
            if parsed is None:
                logger.warning("旧配置键取值不可迁移，已回落默认值：%s", key)
                continue
            if field.section == "interaction":
                # `interaction.config` 整段是一个 JSON 对象，按段合并而不是按字段。
                section = partial.setdefault("interaction", {})
                if isinstance(parsed, dict):
                    section.update(parsed)
            else:
                partial.setdefault(field.section, {})[field.name] = parsed
        for name, values in partial.items():
            self._sections[name] = normalize(name, values)
