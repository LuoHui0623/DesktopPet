"""T7–T11 共用的文件服务夹具。

只装配文件域真正需要的协作件：真实调度器不用，记忆与技能用最小替身，
以便单测聚焦索引、占用、队列与 review 视图本身。
"""

from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from constants.files import WORLD
from module.agent.approval import AutoApprovalGate
from module.agent.context import TaskContext, task_scope
from module.agent.review import ReviewService
from module.agent.skills import SkillRegistry
from module.files.handlers import ModelLimit
from module.files.permissions import (
    ResourcePermission,
    TaskPermissions,
    system_permissions,
)
from module.files.resolver import ResourceResolver
from module.files.service import FileService

TZ = ZoneInfo("Asia/Shanghai")


def make_permissions(*, without: tuple[str, ...] = ()) -> TaskPermissions:
    """从系统快照收紧 `world` 的动作集，其余资源保持不变。"""
    base = system_permissions()
    current = base.resources[WORLD]
    resources = {**base.resources}
    resources[WORLD] = ResourcePermission(
        current.actions - frozenset(without), current.can_delete_file
    )
    return TaskPermissions(resources)


def make_task(
    main_task_id: str = "main-1",
    *,
    permissions: TaskPermissions | None = None,
) -> TaskContext:
    return TaskContext(
        persona_id="芙芙",
        persona_version=1,
        main_task_id=main_task_id,
        subtask_id="sub-1",
        turn_id="turn-1",
        source="user",
        permissions=permissions or system_permissions(),
    )


@pytest.fixture
def env(tmp_path: Path):
    data = tmp_path / "data"
    world = data / "resources"
    world.mkdir(parents=True)
    (world / "草稿本.md").write_text("# 草稿本\n", encoding="utf-8")
    review = ReviewService()
    service = FileService(
        ResourceResolver(data),
        object(),
        object(),
        SkillRegistry(),
        sessions=review.sessions,
        guard=review.guard,
        timezone=TZ,
        limit_provider=lambda: ModelLimit(10000, "estimate"),
    )

    def bind(context: TaskContext) -> TaskContext:
        review.bind(
            context.main_task_id,
            AutoApprovalGate(),
            persona_id=context.persona_id,
            persona_version=context.persona_version,
            source=context.source,
        )
        return context

    task_a = bind(make_task())
    task_b = bind(make_task("main-2"))
    stack = SimpleNamespace(
        data=data,
        world=world,
        review=review,
        service=service,
        task_a=task_a,
        task_b=task_b,
        bind=bind,
        session=lambda task_id="main-1": review.sessions.peek(task_id),
    )
    with task_scope(task_a):
        yield stack
