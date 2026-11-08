import threading
from dataclasses import replace
from types import MappingProxyType

import pytest

from module.agent.context import (
    SOURCE_SYSTEM_AUTO,
    SOURCE_USER,
    TaskContext,
    TaskContextError,
    current_task,
    current_task_or_none,
    task_scope,
)
from module.files.permissions import ResourcePermission, TaskPermissions, system_permissions


def make_context(**overrides) -> TaskContext:
    values = {
        "persona_id": "芙芙",
        "persona_version": 3,
        "main_task_id": "main-1",
        "subtask_id": "sub-1",
        "turn_id": "turn-1",
        "source": SOURCE_USER,
        "permissions": system_permissions(),
    }
    values.update(overrides)
    return TaskContext(**values)


def test_current_task_requires_a_scope():
    assert current_task_or_none() is None
    with pytest.raises(TaskContextError, match="任务身份上下文"):
        current_task()


def test_task_scope_sets_and_restores_the_context():
    outer = make_context()
    inner = make_context(persona_id="小桌", subtask_id="sub-2")

    with task_scope(outer):
        assert current_task() == outer
        with task_scope(inner):
            assert current_task() == inner
        assert current_task() == outer

    assert current_task_or_none() is None


def test_scopes_are_isolated_between_threads():
    seen: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def worker(persona_id: str) -> None:
        context = make_context(persona_id=persona_id, subtask_id=f"sub-{persona_id}")
        with task_scope(context):
            barrier.wait(timeout=5)
            seen[persona_id] = current_task().persona_id

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("芙芙", "小桌")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert seen == {"芙芙": "芙芙", "小桌": "小桌"}


def test_source_must_be_user_or_system_auto():
    assert make_context(source=SOURCE_SYSTEM_AUTO).source == SOURCE_SYSTEM_AUTO
    with pytest.raises(ValueError, match="source"):
        make_context(source="model")


def test_identity_fields_must_not_be_empty():
    with pytest.raises(ValueError, match="main_task_id"):
        make_context(main_task_id="")
    with pytest.raises(ValueError, match="subtask_id"):
        make_context(subtask_id="")


def test_system_permissions_mirror_the_registry_and_allow_all_current_actions():
    permissions = system_permissions()

    assert set(permissions.resources) == {"draft", "structured", "permanent", "world"}
    assert permissions.allows("world", "replace_lines") is True
    assert permissions.allows("world", "delete") is True
    assert permissions.allows("world", "create") is False
    assert permissions.can_delete_file("world") is True
    assert permissions.can_delete_file("draft") is False
    assert permissions.actions_for("permanent") == frozenset({"index", "read", "grep"})


def test_permission_snapshot_is_read_only():
    resources = dict(system_permissions().resources)
    resources["draft"] = ResourcePermission(actions=frozenset({"read"}), can_delete_file=False)
    permissions = TaskPermissions(resources=MappingProxyType(resources))

    with pytest.raises(TypeError):
        permissions.resources["world"] = ResourcePermission(frozenset(), False)
    assert permissions.allows("draft", "read") is True
    assert permissions.allows("draft", "append") is False


def test_identity_can_be_replaced_without_touching_permissions():
    context = make_context()
    narrowed = replace(
        context,
        permissions=TaskPermissions(
            {
                **context.permissions.resources,
                "draft": ResourcePermission(frozenset({"index", "read"}), can_delete_file=False),
            }
        ),
    )

    assert narrowed.persona_id == context.persona_id
    assert narrowed.permissions.allows("draft", "append") is False
