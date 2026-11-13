"""T9：FileGuard 占用、等待取消与前置校验顺序。"""

import threading

import pytest
from conftest import make_permissions, make_task

from module.agent.context import task_scope
from module.files.guard import FileBusy, FileGuard
from module.files.resolver import FileDenied, FileError


def test_same_task_reentry_does_not_stack(env):
    target = env.world / "笔记.md"
    target.write_text("a\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.append("world", "b", path="笔记.md")
    env.service.append("world", "c", path="笔记.md")

    assert env.review.guard.holder(target) == "main-1"
    env.review.guard.release(target, "main-1")
    assert env.review.guard.holder(target) is None


def test_another_task_waits_and_can_cancel(env):
    target = env.world / "笔记.md"
    guard = FileGuard()
    guard.acquire(target, "main-1")
    started = threading.Event()
    errors: list[Exception] = []
    cancel = threading.Event()

    def wait() -> None:
        started.set()
        try:
            guard.acquire(target, "main-2", cancel=cancel)
        except Exception as exc:  # noqa: BLE001 - 测试要区分取消与成功
            errors.append(exc)

    thread = threading.Thread(target=wait)
    thread.start()
    started.wait(1)
    cancel.set()
    thread.join(2)

    assert not thread.is_alive()
    assert errors and isinstance(errors[0], FileBusy)
    assert guard.holder(target) == "main-1"


def test_cancel_wait_stops_later_waiters(env):
    target = env.world / "笔记.md"
    guard = FileGuard()
    guard.acquire(target, "main-1")
    guard.cancel_wait("main-2")
    with pytest.raises(FileBusy):
        guard.acquire(target, "main-2")
    guard.clear_wait("main-2")
    guard.release(target, "main-1")
    guard.acquire(target, "main-2")
    assert guard.holder(target) == "main-2"


def test_read_is_not_blocked_by_another_tasks_occupancy(env):
    target = env.world / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.append("world", "c", path="笔记.md")

    other = env.bind(make_task("main-2"))
    with task_scope(other):
        text = env.service.read("world", "笔记.md")

    assert "1: a" in text
    assert "占用" in text
    assert "可 read" in text


def test_status_distinguishes_self_editing_from_occupied(env):
    target = env.world / "笔记.md"
    target.write_text("a\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.append("world", "b", path="笔记.md")
    guard = env.review.guard

    own = guard.status(target, "main-1")
    peer = guard.status(target, "main-2")
    assert own.state == "editing"
    assert peer.state == "occupied"
    assert peer.editing_by == {"task_id": "main-1"}


def test_permission_failure_releases_the_just_taken_occupancy(env):
    target = env.world / "笔记.md"
    target.write_text("a\n", encoding="utf-8")
    denied = env.bind(make_task("main-denied", permissions=make_permissions(without=("append",))))
    with task_scope(denied):
        env.service.read("world", "笔记.md")
        with pytest.raises(FileDenied):
            env.service.append("world", "b", path="笔记.md")

    assert env.review.guard.holder(target) is None


def test_version_mismatch_aborts_before_queueing(env):
    target = env.world / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[2, 2]], "B", path="笔记.md")
    session = env.session()
    abs_path = str(target.resolve())
    assert len(session.queue.entries_for(abs_path)) == 1

    target.write_text("a\nexternal\n", encoding="utf-8")
    with pytest.raises(FileError, match="已被修改"):
        env.service.append("world", "c", path="笔记.md")

    assert not session.queue.entries_for(abs_path)
    dropped = session.queue.entries(states=frozenset({"rejected"}))
    assert len(dropped) == 1
    assert target.read_text(encoding="utf-8") == "a\nexternal\n"
    # 失败的是本次动作；此前已取得的占用不会因后续动作失败被误释放。
    assert env.review.guard.holder(target) == "main-1"
    env.review.unbind("main-1")
    assert env.review.guard.holder(target) is None
