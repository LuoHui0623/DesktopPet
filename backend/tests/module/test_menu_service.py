from constants.agent import DEFAULT_PERSONA_ID
from module.avatar.contracts import AvatarCatalog, AvatarPack
from module.menu.constants import (
    OPEN_CHAT,
    OPEN_SETTINGS,
    OPEN_STATUS,
    QUIT,
    SWITCH_AVATAR,
    TOGGLE_PROACTIVE,
)
from module.menu.menu_service import MenuService
from service.perception.scheduler import SchedulerConfig


def pack(pack_id: str, name: str) -> AvatarPack:
    return AvatarPack(
        id=pack_id,
        name=name,
        width=240,
        height=240,
        fallback="idle",
        states={},
    )


class FakeAvatars:
    def __init__(self, packs: tuple[AvatarPack, ...], active: str) -> None:
        self._packs = packs
        self._active = active

    def catalog(self) -> AvatarCatalog:
        return AvatarCatalog(persona=DEFAULT_PERSONA_ID, active=self._active, packs=self._packs)


class FakeScheduler:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def config(self) -> SchedulerConfig:
        return SchedulerConfig(
            enabled=self._enabled,
            min_interval_minutes=30,
            max_per_day=10,
            dnd_start="22:00",
            dnd_end="08:00",
        )


def test_builds_the_first_batch_items():
    service = MenuService(
        FakeAvatars((pack("default", "默认形象"),), active="default"),
        FakeScheduler(enabled=True),
    )

    menu = service.menu()

    assert [item.id for item in menu.items] == [
        OPEN_CHAT,
        OPEN_STATUS,
        OPEN_SETTINGS,
        SWITCH_AVATAR,
        TOGGLE_PROACTIVE,
        QUIT,
    ]
    assert [item.label for item in menu.items] == [
        "打开对话",
        "状态",
        "设置",
        "切换形象",
        "暂停主动消息",
        "退出",
    ]


def test_avatar_children_come_from_the_catalog():
    service = MenuService(
        FakeAvatars(
            (pack("default", "默认形象"), pack("cat", "猫咪")),
            active="cat",
        ),
        FakeScheduler(enabled=True),
    )

    switch = next(item for item in service.menu().items if item.id == SWITCH_AVATAR)

    assert switch.enabled is True
    assert switch.checked is None
    assert [(child.id, child.label, child.checked) for child in switch.children] == [
        ("avatar:default", "默认形象", False),
        ("avatar:cat", "猫咪", True),
    ]


def test_avatar_submenu_is_disabled_without_packs():
    service = MenuService(FakeAvatars((), active="default"), FakeScheduler(enabled=True))

    switch = next(item for item in service.menu().items if item.id == SWITCH_AVATAR)

    assert switch.enabled is False
    assert switch.children == ()


def test_paused_proactive_is_checked_when_scheduler_is_disabled():
    paused = MenuService(
        FakeAvatars((pack("default", "默认形象"),), active="default"),
        FakeScheduler(enabled=False),
    )
    running = MenuService(
        FakeAvatars((pack("default", "默认形象"),), active="default"),
        FakeScheduler(enabled=True),
    )

    def toggle(service: MenuService):
        return next(item for item in service.menu().items if item.id == TOGGLE_PROACTIVE)

    assert toggle(paused).checked is True
    assert toggle(running).checked is False
    assert toggle(paused).enabled is True
