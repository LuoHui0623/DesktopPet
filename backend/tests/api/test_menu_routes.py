from fastapi.testclient import TestClient

from api.app import create_app
from module.avatar.contracts import AvatarCatalog, AvatarPack
from module.menu.menu_service import MenuService
from service.perception.scheduler import SchedulerConfig

TOKEN = "menu-token"
AUTH = {"X-Pet-Token": TOKEN}


class FakeAvatars:
    def catalog(self) -> AvatarCatalog:
        return AvatarCatalog(
            active="default",
            packs=(
                AvatarPack(
                    id="default",
                    name="默认形象",
                    width=240,
                    height=240,
                    fallback="idle",
                    states={},
                ),
            ),
        )


class FakeScheduler:
    def config(self) -> SchedulerConfig:
        return SchedulerConfig(
            enabled=False,
            min_interval_minutes=30,
            max_per_day=10,
            dnd_start="22:00",
            dnd_end="08:00",
        )


def make_client(service: MenuService | None) -> TestClient:
    return TestClient(create_app(TOKEN, menu_service=service))


def leaf(item_id: str, label: str, checked: bool | None = None) -> dict:
    return {"id": item_id, "label": label, "enabled": True, "checked": checked, "children": []}


def test_returns_the_backend_defined_menu():
    response = make_client(MenuService(FakeAvatars(), FakeScheduler())).get(
        "/api/menu", headers=AUTH
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            leaf("open_chat", "打开对话"),
            leaf("open_settings", "设置"),
            {
                "id": "switch_avatar",
                "label": "切换形象",
                "enabled": True,
                "checked": None,
                "children": [leaf("avatar:default", "默认形象", True)],
            },
            leaf("toggle_proactive", "暂停主动消息", True),
            leaf("quit", "退出"),
        ]
    }


def test_requires_token():
    response = make_client(MenuService(FakeAvatars(), FakeScheduler())).get("/api/menu")

    assert response.status_code == 401


def test_returns_503_without_service():
    response = make_client(None).get("/api/menu", headers=AUTH)

    assert response.status_code == 503
