from api.event_publisher import BusEventPublisher
from module.agent.approval import ApprovalDraft, ApprovalTicket
from module.agent.preview import FilePreview, PreviewHunk


class CollectingBus:
    def __init__(self) -> None:
        self.events: list = []

    def publish(self, event) -> None:
        self.events.append(event)


def test_approval_event_carries_file_previews_and_model():
    bus = CollectingBus()
    publisher = BusEventPublisher(bus)
    preview = FilePreview(
        resource="world",
        path="笔记.md",
        op="replace_lines",
        created=False,
        deleted=False,
        authors=("sub-1", "sub-2"),
        hunk_count=1,
        folded=False,
        hunks=(PreviewHunk(header="@@ -2,1 +2,1 @@", removed=("旧",), added=("新",)),),
    )
    ticket = ApprovalTicket(
        approval_id="ap-1",
        draft=ApprovalDraft(
            run_id="world:笔记.md",
            topic="文件编辑：笔记.md",
            task="编辑 笔记.md",
            content="共 1 处改动；批准后写入，拒绝则丢弃该文件草稿",
            tools=("edit",),
            main_task_id="main-1",
            subtask_id="sub-2",
            previews=(preview,),
            model="deepseek/deepseek-flash",
        ),
    )

    publisher.approval_request("main-1", [ticket])

    event = bus.events[0]
    item = event.items[0]
    assert item.model == "deepseek/deepseek-flash"
    assert item.previews[0].path == "笔记.md"
    assert item.previews[0].authors == ["sub-1", "sub-2"]
    assert item.previews[0].hunks[0].removed == ["旧"]
    assert item.previews[0].hunks[0].added == ["新"]
    payload = event.model_dump(mode="json")
    assert payload["items"][0]["previews"][0]["hunk_count"] == 1
    assert payload["items"][0]["previews"][0]["deleted"] is False


def test_approval_event_defaults_to_no_preview_and_no_model():
    bus = CollectingBus()
    publisher = BusEventPublisher(bus)
    ticket = ApprovalTicket(
        approval_id="ap-2",
        draft=ApprovalDraft(
            run_id="run-1",
            topic="记忆写入",
            task="记一条",
            content="草案",
        ),
    )

    publisher.approval_request("main-2", [ticket])

    item = bus.events[0].items[0]
    assert item.previews == []
    assert item.model is None
