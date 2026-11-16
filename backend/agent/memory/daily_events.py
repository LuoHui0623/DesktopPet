import json
from dataclasses import dataclass


class EventError(ValueError):
    code = "invalid_event"


@dataclass(frozen=True)
class EventSpec:
    fields: tuple[str, ...]
    in_context: bool
    template: str


EVENT_TYPES: dict[str, EventSpec] = {
    "session_boundary": EventSpec(
        fields=("persona",),
        in_context=False,
        template="切换人设为{persona}，新会话开始",
    ),
    "dispatch_failed": EventSpec(
        fields=("topic", "reason"),
        in_context=True,
        template="派发失败：{topic}，{reason}",
    ),
    "approval_rejected": EventSpec(
        fields=("topic", "reason"),
        in_context=True,
        template="审批被拒绝：{topic}，{reason}",
    ),
}


def _spec(event_type: str) -> EventSpec:
    spec = EVENT_TYPES.get(event_type)
    if spec is None:
        raise EventError(f"未知的 event 类型：{event_type}")
    return spec


def encode_event(event_type: str, **fields: str) -> str:
    spec = _spec(event_type)
    if set(fields) != set(spec.fields):
        raise EventError(
            f"event {event_type} 需要字段 {list(spec.fields)}，实际为 {sorted(fields)}"
        )
    values: dict[str, str] = {}
    for name in spec.fields:
        value = fields[name]
        if not isinstance(value, str) or not value.strip():
            raise EventError(f"event {event_type} 的字段 {name} 必须是非空字符串")
        values[name] = value.strip()
    return json.dumps({"type": event_type, **values}, ensure_ascii=False)


def decode_event(content: str) -> tuple[str, dict[str, str]]:
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise EventError("event 内容不是合法的 JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        raise EventError("event 缺少 type")
    event_type = data["type"]
    spec = _spec(event_type)
    fields = {key: value for key, value in data.items() if key != "type"}
    if set(fields) != set(spec.fields):
        raise EventError(f"event {event_type} 的字段不合法：{sorted(fields)}")
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise EventError(f"event {event_type} 的字段 {name} 必须是非空字符串")
    return event_type, fields


def event_in_context(content: str) -> bool:
    try:
        event_type, _ = decode_event(content)
    except EventError:
        return False
    return EVENT_TYPES[event_type].in_context


def event_text(content: str) -> str:
    try:
        event_type, fields = decode_event(content)
    except EventError:
        return content
    return EVENT_TYPES[event_type].template.format(**fields)


def display_content(role: str, content: str) -> str:
    return event_text(content) if role == "event" else content
