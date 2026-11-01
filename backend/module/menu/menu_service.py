from module.menu.constants import (
    AVATAR_PREFIX,
    OPEN_CHAT,
    OPEN_SETTINGS,
    OPEN_STATUS,
    QUIT,
    SWITCH_AVATAR,
    TOGGLE_PROACTIVE,
)
from module.menu.contracts import AvatarCatalogReader, Menu, MenuItem, SchedulerConfigReader


class MenuService:
    def __init__(
        self,
        avatars: AvatarCatalogReader,
        scheduler: SchedulerConfigReader,
    ) -> None:
        self._avatars = avatars
        self._scheduler = scheduler

    def menu(self) -> Menu:
        catalog = self._avatars.catalog()
        config = self._scheduler.config()
        avatars = tuple(
            MenuItem(
                id=f"{AVATAR_PREFIX}{pack.id}",
                label=pack.name,
                enabled=True,
                checked=pack.id == catalog.active,
            )
            for pack in catalog.packs
        )
        return Menu(
            items=(
                MenuItem(id=OPEN_CHAT, label="打开对话", enabled=True, checked=None),
                MenuItem(id=OPEN_STATUS, label="状态", enabled=True, checked=None),
                MenuItem(id=OPEN_SETTINGS, label="设置", enabled=True, checked=None),
                MenuItem(
                    id=SWITCH_AVATAR,
                    label="切换形象",
                    enabled=bool(avatars),
                    checked=None,
                    children=avatars,
                ),
                # 勾选表示“已暂停”，与调度配置 enabled 相反
                MenuItem(
                    id=TOGGLE_PROACTIVE,
                    label="暂停主动消息",
                    enabled=True,
                    checked=not config.enabled,
                ),
                MenuItem(id=QUIT, label="退出", enabled=True, checked=None),
            )
        )
