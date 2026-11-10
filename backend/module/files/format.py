"""展示层格式化：相对路径、显示名、时间解析与永久记忆渲染。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, tzinfo
from pathlib import Path

from constants.files import WORLD
from module.files.resolver import FileError, Resource


def relative_path(path: Path, root: Path) -> str:
    """统一用 POSIX 分隔符，保证工具输出跨平台一致。"""
    return path.relative_to(root).as_posix()


def display(resource: Resource) -> str:
    """目录索引里的资源名：小世界整体展示为 resources。"""
    if resource.spec.id == WORLD:
        return "resources"
    assert resource.path is not None
    return resource.path.name


def file_display(resource: Resource) -> str:
    """单文件索引用相对路径，其余资源用文件名。"""
    assert resource.path is not None
    if resource.spec.id == WORLD:
        return relative_path(resource.path, resource.root)
    return resource.path.name


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def moment(value: str) -> datetime:
    """记忆时间戳按 UTC 解释，便于跨时区排序。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_moment(value: str, timezone: tzinfo) -> datetime:
    """解析查询时间；不带时区时按配置时区解释。"""
    try:
        if len(value) == 10:
            parsed = datetime.combine(date.fromisoformat(value), time.min)
        else:
            parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FileError(f"时间格式不合法：{value}；应为 YYYY-MM-DD 或 ISO 8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def day_text(day: str) -> str:
    try:
        return date.fromisoformat(day).isoformat()
    except ValueError as exc:
        raise FileError(f"日期格式不合法：{day}") from exc


def render_permanent(view, timezone: tzinfo) -> str:
    """把一条记忆视图渲染成单行，便于按行编号与检索。"""
    at = moment(view.created_at).astimezone(timezone)
    text = view.content.replace("\r\n", "\n").replace("\n", " ")
    if view.role == "event":
        text = f"事件：{text}"
    return f"[{at:%Y-%m-%d %H:%M:%S}][{view.role}]{text}"
