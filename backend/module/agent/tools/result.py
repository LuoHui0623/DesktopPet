from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    action: str
    content: str | None = None
    error: str | None = None


def call(
    action: str,
    operation: Callable[[], str],
    errors: tuple[type[BaseException], ...],
) -> dict[str, Any]:
    try:
        content = operation()
    except errors as exc:
        return asdict(ToolResult(False, action, error=str(exc)))
    return asdict(ToolResult(True, action, content=content))
