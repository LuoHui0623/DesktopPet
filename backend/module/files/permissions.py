"""任务权限快照：派发时按系统登记解析，任务执行期间不随配置变化。"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from constants.files import RESOURCES


@dataclass(frozen=True)
class ResourcePermission:
    actions: frozenset[str]
    can_delete_file: bool


@dataclass(frozen=True)
class TaskPermissions:
    """一次任务可用的资源与动作；由后端在派发时填充，工具不能自行扩大。"""

    resources: Mapping[str, ResourcePermission]

    def __post_init__(self) -> None:
        object.__setattr__(self, "resources", MappingProxyType(dict(self.resources)))

    def actions_for(self, resource_id: str) -> frozenset[str]:
        permission = self.resources.get(resource_id)
        return permission.actions if permission is not None else frozenset()

    def allows(self, resource_id: str, action: str) -> bool:
        return action in self.actions_for(resource_id)

    def can_delete_file(self, resource_id: str) -> bool:
        permission = self.resources.get(resource_id)
        return permission is not None and permission.can_delete_file

    def read_supports_for(self, resource_id: str) -> tuple[str, ...]:
        """按注册表顺序投影当前任务仍有效的读动作，供 index 展示。"""
        spec = RESOURCES.get(resource_id)
        if spec is None:
            return ()
        return tuple(action for action in spec.read_supports if self.allows(resource_id, action))

    def edit_supports_for(self, resource_id: str) -> tuple[str, ...]:
        """按注册表顺序投影当前任务仍有效的写动作；空元组表示只读。"""
        spec = RESOURCES.get(resource_id)
        if spec is None:
            return ()
        return tuple(action for action in spec.edit_supports if self.allows(resource_id, action))


def system_permissions() -> TaskPermissions:
    """按资源登记表生成快照；后续用户配置只能在此基础上收紧。"""
    return TaskPermissions(
        resources={
            resource_id: ResourcePermission(
                actions=spec.actions, can_delete_file=spec.can_delete_file
            )
            for resource_id, spec in RESOURCES.items()
        }
    )
