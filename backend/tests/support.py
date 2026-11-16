"""测试辅助：设置文档（`data/settings.json`）与运行时台账的内存装配。

设置写入在 `T6` 之后只经 `SettingsCoordinator` 落盘，各域只读段快照。测试里用
同一份 `SettingsDocument` 实例模拟真实进程内共享，用 `reload_document` 模拟重启。
"""

from pathlib import Path

from app.settings.coordinator import SettingsCoordinator
from app.settings.document import SettingsDocument

_DOCUMENTS: dict[Path, SettingsDocument] = {}


def settings_path(db_or_dir: Path) -> Path:
    """接受 `pet.db` 文件路径或数据目录，统一得到 settings.json 路径。"""
    if db_or_dir.suffix:
        return db_or_dir.with_name("settings.json")
    return db_or_dir / "settings.json"


def settings_doc(db_or_dir: Path) -> SettingsDocument:
    """同一路径返回同一实例：等价于旧实现里多个服务共享一个数据库门面。"""
    path = settings_path(db_or_dir)
    document = _DOCUMENTS.get(path)
    if document is None:
        document = SettingsDocument(path)
        document.load()
        _DOCUMENTS[path] = document
    return document


def reload_document(db_or_dir: Path) -> SettingsDocument:
    """丢弃内存实例后重新从文件载入，用于验证重启后仍生效。"""
    _DOCUMENTS.pop(settings_path(db_or_dir), None)
    return settings_doc(db_or_dir)


def write_section(db_or_dir: Path, name: str, values: object) -> SettingsDocument:
    """直接写一段并落盘，用于准备初始状态（不经过域方法）。"""
    document = settings_doc(db_or_dir)
    document.replace(name, values)
    document.persist()
    return document


def coordinator_for(document: SettingsDocument, sources: dict | None = None) -> SettingsCoordinator:
    """构造协调器；测试只关心版本号与落盘时可不注册任何段来源。"""
    return SettingsCoordinator(document, sources or {})
