"""记忆条目工具：AI 只提交 add / enable / disable 三种语义操作。"""

from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, ValidationError, model_validator

from module.memory.contracts import MemoryEntry, MemoryEntryCreate
from module.memory.entry_service import MemoryEntryError, MemoryEntryService

MemoryAction = Literal["add", "enable", "disable"]
MEMORY_ACTIONS: tuple[str, ...] = ("add", "enable", "disable")
ERRORS = (MemoryEntryError, ValidationError)


class MemoryArgs(BaseModel):
    action: MemoryAction = Field(
        description=(
            "add：新增一条记忆（内容相同且 tag 有交集时只刷新确认时间）；"
            "enable：恢复已弃用条目；disable：弃用一条记忆并写明原因"
        )
    )
    content: str | None = Field(default=None, description="add：一个可归类的短语或值，不写完整句式")
    tags: list[str] | None = Field(
        default=None, description="add：平铺标签，第一个是分区；优先复用已有 tag"
    )
    priority: int | None = Field(default=None, ge=1, le=3, description="add：1 低、2 普通、3 高")
    entry_id: int | None = Field(default=None, description="enable / disable：条目 id")
    reason: str | None = Field(default=None, description="disable：弃用原因，供之后 enable 时判断")

    @model_validator(mode="after")
    def _check_action(self) -> "MemoryArgs":
        if self.action == "add":
            if not (self.content and self.content.strip()):
                raise ValueError("add 必须提供 content")
            if not self.tags:
                raise ValueError("add 必须提供 tags，第一个 tag 是分区")
            if self.entry_id is not None or self.reason is not None:
                raise ValueError("add 不能使用 entry_id 或 reason")
            return self
        if self.entry_id is None:
            raise ValueError(f"{self.action} 必须提供 entry_id")
        if self.content is not None or self.tags is not None or self.priority is not None:
            raise ValueError(f"{self.action} 只接受 entry_id 与 reason")
        if self.action == "disable" and not (self.reason and self.reason.strip()):
            raise ValueError("disable 必须写明 reason")
        if self.action == "enable" and self.reason is not None:
            raise ValueError("enable 不接受 reason")
        return self


def make_memory_tools(service: MemoryEntryService) -> list[BaseTool]:
    @tool("memory", args_schema=MemoryArgs)
    def memory(
        action: str,
        content: str | None = None,
        tags: list[str] | None = None,
        priority: int | None = None,
        entry_id: int | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """按语义维护结构化记忆：add 新增（相同内容与 tag 交集只刷新确认时间）、
        enable 恢复已弃用条目、disable 弃用并写明原因。没有 update 与 merge：
        改值即弃旧 + 新增；合并 tag 只由设置页人工执行。"""
        try:
            if action == "add":
                payload = MemoryEntryCreate(
                    content=content or "",
                    tags=tags or [],
                    priority=2 if priority is None else priority,
                )
                result = service.add(payload)
                return _ok(
                    action,
                    entry=result.entry,
                    reused=result.reused,
                    new_tags=list(result.new_tags),
                )
            if action == "enable":
                return _ok(action, entry=service.enable(int(entry_id or 0)))
            if action == "disable":
                return _ok(action, entry=service.disable(int(entry_id or 0), reason or ""))
            return {"ok": False, "action": action, "error": f"未知的记忆动作：{action}"}
        except ERRORS as exc:
            return {"ok": False, "action": action, "error": str(exc)}

    return [memory]


def _ok(action: str, *, entry: MemoryEntry, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "action": action,
        "entry": {
            "id": entry.id,
            "content": entry.content,
            "tags": list(entry.tags),
            "priority": entry.priority,
            "status": entry.status_state,
        },
    }
    payload.update(extra)
    return payload
