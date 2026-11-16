"""文件域结构化日志。

一次动作在进入时开一条记录，退出时补齐结果与耗时；记录的 session 由任务上下文给出，
因此同一主任务/子任务下的读取、检索、编辑能按 session 串成完整阶段。
"""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from inspect import signature

logger = logging.getLogger("pet.files")

EVENT_ACTION = "files.action"
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
UNKNOWN_SESSION = "-"
_UNSET = "-"


def session_of(context) -> str:
    """压成 main_task_id/subtask_id；取不到上下文时给占位符，日志不因此中断。"""
    if context is None:
        return UNKNOWN_SESSION
    try:
        return f"{context.main_task_id}/{context.subtask_id}"
    except Exception:
        return UNKNOWN_SESSION


@dataclass
class ActionSpan:
    """一条动作记录；elapsed_ms 与 outcome 在退出时补齐。"""

    action: str
    resource: str = ""
    path: str = ""
    session: str = UNKNOWN_SESSION
    outcome: str = OUTCOME_OK
    elapsed_ms: int = 0
    error: str = ""


@contextmanager
def span(
    action: str,
    *,
    resource: str | None = None,
    path: str | None = None,
    context=None,
    clock: Callable[[], float] = time.perf_counter,
) -> Iterator[ActionSpan]:
    """记录一次文件动作。context 可以是任务上下文，也可以是返回它的工厂。"""
    source = context
    if callable(source):
        try:
            source = source()
        except Exception:
            source = None
    record = ActionSpan(
        action=action,
        resource=resource or "",
        path=path or "",
        session=session_of(source),
    )
    started = clock()
    try:
        yield record
    except BaseException as exc:
        record.outcome = OUTCOME_ERROR
        record.error = str(exc)
        raise
    finally:
        record.elapsed_ms = int((clock() - started) * 1000)
        _emit(record)


def traced(action: str) -> Callable:
    """给 FileService 的公开动作套一层 span；目标取自同名的 resource/path 参数。"""

    def decorate(func: Callable) -> Callable:
        position = _positions(func)

        @wraps(func)
        def wrapper(*args, **kwargs):
            resource, path = _targets(position, args, kwargs)
            context = getattr(args[0], "_context", None) if args else None
            with span(action, resource=resource, path=path, context=context):
                return func(*args, **kwargs)

        return wrapper

    return decorate


def _positions(func: Callable) -> dict[str, int | None]:
    names = list(signature(func).parameters)
    return {name: names.index(name) if name in names else None for name in ("resource", "path")}


def _targets(
    position: dict[str, int | None],
    args: tuple,
    kwargs: dict,
) -> tuple[str | None, str | None]:
    def value(name: str) -> str | None:
        index = position[name]
        if index is not None and index < len(args):
            return args[index]
        return kwargs.get(name)

    return value("resource"), value("path")


def _emit(record: ActionSpan) -> None:
    fields = [
        f"event={EVENT_ACTION}",
        f"session={record.session}",
        f"action={record.action}",
        f"resource={record.resource or _UNSET}",
        f"path={record.path or _UNSET}",
        f"outcome={record.outcome}",
        f"elapsed_ms={record.elapsed_ms}",
    ]
    if record.error:
        fields.append(f"error={record.error}")
    logger.debug(
        " ".join(fields),
        extra={
            "event": EVENT_ACTION,
            "session": record.session,
            "action": record.action,
            "resource": record.resource or _UNSET,
            "path": record.path or _UNSET,
            "outcome": record.outcome,
            "elapsed_ms": record.elapsed_ms,
            "error": record.error,
        },
    )
