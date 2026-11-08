"""任务身份上下文：后端填充的人设、任务、来源与权限快照。

工具签名不接收身份参数。派发层在子代理进入 ReAct 循环前用 contextvar 建立作用域，
文件访问和审批在同一调用栈内读取这份快照；线程池中的每个子任务各自建立作用域，
并行派发不会互相串用人设或权限。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from module.files.permissions import TaskPermissions

SOURCE_USER = "user"
SOURCE_SYSTEM_AUTO = "system_auto"
SOURCES = frozenset({SOURCE_USER, SOURCE_SYSTEM_AUTO})


class TaskContextError(Exception):
    code = "task_context_missing"


@dataclass(frozen=True)
class TaskContext:
    persona_id: str
    persona_version: int
    main_task_id: str
    subtask_id: str
    turn_id: str | None
    source: str
    permissions: TaskPermissions

    def __post_init__(self) -> None:
        if not self.persona_id:
            raise ValueError("persona_id 不能为空")
        if not self.main_task_id:
            raise ValueError("main_task_id 不能为空")
        if not self.subtask_id:
            raise ValueError("subtask_id 不能为空")
        if self.source not in SOURCES:
            raise ValueError(f"source 必须是 {'、'.join(sorted(SOURCES))}；实际为 {self.source}")


_current: ContextVar[TaskContext | None] = ContextVar("pet_task_context", default=None)


def current_task() -> TaskContext:
    context = _current.get()
    if context is None:
        raise TaskContextError("文件访问缺少任务身份上下文；调用必须来自子代理工具")
    return context


def current_task_or_none() -> TaskContext | None:
    return _current.get()


@contextmanager
def task_scope(context: TaskContext) -> Iterator[TaskContext]:
    """在同步调用栈内建立任务作用域；退出时恢复外层，避免跨任务残留。"""
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
