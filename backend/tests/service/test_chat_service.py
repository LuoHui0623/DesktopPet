import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from config.settings import ModelSettingsSource
from constants.speech import SENTENCE_MAX_WIDTH
from infra.llm.tokens import TokenCount, TokenCountError
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.approval import ApprovalDraft
from module.agent.context import SOURCE_USER
from module.agent.orchestrator import Checkpoint
from module.agent.plan import PlanStep
from module.agent.prompt import load_templates
from module.chat.chat_service import (
    ChatError,
    ChatRequest,
    ChatService,
    DispatchRejectedError,
    ModelOutputError,
    ReplyMessage,
    overlong_sentences,
    parse_chat_output,
    split_sentences,
    visible_width,
)
from module.chat.context import (
    CURRENT_SEPARATOR,
    KIND_DAILY,
    KIND_STRUCTURED,
    MemoryFragment,
    day_marker,
)
from module.interaction.interaction_service import InteractionService
from module.llm import DbUsageReader, LlmService, UsageService
from module.llm.repository import db_usage_sink
from module.memory.daily_log_service import DailyLogService
from module.memory.entry_service import MemoryEntryService
from module.memory.memory_service import MemoryReadError
from module.memory.recall import RecallService
from module.memory.recollection_service import FAILURE_PREFIX, Recollection
from module.memory.repository import DbDailyLog
from module.persona.persona_service import PersonaService
from module.persona.repository import DbPersonaVersions
from module.settings.settings_service import SettingsService

LLM_YAML = """
providers:
  deepseek:
    display_name: DeepSeek
    models:
      - name: deepseek-flash
        id: deepseek-flash
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
"""

ENV = """
LLM_PROVIDERS=deepseek
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEFAULT_CHAT_MODEL=deepseek-flash
DEFAULT_CONSOLIDATE_MODEL=deepseek-flash
"""

CARD_TEXT = "role:\n  名字: 小桌\n  人设: 陪伴\npersonality:\n  - 温和\n"


def persona_service(tmp_path: Path, db_file: Path) -> PersonaService:
    directory = tmp_path / "personas"
    directory.mkdir(exist_ok=True)
    (directory / "芙芙.yaml").write_text(CARD_TEXT, encoding="utf-8")
    return PersonaService(directory, SettingsService(db_file), DbPersonaVersions(db_file))


SHORT = "你好呀，今天过得怎么样？"
LONG = "很" * 41 + "。"
CJK = "字"
BROKEN = '{"type": "chat", "text": '


class Raw(str):
    """模型原样输出的内容，用于验证协议错误。"""


def chat_output(text: str | list[str]) -> str:
    return json.dumps({"type": "chat", "text": text}, ensure_ascii=False)


def ask(chat: ChatService, text: str, fragments=()):
    return chat.chat(ChatRequest(user_text=text, fragments=fragments))


class FakeChat:
    def __init__(self, outcomes: list, trace=None) -> None:
        self.trace = trace
        self.outcomes = [
            outcome if isinstance(outcome, (Exception, Raw)) else chat_output(outcome)
            for outcome in outcomes
        ]
        self.calls: list[list] = []

    def invoke(self, messages, config=None, **kwargs):
        if self.trace is not None:
            self.trace.add("generate")
        self.calls.append(messages)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return AIMessage(
            content=outcome,
            usage_metadata={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
        )


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        outcomes: list,
        env: str = ENV,
        keys: dict | None = None,
        events=None,
        orchestrator=None,
        clock=None,
        interactions=None,
        llm_yaml: str = LLM_YAML,
        counter_factory=None,
        trace=None,
        recollection=None,
        recall=None,
        memory=None,
    ):
        self.db_file = tmp_path / "pet.db"
        self.tmp_path = tmp_path
        conn = connect(self.db_file)
        migrate(conn)
        conn.close()
        (tmp_path / ".env").write_text(env, encoding="utf-8")
        (tmp_path / "llm.yaml").write_text(llm_yaml, encoding="utf-8")
        environ = {"DEEPSEEK_API_KEY": "sk-test"} if keys is None else keys
        self.trace = trace
        self.fake = FakeChat(outcomes, trace=trace)
        self.llm = LlmService(
            ModelSettingsSource(tmp_path, environ=environ),
            builder=lambda spec: self.fake,
            environ=environ,
            usage_sink=db_usage_sink(self.db_file),
            counter_factory=counter_factory,
        )
        self.llm.start()
        self.daily = DailyLogService(
            DbDailyLog(self.db_file),
            SettingsService(self.db_file),
            ZoneInfo("Asia/Shanghai"),
            **({"clock": clock} if clock is not None else {}),
        )
        self.clock = clock
        self.events = events
        self.orchestrator = orchestrator
        self.interactions = interactions
        self.memory = memory or MemoryEntryService(self.db_file)
        self.recollection = (
            recollection(self.daily, trace) if callable(recollection) else recollection
        )
        if self.recollection is not None:
            self.recollection.bind(self.daily, trace)
        self.recall = recall(self.daily) if callable(recall) else recall
        self.chat = self.new_chat()

    def new_chat(self) -> ChatService:
        """在同一个数据库上重建服务，模拟重启。"""
        daily = TracedDaily(self.daily, self.trace) if self.trace is not None else self.daily
        return ChatService(
            self.llm,
            daily,
            persona_service(self.tmp_path, self.db_file),
            load_templates(),
            db_usage_sink(self.db_file),
            self.events,
            timezone=ZoneInfo("Asia/Shanghai"),
            clock=self.clock,
            orchestrator=self.orchestrator,
            interactions=self.interactions,
            usage=UsageService(DbUsageReader(self.db_file)),
            recollection=self.recollection,
            recall=self.recall,
            memory=self.memory,
        )

    def query(self, sql: str) -> list[tuple]:
        conn = connect(self.db_file)
        try:
            return [tuple(row) for row in conn.execute(sql).fetchall()]
        finally:
            conn.close()


def test_normal_call_returns_reply_and_writes_usage_and_log(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT])
    result = ask(harness.chat, "你好")
    assert result.text == SHORT
    assert result.recorded is True
    usage = harness.query("SELECT purpose, success, tokens_in, prompt_hash FROM llm_call")
    assert len(usage) == 1
    assert usage[0][:3] == ("chat", 1, 3)
    assert len(usage[0][3]) == 64
    logged = harness.query("SELECT role, content FROM daily_log ORDER BY id")
    assert logged == [("master", "你好"), ("me", SHORT)]


def test_system_prompt_comes_first_and_contains_persona(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT])
    ask(harness.chat, "你好")
    first = harness.fake.calls[0][0]
    assert isinstance(first, SystemMessage)
    assert "你的名字是小桌" in first.content


class BrokenMemory:
    """模拟结构化记忆读取失败。"""

    def load(self, **kwargs):
        raise MemoryReadError("坏了")


def test_empty_structured_memory_adds_no_notice(tmp_path: Path):
    events = RecordingEvents()
    harness = Harness(tmp_path, [SHORT], events=events)
    result = ask(harness.chat, "你好")

    assert result.notices == ()
    assert next(entry for entry in events.log if entry[0] == "done")[3] == ()


def test_structured_memory_failure_keeps_the_reply_and_reports_a_notice(tmp_path: Path):
    events = RecordingEvents()
    harness = Harness(tmp_path, [SHORT], events=events, memory=BrokenMemory())
    result = ask(harness.chat, "你好")

    assert result.text == SHORT
    assert result.recorded is True
    assert result.notices == ("memory_unloaded",)
    done = next(entry for entry in events.log if entry[0] == "done")
    assert done[3] == ("memory_unloaded",)


def test_dropped_fragments_do_not_become_user_notices(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("module.chat.context.FRAGMENT_TOKEN_BUDGET", 40)
    harness = Harness(tmp_path, [SHORT])
    fragments = [MemoryFragment(kind=KIND_DAILY, date="2026-10-18", text="字" * 20)] * 4
    result = ask(harness.chat, "你好", fragments)

    assert result.notices == ()


def test_multiple_utterances_are_returned_and_logged_in_order(tmp_path: Path):
    events = RecordingEvents()
    harness = Harness(tmp_path, [["第一句。", "第二句！", "第三句。"]], events=events)
    result = ask(harness.chat, "你好")

    assert [message.text for message in result.messages] == ["第一句。", "第二句！", "第三句。"]
    assert result.text == "第一句。\n第二句！\n第三句。"
    assert [message.text for message in events.log[-2][2]] == [
        "第一句。",
        "第二句！",
        "第三句。",
    ]
    logged = harness.query("SELECT role, content FROM daily_log ORDER BY id")
    assert logged == [
        ("master", "你好"),
        ("me", "第一句。"),
        ("me", "第二句！"),
        ("me", "第三句。"),
    ]


def test_each_utterance_keeps_its_own_sources(tmp_path: Path):
    reply = ["你上次说过喜欢美式 [引用 2026-10-18]。", "另外昨天聊过茶 [引用 2026-10-17]。"]
    harness = Harness(tmp_path, [reply])
    result = ask(harness.chat, "你好", CITED_FRAGMENTS)

    assert [message.text for message in result.messages] == [
        "你上次说过喜欢美式。",
        "另外昨天聊过茶。",
    ]
    assert [[source.id for source in message.sources] for message in result.messages] == [
        ["2026-10-18"],
        ["2026-10-17"],
    ]
    assert [source.id for source in result.sources] == ["2026-10-18", "2026-10-17"]


def test_reply_messages_share_one_generation_time(tmp_path: Path):
    harness = Harness(tmp_path, [["第一句。", "第二句！"]])
    result = ask(harness.chat, "你好")

    assert isinstance(result.messages[0], ReplyMessage)
    assert result.messages[0].at == result.messages[1].at
    assert result.messages[0].at.tzinfo is not None
    ids = [message.message_id for message in result.messages]
    assert len(set(ids)) == 2
    assert all(len(item) == 32 for item in ids)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class MutableClock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


class ShiftingClock:
    """每次调用前进一秒，用来验证同一轮只采样一次接收时间。"""

    def __init__(self, start: datetime) -> None:
        self.moment = start

    def __call__(self, zone: ZoneInfo) -> datetime:
        current = self.moment.astimezone(zone)
        self.moment = self.moment + timedelta(seconds=1)
        return current


def test_interactions_are_injected_once_without_being_persisted(tmp_path: Path):
    moment = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    interactions = InteractionService(clock=FakeClock(moment))
    harness = Harness(
        tmp_path, [SHORT, SHORT], clock=MutableClock(moment), interactions=interactions
    )
    interactions.record("click")

    ask(harness.chat, "你好")

    last = harness.fake.calls[0][-1]
    assert isinstance(last, HumanMessage)
    assert last.content == "[master] 单击宠物\n\n[master][10:00:00]你好"
    assert harness.query("SELECT role, content FROM daily_log ORDER BY id") == [
        ("master", "你好"),
        ("me", SHORT),
    ]

    ask(harness.chat, "在吗")
    assert harness.fake.calls[1][-1].content == "[master][10:00:00]在吗"


def test_interactions_wait_for_the_next_user_turn(tmp_path: Path):
    clock = FakeClock(datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    interactions = InteractionService(clock=clock)
    harness = Harness(tmp_path, [SHORT, SHORT], interactions=interactions)
    interactions.record("hover")

    harness.chat.chat(ChatRequest(trigger_reason="到点了"))
    proactive_prompt = harness.fake.calls[0][-1]
    assert isinstance(proactive_prompt, HumanMessage)
    assert "[master]" not in proactive_prompt.content

    ask(harness.chat, "你好")
    assert "[master] 悬停查看宠物" in harness.fake.calls[1][-1].content


def test_overlong_sentence_is_regenerated_once(tmp_path: Path):
    harness = Harness(tmp_path, [LONG, SHORT])
    result = ask(harness.chat, "你好")
    assert result.text == SHORT
    assert len(harness.fake.calls) == 2
    assert harness.query("SELECT COUNT(*) FROM llm_call")[0][0] == 2


def test_overlong_twice_returns_reason_not_truncated_text(tmp_path: Path):
    harness = Harness(tmp_path, [LONG, LONG])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "sentence_too_long"
    assert "80" in str(exc.value)
    assert harness.query("SELECT role, content FROM daily_log") == [("master", "你好")]


def test_missing_key_is_clear_error_and_records_failure(tmp_path: Path):
    harness = Harness(tmp_path, [], keys={})
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "model_unavailable"
    assert "DEEPSEEK_API_KEY" in str(exc.value)
    usage = harness.query("SELECT provider, model, success FROM llm_call")
    assert usage == [("deepseek", "deepseek-flash", 0)]
    assert harness.query("SELECT role, content FROM daily_log") == [("master", "你好")]


def test_unconfigured_chat_model_is_clear_error_and_records_failure(tmp_path: Path):
    env = ENV.replace("DEFAULT_CHAT_MODEL=deepseek-flash\n", "")
    harness = Harness(tmp_path, [], env=env)
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "model_unavailable"
    assert harness.query("SELECT provider, success FROM llm_call") == [("-", 0)]


def test_model_failure_is_reported_without_fabricated_reply(tmp_path: Path):
    harness = Harness(tmp_path, [RuntimeError("upstream down")])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "model_failed"
    assert "upstream down" in str(exc.value)
    assert harness.query("SELECT success FROM llm_call") == [(0,)]
    assert harness.query("SELECT role, content FROM daily_log") == [("master", "你好")]


def test_empty_model_reply_is_a_failure(tmp_path: Path):
    harness = Harness(tmp_path, [Raw("   "), Raw("   ")])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "invalid_output"
    assert harness.query("SELECT role, content FROM daily_log") == [("master", "你好")]


def test_broken_json_reply_is_regenerated_once(tmp_path: Path):
    harness = Harness(tmp_path, [Raw(BROKEN), Raw(chat_output(SHORT))])
    result = ask(harness.chat, "你好")
    assert result.text == SHORT
    assert len(harness.fake.calls) == 2


def test_broken_json_twice_returns_reason(tmp_path: Path):
    harness = Harness(tmp_path, [Raw(BROKEN), Raw(BROKEN)])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "invalid_output"
    assert "JSON" in str(exc.value)


def test_type_must_be_the_first_field():
    with pytest.raises(ModelOutputError, match="第一个字段"):
        parse_chat_output(json.dumps({"text": SHORT, "type": "chat"}, ensure_ascii=False))


def test_chat_output_accepts_only_type_and_text():
    with pytest.raises(ModelOutputError, match="两个字段"):
        parse_chat_output(
            json.dumps({"type": "chat", "text": SHORT, "extra": "x"}, ensure_ascii=False)
        )


def test_dispatch_plan_is_parsed():
    output = parse_chat_output(
        json.dumps(
            {"type": "dispatch", "plan": [{"topic": "记忆写入", "task": "记下主人喜欢猫"}]},
            ensure_ascii=False,
        )
    )
    assert output.kind == "dispatch"
    assert output.plan == (PlanStep("记忆写入", "记下主人喜欢猫"),)


def test_dispatch_plan_must_be_one_to_three_items():
    with pytest.raises(DispatchRejectedError, match="1–3"):
        parse_chat_output(json.dumps({"type": "dispatch", "plan": []}, ensure_ascii=False))
    with pytest.raises(DispatchRejectedError, match="1–3"):
        parse_chat_output(
            json.dumps(
                {"type": "dispatch", "plan": [{"topic": "记忆写入", "task": "记下"}] * 4},
                ensure_ascii=False,
            )
        )


def test_dispatch_accepts_only_type_and_plan():
    with pytest.raises(DispatchRejectedError, match="两个字段"):
        parse_chat_output(
            json.dumps(
                {
                    "type": "dispatch",
                    "plan": [{"topic": "记忆写入", "task": "记下"}],
                    "extra": "x",
                },
                ensure_ascii=False,
            )
        )


def test_dispatch_step_fields_are_validated():
    with pytest.raises(DispatchRejectedError, match="缺少任务描述"):
        parse_chat_output(
            json.dumps(
                {"type": "dispatch", "plan": [{"topic": "记忆写入"}]},
                ensure_ascii=False,
            )
        )


def test_unknown_type_and_empty_text_are_rejected():
    with pytest.raises(ModelOutputError, match="type 不合法"):
        parse_chat_output(json.dumps({"type": "weather", "text": SHORT}, ensure_ascii=False))
    with pytest.raises(ModelOutputError, match="非空字符串"):
        parse_chat_output(json.dumps({"type": "chat", "text": "  "}, ensure_ascii=False))


def test_chat_output_text_is_returned_stripped():
    assert parse_chat_output(chat_output(f"  {SHORT}  ")).text == SHORT


def test_markdown_fenced_output_is_accepted():
    assert parse_chat_output(f"```json\n{chat_output(SHORT)}\n```").text == SHORT


def test_output_with_surrounding_prose_is_extracted():
    content = f"好的，下面是我的回复：\n{chat_output(SHORT)}\n以上。"
    assert parse_chat_output(content).text == SHORT


def test_fenced_dispatch_is_parsed():
    payload = json.dumps(
        {"type": "dispatch", "plan": [{"topic": "记忆写入", "task": "记下主人喜欢猫"}]},
        ensure_ascii=False,
    )
    output = parse_chat_output(f"```json\n{payload}\n```")
    assert output.kind == "dispatch"
    assert output.plan == (PlanStep("记忆写入", "记下主人喜欢猫"),)


def test_fence_around_plain_text_is_accepted_as_chat():
    output = parse_chat_output(f"```text\n{SHORT}\n```")
    assert output.kind == "chat"
    assert output.texts == (SHORT,)


def test_plain_text_reply_is_accepted_as_chat():
    assert parse_chat_output(SHORT).texts == (SHORT,)


def test_plain_reply_strips_timeline_prefixes_and_splits_paragraphs():
    content = "[me][22:00:48]第一句。\n\n[me][22:00:48]第二句。\n\n第三句。"
    assert parse_chat_output(content).texts == ("第一句。", "第二句。", "第三句。")


def test_malformed_json_is_still_rejected():
    with pytest.raises(ModelOutputError, match="JSON"):
        parse_chat_output(BROKEN)


def test_chat_output_accepts_an_ordered_array_of_up_to_four_items():
    output = parse_chat_output(chat_output(["第一句。", "第二句！", "第三句。", "第四句。"]))
    assert output.kind == "chat"
    assert output.texts == ("第一句。", "第二句！", "第三句。", "第四句。")
    assert output.text == "第一句。\n第二句！\n第三句。\n第四句。"


def test_legacy_string_text_is_normalized_to_a_single_item():
    output = parse_chat_output(chat_output(SHORT))
    assert output.texts == (SHORT,)


def test_empty_array_and_more_than_four_items_are_rejected():
    with pytest.raises(ModelOutputError, match="1"):
        parse_chat_output(chat_output([]))
    with pytest.raises(ModelOutputError, match="4"):
        parse_chat_output(chat_output(["一。", "二。", "三。", "四。", "五。"]))


def test_every_item_must_be_a_non_blank_string():
    with pytest.raises(ModelOutputError, match="字符串"):
        parse_chat_output(json.dumps({"type": "chat", "text": ["好。", 1]}, ensure_ascii=False))
    with pytest.raises(ModelOutputError, match="非空"):
        parse_chat_output(chat_output(["好。", "   "]))


def test_items_are_stripped_and_a_citation_only_item_is_rejected():
    assert parse_chat_output(chat_output(["  好。  ", " 也好。 "])).texts == ("好。", "也好。")
    with pytest.raises(ModelOutputError, match="只有引用标记"):
        parse_chat_output(chat_output(["好。", "[引用 2026-10-18]"]))


def test_one_overlong_item_in_the_array_is_enough_to_fail(tmp_path: Path):
    harness = Harness(tmp_path, [chat_output(["好的。", CJK * 41]), SHORT])
    assert ask(harness.chat, "你好").text == SHORT
    assert len(harness.fake.calls) == 2
    assert harness.query("SELECT COUNT(*) FROM daily_log WHERE role = 'me'")[0][0] == 1


def test_legacy_temporary_setting_no_longer_blocks_the_log(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT])
    SettingsService(harness.db_file).put("memory.temporary_mode", "true")
    result = ask(harness.chat, "你好")
    assert result.text == SHORT
    assert result.recorded is True
    assert harness.query("SELECT role, content FROM daily_log ORDER BY id") == [
        ("master", "你好"),
        ("me", SHORT),
    ]


def test_empty_input_is_rejected(tmp_path: Path):
    harness = Harness(tmp_path, [])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "   ")
    assert exc.value.code == "empty_input"
    assert harness.fake.calls == []


def test_proactive_request_writes_only_a_reply_and_uses_the_trigger_block(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT])
    result = harness.chat.chat(
        ChatRequest(trigger_reason="使用 code 已 3 分钟", allow_dispatch=False)
    )

    assert result.recorded is True
    assert harness.query("SELECT role, content FROM daily_log ORDER BY id") == [("me", SHORT)]
    input_message = harness.fake.calls[0][-1]
    assert isinstance(input_message, HumanMessage)
    assert "【本次触发】使用 code 已 3 分钟" in input_message.content
    assert "【当前时间】" in input_message.content
    assert "现在主动说一句话" in input_message.content


def test_proactive_reply_enters_the_next_context_as_a_reply(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT, "你好呀。"])
    harness.chat.chat(ChatRequest(trigger_reason="深夜仍在使用电脑", allow_dispatch=False))
    ask(harness.chat, "你在干嘛")

    contents = [message.content for message in harness.fake.calls[1]]
    assert any(text.startswith("[me][") and text.endswith(f"]{SHORT}") for text in contents)
    assert "【本次触发】深夜仍在使用电脑" not in contents


def test_proactive_request_can_disable_dispatch(tmp_path: Path):
    harness = Harness(tmp_path, [Raw(DISPATCH), Raw(DISPATCH)])
    with pytest.raises(ChatError) as exc:
        harness.chat.chat(ChatRequest(trigger_reason="使用 code 已 3 分钟", allow_dispatch=False))
    assert exc.value.code == "invalid_output"
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


def test_proactive_dispatch_is_requested_in_read_only_mode(tmp_path: Path):
    plan = json.dumps(
        {"type": "dispatch", "plan": [{"topic": "记忆检索", "task": "查咖啡偏好"}]},
        ensure_ascii=False,
    )
    orchestrator = FakeOrchestrator(outputs={"记忆检索": "查到：主人喜欢美式。"})
    harness = Harness(tmp_path, [Raw(plan), "想起来啦。"], orchestrator=orchestrator)

    result = harness.chat.chat(
        ChatRequest(trigger_reason="主人刚打开电脑", read_only_dispatch=True)
    )

    assert result.text == "想起来啦。"
    assert orchestrator.read_only_flags == [True]


def test_chat_requires_user_text_or_trigger_reason(tmp_path: Path):
    harness = Harness(tmp_path, [])
    with pytest.raises(ChatError) as exc:
        harness.chat.chat(ChatRequest())
    assert exc.value.code == "empty_input"
    assert harness.fake.calls == []


def test_split_sentences_uses_all_boundaries():
    text = "你好。我在！真的吗？好的……嗯\n最后一句"
    assert split_sentences(text) == ["你好。", "我在！", "真的吗？", "好的……", "嗯", "最后一句"]


def test_split_sentences_handles_english_periods():
    assert split_sentences("Hello there. Next one! Really?") == [
        "Hello there.",
        "Next one!",
        "Really?",
    ]
    # 小数、域名、常见缩写内部的点不是句末
    assert split_sentences("It costs 1.5 dollars.") == ["It costs 1.5 dollars."]
    assert split_sentences("Visit example.com now.") == ["Visit example.com now."]
    assert split_sentences("Mr. Smith is here.") == ["Mr. Smith is here."]


def test_visible_width_counts_cjk_as_two_and_ascii_as_one():
    assert visible_width("字") == 2
    assert visible_width("a") == 1
    assert visible_width(" ") == 1
    assert visible_width("。") == 2
    assert visible_width("你好ab") == 6


def test_sentence_length_budget_accepts_both_languages():
    assert SENTENCE_MAX_WIDTH == 80
    assert overlong_sentences(CJK * 40) == []
    assert overlong_sentences("a" * 80) == []
    assert overlong_sentences(CJK * 20 + "a" * 40) == []


def test_sentence_length_overflow_by_one_unit():
    assert overlong_sentences(CJK * 41) == [CJK * 41]
    assert overlong_sentences("a" * 81) == ["a" * 81]
    assert overlong_sentences(CJK * 21 + "a" * 40) == [CJK * 21 + "a" * 40]


def test_sentence_length_counts_the_trailing_punctuation():
    assert overlong_sentences(CJK * 39 + "。") == []
    assert overlong_sentences(CJK * 40 + "。") == [CJK * 40 + "。"]
    assert overlong_sentences("短句一。" + CJK * 41) == [CJK * 41]


class RecordingEvents:
    def __init__(self) -> None:
        self.log: list[tuple] = []

    def status(self, state):
        self.log.append(("status", state))

    def chunk(self, message_id, text):
        self.log.append(("chunk", message_id, text))

    def done(self, message_id, messages, notices=()):
        self.log.append(("done", message_id, list(messages), tuple(notices)))


def test_events_follow_thinking_speaking_chunks_done_idle(tmp_path: Path):
    events = RecordingEvents()
    harness = Harness(tmp_path, ["第一句。第二句！"], events=events)
    result = ask(harness.chat, "你好")
    kinds = [entry[0] for entry in events.log]
    assert kinds == ["status", "status", "chunk", "chunk", "done", "status"]
    assert events.log[0] == ("status", "thinking")
    assert events.log[1] == ("status", "speaking")
    assert events.log[-1] == ("status", "idle")
    done = events.log[4]
    assert done[1] == result.message_id
    assert [message.text for message in done[2]] == ["第一句。第二句！"]


def test_chunks_join_back_to_the_reply(tmp_path: Path):
    events = RecordingEvents()
    reply = "你好。\n今天\n\n天气不错……真的！"
    harness = Harness(tmp_path, [reply], events=events)
    ask(harness.chat, "你好")
    chunks = [entry[2] for entry in events.log if entry[0] == "chunk"]
    assert "".join(chunks) == reply


def test_chunks_of_several_items_join_back_to_the_joined_reply(tmp_path: Path):
    events = RecordingEvents()
    harness = Harness(tmp_path, [["第一句。", "第二句！"]], events=events)
    result = ask(harness.chat, "你好")
    chunks = [entry[2] for entry in events.log if entry[0] == "chunk"]
    assert "".join(chunks) == result.text == "第一句。\n第二句！"


def test_failed_reply_still_returns_status_to_idle(tmp_path: Path):
    events = RecordingEvents()
    harness = Harness(tmp_path, [RuntimeError("down")], events=events)
    with pytest.raises(ChatError):
        ask(harness.chat, "你好")
    assert events.log == [("status", "thinking"), ("status", "idle")]


def test_history_returns_newest_first_with_has_more(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT, SHORT])
    ask(harness.chat, "一")
    ask(harness.chat, "二")
    records, has_more = harness.chat.history(
        keyword=None, date_from=None, date_to=None, limit=3, offset=0
    )
    assert [record.content for record in records] == [SHORT, "二", SHORT]
    assert has_more is True
    records, has_more = harness.chat.history(
        keyword=None, date_from=None, date_to=None, limit=3, offset=3
    )
    assert [record.content for record in records] == ["一"]
    assert has_more is False


def test_second_reply_includes_the_earlier_turn_of_the_day(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT, SHORT])
    ask(harness.chat, "第一句")
    ask(harness.chat, "第二句")
    second_call = harness.fake.calls[1]
    contents = [message.content for message in second_call[1:]]
    assert contents[0].startswith("[master][")
    assert contents[0].endswith("]第一句")
    assert contents[1].startswith("[me][")
    assert contents[1].endswith(f"]{SHORT}")
    assert contents[-1].startswith("[master][")
    assert contents[-1].endswith("]第二句")


def test_input_time_comes_from_the_receive_sample(tmp_path: Path):
    """同一轮只采样一次接收时间，输入前缀与日期标记都来自它。"""
    clock = ShiftingClock(datetime(2026, 10, 29, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    harness = Harness(tmp_path, [SHORT], clock=clock)

    ask(harness.chat, "你好")

    call = harness.fake.calls[0]
    assert call[-1].content == "[master][10:00:00]你好"
    assert call[0].content.endswith(
        day_marker(datetime(2026, 10, 29, tzinfo=ZoneInfo("Asia/Shanghai")))
    )


def test_restored_context_comes_from_daily_log_even_for_rows_written_elsewhere(
    tmp_path: Path,
):
    moment = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    harness = Harness(tmp_path, [SHORT], clock=MutableClock(moment))
    harness.daily.record_turn("外部记录", "外部回复", received_at=moment, replied_at=moment)

    ask(harness.chat, "你好")

    contents = [message.content for message in harness.fake.calls[0]]
    assert any(text.endswith("]外部记录") for text in contents)
    assert any(text.endswith("]外部回复") for text in contents)


def test_day_change_starts_a_new_snapshot(tmp_path: Path):
    clock = MutableClock(datetime(2026, 10, 18, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    harness = Harness(tmp_path, [SHORT, SHORT], clock=clock)
    ask(harness.chat, "第一句")

    clock.moment = datetime(2026, 10, 19, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    ask(harness.chat, "第二句")

    contents = [message.content for message in harness.fake.calls[1][1:]]
    assert contents == ["[master][08:00:00]第二句"]


def test_legacy_temporary_setting_keeps_earlier_turns_in_the_snapshot(tmp_path: Path):
    clock = MutableClock(datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    harness = Harness(tmp_path, [SHORT, SHORT], clock=clock)
    SettingsService(harness.db_file).put("memory.temporary_mode", "true")
    ask(harness.chat, "第一句")
    ask(harness.chat, "第二句")

    contents = [message.content for message in harness.fake.calls[1][1:]]
    assert contents == [
        "[master][10:00:00]第一句",
        f"[me][10:00:00]{SHORT}",
        CURRENT_SEPARATOR,
        "[master][10:00:00]第二句",
    ]


CITED_FRAGMENTS = [
    MemoryFragment(kind=KIND_STRUCTURED, date="2026-10-18", text="喜欢美式咖啡，不加糖"),
    MemoryFragment(kind=KIND_DAILY, date="2026-10-17", text="昨天聊了茶"),
]


def test_citations_are_stripped_and_only_injected_memories_become_sources(tmp_path: Path):
    events = RecordingEvents()
    reply = (
        "你上次说过喜欢美式 [引用 2026-10-18]。"
        "另外昨天聊过茶 [引用 2026-10-17]，还有编造的 [引用 2026-10-01]。"
    )
    harness = Harness(tmp_path, [reply], events=events)
    result = ask(harness.chat, "你好", CITED_FRAGMENTS)
    assert result.text == "你上次说过喜欢美式。另外昨天聊过茶，还有编造的。"
    assert [source.id for source in result.sources] == ["2026-10-18", "2026-10-17"]
    assert result.sources[0].text == "喜欢美式咖啡，不加糖"
    done = next(entry for entry in events.log if entry[0] == "done")
    assert [source.id for source in done[2][0].sources] == ["2026-10-18", "2026-10-17"]
    assert harness.query("SELECT content FROM daily_log WHERE role = 'me'") == [(result.text,)]


def test_injected_fragments_carry_their_dates_without_a_citation_rule(tmp_path: Path):
    """第 26 条：拒绝 `[引用 日期]` 引用格式，片段只带来源标签、不再附引用规则。"""
    harness = Harness(tmp_path, [SHORT])
    ask(harness.chat, "你好", CITED_FRAGMENTS)
    system = harness.fake.calls[0][0].content
    assert "[结构化记忆 · 2026-10-18] 喜欢美式咖啡，不加糖" in system
    assert "[每日对话 · 2026-10-17] 昨天聊了茶" in system
    assert "[引用 日期]" not in system


class BrokenRecall:
    def __init__(self, daily) -> None:
        self._daily = daily

    def recall(self, *args, **kwargs):
        raise RuntimeError("召回坏了")


def test_memory_recall_is_disabled_pending_design(tmp_path: Path):
    # 记忆召回（RecallService 关键词检索）尚未设计，先禁用（T-03 重议后恢复）：
    # 即使接上 RecallService 且有可召回历史，也不注入【检索到的记忆】。
    harness = Harness(
        tmp_path,
        [SHORT],
        clock=lambda tz: datetime(2026, 11, 9, 12, 0, tzinfo=tz),
        recall=RecallService,
    )
    zone = ZoneInfo("Asia/Shanghai")
    harness.daily.record_turn(
        "今天玩了冰汽时代",
        "冰汽时代太难了",
        received_at=datetime(2026, 11, 7, 20, 0, tzinfo=zone),
        replied_at=datetime(2026, 11, 7, 20, 0, 5, tzinfo=zone),
    )

    ask(harness.chat, "还记得冰汽时代吗")

    system = harness.fake.calls[0][0].content
    assert "【检索到的记忆】" not in system
    assert "冰汽时代太难了" not in system


def test_chat_survives_recall_failure(tmp_path: Path):
    harness = Harness(
        tmp_path,
        [SHORT],
        clock=lambda tz: datetime(2026, 11, 9, 12, 0, tzinfo=tz),
        recall=BrokenRecall,
    )

    result = ask(harness.chat, "还记得冰汽时代吗")

    assert result.text == SHORT
    assert "【检索到的记忆】" not in harness.fake.calls[0][0].content


def test_reply_with_only_citation_markers_is_a_failure(tmp_path: Path):
    marker = "[引用 2026-10-18]"
    harness = Harness(tmp_path, [marker, marker])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好", CITED_FRAGMENTS)
    assert exc.value.code == "invalid_output"
    assert harness.query("SELECT role, content FROM daily_log") == [("master", "你好")]


DISPATCH = json.dumps(
    {"type": "dispatch", "plan": [{"topic": "记忆写入", "task": "记下主人喜欢猫"}]},
    ensure_ascii=False,
)


class FakeOrchestrator:
    def __init__(
        self,
        ok: bool = True,
        outputs: dict[str, str] | None = None,
        errors: dict[str, str] | None = None,
    ) -> None:
        self.plans: list[list[PlanStep]] = []
        self.ok = ok
        self.outputs = outputs or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, list]] = []
        self.read_only_flags: list[bool] = []
        self.turn_ids: list[str | None] = []
        self.personas: list[object] = []
        self.sources: list[str | None] = []

    def run_plan(
        self,
        plan,
        *,
        gate,
        persona=None,
        source=None,
        plan_id=None,
        read_only=False,
        turn_id=None,
    ):
        steps = list(plan)
        self.plans.append(steps)
        self.read_only_flags.append(read_only)
        self.turn_ids.append(turn_id)
        self.personas.append(persona)
        self.sources.append(source)
        drafts = [
            ApprovalDraft(
                run_id=f"run-{index}",
                topic=step.topic,
                task=step.task,
                content=f"草案：{step.topic}",
                tools=("append",),
            )
            for index, step in enumerate(steps, start=1)
        ]
        outcomes = gate.request(plan_id or "", drafts)
        self.calls.append((plan_id or "", list(outcomes.values())))
        return [
            Checkpoint(
                run_id=f"run-{index}",
                topic=step.topic,
                ok=self.ok,
                text=f"执行：{step.topic}",
                output=self.outputs.get(step.topic, "已写入") if self.ok else None,
                error=None if self.ok else self.errors.get(step.topic, "写入失败"),
                error_code=None if self.ok else "failed",
                status="succeeded" if self.ok else "failed",
            )
            for index, step in enumerate(steps, start=1)
        ]


class RejectingOrchestrator(FakeOrchestrator):
    """把整批草案都拒掉，返回 rejected 检查点。"""

    def __init__(self) -> None:
        super().__init__(ok=False)

    def run_plan(
        self,
        plan,
        *,
        gate,
        persona=None,
        source=None,
        plan_id=None,
        read_only=False,
        turn_id=None,
    ):
        steps = list(plan)
        self.plans.append(steps)
        self.read_only_flags.append(read_only)
        self.turn_ids.append(turn_id)
        self.personas.append(persona)
        self.sources.append(source)
        drafts = [
            ApprovalDraft(
                run_id=f"run-{index}",
                topic=step.topic,
                task=step.task,
                content=f"草案：{step.topic}",
                tools=("append",),
            )
            for index, step in enumerate(steps, start=1)
        ]
        gate.request(plan_id or "", drafts)
        return [
            Checkpoint(
                run_id=f"run-{index}",
                topic=step.topic,
                ok=False,
                text=f"执行：{step.topic}",
                output=None,
                error="用户拒绝执行",
                error_code="rejected",
                status="rejected",
            )
            for index, step in enumerate(steps, start=1)
        ]


def backfill_messages(messages) -> list[str]:
    """二次生成里承担回注的 assistant 消息正文。"""
    return [message.content for message in messages if isinstance(message, AIMessage)]


def test_dispatch_runs_the_plan_then_answers_with_chat(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [Raw(DISPATCH), "记好啦。"], orchestrator=orchestrator)
    result = ask(harness.chat, "记一下我喜欢猫")
    assert orchestrator.plans == [[PlanStep("记忆写入", "记下主人喜欢猫")]]
    assert len(harness.fake.calls) == 2
    assert result.text == "记好啦。"
    logged = harness.query("SELECT role, content FROM daily_log ORDER BY id")
    assert [role for role, _ in logged] == ["master", "assistant", "me"]
    assert logged[0] == ("master", "记一下我喜欢猫")
    assert logged[1] == ("assistant", "已写入")
    assert "[finished_plan]" not in logged[1][1]
    assert logged[2] == ("me", "记好啦。")


DSML_DISPATCH = (
    '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="dispatch">'
    '<｜｜DSML｜｜ parameter name="plan" string="false">'
    '[{"topic":"记忆写入","task":"记下主人喜欢猫"}]'
    "</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>"
)


def test_dsml_dispatch_runs_existing_plan_and_gate(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [Raw(DSML_DISPATCH), "记好啦。"], orchestrator=orchestrator)
    result = ask(harness.chat, "记一下我喜欢猫")

    assert orchestrator.plans == [[PlanStep("记忆写入", "记下主人喜欢猫")]]
    assert len(orchestrator.calls) == 1
    assert result.text == "记好啦。"


def test_dsml_dispatch_obeys_disabled_dispatch(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [Raw(DSML_DISPATCH), "好的。"], orchestrator=orchestrator)
    result = harness.chat.chat(ChatRequest(trigger_reason="主动问候", allow_dispatch=False))
    assert result.text == "好的。"
    assert orchestrator.plans == []
    assert len(harness.fake.calls) == 2


def test_dsml_dispatch_obeys_read_only_mode(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [Raw(DSML_DISPATCH), "好的。"], orchestrator=orchestrator)
    harness.chat.chat(ChatRequest(trigger_reason="主动问候", read_only_dispatch=True))
    assert orchestrator.read_only_flags == [True]


def test_dsml_multiple_calls_retry_without_dispatch(tmp_path: Path):
    doubled = DSML_DISPATCH.replace(
        "</｜｜DSML｜｜ calls>",
        DSML_DISPATCH.partition("<｜｜DSML｜｜ calls>")[2],
    )
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [Raw(doubled), "好的。"], orchestrator=orchestrator)
    result = ask(harness.chat, "记一下")
    assert result.text == "好的。"
    assert orchestrator.plans == []
    assert len(harness.fake.calls) == 2


def test_second_dsml_dispatch_in_same_turn_is_rejected(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(
        tmp_path,
        [Raw(DSML_DISPATCH), Raw(DSML_DISPATCH), Raw(DSML_DISPATCH)],
        orchestrator=orchestrator,
    )
    with pytest.raises(ChatError, match="不能再次派发"):
        ask(harness.chat, "记一下我喜欢猫")
    assert len(orchestrator.plans) == 1


def test_dispatch_passes_the_current_turn_id_to_the_orchestrator(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [Raw(DISPATCH), "记好啦。"], orchestrator=orchestrator)
    ask(harness.chat, "记一下我喜欢猫")
    [turn_id] = harness.query("SELECT DISTINCT turn_id FROM llm_call")
    assert turn_id[0]
    assert orchestrator.turn_ids == [turn_id[0]]


def test_dispatch_passes_the_persona_snapshot_and_user_source(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [Raw(DISPATCH), "记好啦。"], orchestrator=orchestrator)
    ask(harness.chat, "记一下我喜欢猫")

    [persona] = orchestrator.personas
    assert persona.id == "芙芙"
    assert persona.version >= 1
    assert orchestrator.sources == [SOURCE_USER]


def test_direct_chat_does_not_dispatch_even_when_an_orchestrator_is_available(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [SHORT], orchestrator=orchestrator)
    assert ask(harness.chat, "你好").text == SHORT
    assert orchestrator.plans == []


def test_multiple_subagent_results_are_merged_into_one_assistant_row(tmp_path: Path):
    orchestrator = FakeOrchestrator(
        outputs={
            "记忆检索": "查到：主人喜欢美式。反馈：可以放心推荐。",
            "记忆写入": "已写入：咖啡偏好",
        }
    )
    plan = json.dumps(
        {
            "type": "dispatch",
            "plan": [
                {"topic": "记忆检索", "task": "找咖啡偏好"},
                {"topic": "记忆写入", "task": "记下今天喝了咖啡"},
            ],
        },
        ensure_ascii=False,
    )
    harness = Harness(tmp_path, [Raw(plan), "都做完了。"], orchestrator=orchestrator)
    ask(harness.chat, "帮我处理一下")

    merged = "查到：主人喜欢美式。反馈：可以放心推荐。\n已写入：咖啡偏好"
    rows = harness.query("SELECT content FROM daily_log WHERE role = 'assistant' ORDER BY id")
    assert rows == [(merged,)]
    assert backfill_messages(harness.fake.calls[1]) == [merged]


def dispatch_harness(tmp_path: Path, body: str, outcomes: list | None = None, clock=None) -> tuple:
    plan = json.dumps(
        {"type": "dispatch", "plan": [{"topic": "记忆检索", "task": "查咖啡偏好"}]},
        ensure_ascii=False,
    )
    orchestrator = FakeOrchestrator(outputs={"记忆检索": body})
    harness = Harness(
        tmp_path,
        outcomes or [Raw(plan), "查到啦。", SHORT],
        orchestrator=orchestrator,
        clock=clock,
    )
    return harness, plan, orchestrator


def test_query_content_without_feedback_is_logged_verbatim(tmp_path: Path):
    body = "查到：主人喜欢美式。"
    harness, _, _ = dispatch_harness(tmp_path, body)
    ask(harness.chat, "查一下我喝什么")

    assert harness.query("SELECT content FROM daily_log WHERE role = 'assistant'") == [(body,)]
    assert backfill_messages(harness.fake.calls[1]) == [body]


def test_empty_query_result_is_backfilled_and_visible_to_the_main_agent(tmp_path: Path):
    body = "没有找到相关记录。"
    harness, _, _ = dispatch_harness(tmp_path, body)
    ask(harness.chat, "查一下我喝什么")

    assert harness.query("SELECT content FROM daily_log WHERE role = 'assistant'") == [(body,)]
    contents = [message.content for message in harness.fake.calls[1]]
    assert any(body in text for text in contents)


def test_backfilled_content_is_restored_in_later_context(tmp_path: Path):
    moment = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    body = "查到：主人喜欢美式。反馈：可以放心推荐。"
    harness, _, _ = dispatch_harness(tmp_path, body, clock=MutableClock(moment))
    ask(harness.chat, "查一下我喝什么")

    restarted = harness.new_chat()
    ask(restarted, "后来呢")

    contents = [message.content for message in harness.fake.calls[2]]
    assert any("[assistant]" in text and body in text for text in contents)


def test_failed_subagent_error_is_logged_without_the_checkpoint_wrapper(tmp_path: Path):
    orchestrator = FakeOrchestrator(ok=False, errors={"记忆写入": "写入失败：磁盘只读"})
    harness = Harness(tmp_path, [Raw(DISPATCH), "没成功。"], orchestrator=orchestrator)
    ask(harness.chat, "记一下我喜欢猫")

    assert harness.query("SELECT content FROM daily_log WHERE role = 'assistant'") == [
        ("写入失败：磁盘只读",)
    ]


def test_retrieval_limit_error_is_backfilled_and_shown_to_the_main_agent(tmp_path: Path):
    reason = "永久记忆读取超过模型上限"
    plan = json.dumps(
        {"type": "dispatch", "plan": [{"topic": "记忆检索", "task": "查咖啡偏好"}]},
        ensure_ascii=False,
    )
    orchestrator = FakeOrchestrator(ok=False, errors={"记忆检索": reason})
    harness = Harness(tmp_path, [Raw(plan), "没查到。"], orchestrator=orchestrator)
    ask(harness.chat, "查一下我喝什么")

    assert harness.query("SELECT content FROM daily_log WHERE role = 'assistant'") == [(reason,)]
    assert backfill_messages(harness.fake.calls[1]) == [reason]


def test_final_chat_failure_keeps_user_input_and_backfill_for_restore(tmp_path: Path):
    body = "查到：主人喜欢美式。"
    plan = json.dumps(
        {"type": "dispatch", "plan": [{"topic": "记忆检索", "task": "查咖啡偏好"}]},
        ensure_ascii=False,
    )
    orchestrator = FakeOrchestrator(outputs={"记忆检索": body})
    harness = Harness(
        tmp_path,
        [Raw(plan), Raw(BROKEN), Raw(BROKEN), SHORT],
        orchestrator=orchestrator,
    )
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "查一下我喝什么")
    assert exc.value.code == "invalid_output"

    rows = harness.query("SELECT role, content FROM daily_log ORDER BY id")
    assert rows == [
        ("master", "查一下我喝什么"),
        ("assistant", body),
    ]

    restarted = harness.new_chat()
    ask(restarted, "后来呢")
    contents = [message.content for message in harness.fake.calls[-1]]
    assert any("[assistant]" in text and body in text for text in contents)
    assert any(text.endswith("]后来呢") for text in contents)


def test_second_generation_receives_the_persisted_backfill_without_plan_structures(
    tmp_path: Path,
):
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [Raw(DISPATCH), "记好啦。"], orchestrator=orchestrator)
    ask(harness.chat, "记一下我喜欢猫")

    assert harness.query("SELECT content FROM daily_log WHERE role = 'assistant'") == [("已写入",)]
    follow_up = harness.fake.calls[1]
    assert backfill_messages(follow_up) == ["已写入"]
    # 系统提示词里的派发规则不算二次生成携带的结构，只看对话消息。
    block = "\n".join(
        str(message.content) for message in follow_up if not isinstance(message, SystemMessage)
    )
    for marker in ("[finished_plan]", "plan_id", '"type": "dispatch"', "检查点"):
        assert marker not in block


def test_merged_backfill_keeps_plan_order(tmp_path: Path):
    orchestrator = FakeOrchestrator(outputs={"记忆检索": "找到了", "记忆写入": "写好了"})
    plan = json.dumps(
        {
            "type": "dispatch",
            "plan": [
                {"topic": "记忆检索", "task": "找咖啡偏好"},
                {"topic": "记忆写入", "task": "记下今天喝了咖啡"},
            ],
        },
        ensure_ascii=False,
    )
    harness = Harness(tmp_path, [Raw(plan), "都做完了。"], orchestrator=orchestrator)
    ask(harness.chat, "帮我处理一下")

    assert harness.query("SELECT content FROM daily_log WHERE role = 'assistant'") == [
        ("找到了\n写好了",)
    ]
    assert backfill_messages(harness.fake.calls[1]) == ["找到了\n写好了"]


def test_failed_checkpoint_reports_the_error_as_the_only_backfill(tmp_path: Path):
    orchestrator = FakeOrchestrator(ok=False)
    harness = Harness(tmp_path, [Raw(DISPATCH), "没成功。"], orchestrator=orchestrator)
    ask(harness.chat, "记一下我喜欢猫")

    assert harness.query("SELECT content FROM daily_log WHERE role = 'assistant'") == [
        ("写入失败",)
    ]
    assert backfill_messages(harness.fake.calls[1]) == ["写入失败"]


def test_second_dispatch_in_the_same_turn_is_rejected(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(
        tmp_path, [Raw(DISPATCH), Raw(DISPATCH), Raw(DISPATCH)], orchestrator=orchestrator
    )
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "记一下我喜欢猫")
    assert exc.value.code == "invalid_output"
    assert "不能再次派发" in str(exc.value)
    assert len(orchestrator.plans) == 1
    assert harness.query("SELECT COUNT(*) FROM daily_log WHERE role = 'me'")[0][0] == 0


def test_rejected_dispatch_writes_an_approval_event(tmp_path: Path):
    orchestrator = RejectingOrchestrator()
    harness = Harness(tmp_path, [Raw(DISPATCH), "那就不记了。", SHORT], orchestrator=orchestrator)
    result = ask(harness.chat, "记一下我喜欢猫")
    assert result.text == "那就不记了。"
    events = harness.query("SELECT content FROM daily_log WHERE role = 'event'")
    assert len(events) == 1
    assert json.loads(events[0][0]) == {
        "type": "approval_rejected",
        "topic": "记忆写入",
        "reason": "用户拒绝执行",
    }

    ask(harness.chat, "再来")
    contents = [message.content for message in harness.fake.calls[2]]
    assert "[assistant][" in contents[2]
    assert contents[2].endswith("]用户拒绝执行")
    assert not any(text.startswith("[事件]") for text in contents)
    assert not any("approval_rejected" in text for text in contents)


def test_rejected_subagent_reason_is_logged_as_the_assistant_body(tmp_path: Path):
    orchestrator = RejectingOrchestrator()
    harness = Harness(tmp_path, [Raw(DISPATCH), "那就不记了。"], orchestrator=orchestrator)
    ask(harness.chat, "记一下我喜欢猫")

    assert harness.query("SELECT content FROM daily_log WHERE role = 'assistant'") == [
        ("用户拒绝执行",)
    ]


def test_invalid_dispatch_writes_an_event_then_regenerates(tmp_path: Path):
    bad = json.dumps({"type": "dispatch", "plan": []}, ensure_ascii=False)
    harness = Harness(tmp_path, [Raw(bad), "我还是直接说吧。", SHORT])
    result = ask(harness.chat, "你好")
    assert result.text == "我还是直接说吧。"
    rows = harness.query("SELECT role, content FROM daily_log ORDER BY id")
    assert rows[0] == ("master", "你好")
    assert rows[1][0] == "event"
    event = json.loads(rows[1][1])
    assert event["type"] == "dispatch_failed"
    assert "1–3" in event["reason"]
    assert rows[2:] == [("me", "我还是直接说吧。")]

    ask(harness.chat, "再说一句")
    contents = [message.content for message in harness.fake.calls[2]]
    assert not any(text.startswith("[事件]") for text in contents)
    assert not any("dispatch_failed" in text for text in contents)


def test_dispatch_without_an_orchestrator_is_a_failure(tmp_path: Path):
    harness = Harness(tmp_path, [Raw(DISPATCH)])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "记一下我喜欢猫")
    assert exc.value.code == "invalid_output"
    assert "执行器未配置" in str(exc.value)


def test_restart_restores_history_without_repeating_the_current_input(tmp_path: Path):
    clock = MutableClock(datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    harness = Harness(tmp_path, [SHORT, "第二句回复。"], clock=clock)
    ask(harness.chat, "第一句")

    restarted = harness.new_chat()
    ask(restarted, "第二句")

    contents = [message.content for message in harness.fake.calls[1][1:]]
    assert contents[0].endswith("]第一句")
    assert contents[1].endswith(f"]{SHORT}")
    assert contents[2] == CURRENT_SEPARATOR
    assert contents[3].endswith("]第二句")
    assert sum(text.endswith("]第二句") for text in contents) == 1


def test_assistant_rows_from_the_same_day_are_restored_with_their_role(tmp_path: Path):
    moment = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    harness = Harness(tmp_path, [SHORT], clock=MutableClock(moment))
    harness.daily.record_user("查一下咖啡", at=moment, persona_id="芙芙")
    harness.daily.record_assistant("查到喜欢美式", at=moment, persona_id="芙芙")
    harness.daily.record_reply("记下啦", at=moment, persona_id="芙芙")

    ask(harness.chat, "你好")

    contents = [message.content for message in harness.fake.calls[0]]
    assert any("[assistant]" in text and "查到喜欢美式" in text for text in contents)
    assert any("[me]" in text and "记下啦" in text for text in contents)


BUDGET_YAML = LLM_YAML.replace(
    "max_tokens: 1024",
    "max_tokens: 64\n        context_window: 100",
)


class FixedCounter:
    def __init__(self, tokens: int, estimated: bool = False) -> None:
        self.tokens = tokens
        self.estimated = estimated

    def count(self, messages) -> TokenCount:
        return TokenCount(self.tokens, estimated=self.estimated)


class BrokenCounter:
    def count(self, messages) -> TokenCount:
        raise TokenCountError("分词器不可用")


def budget_harness(tmp_path: Path, counter, outcomes: list | None = None) -> Harness:
    return Harness(
        tmp_path,
        outcomes or [SHORT],
        llm_yaml=BUDGET_YAML,
        counter_factory=lambda encoding: counter,
    )


def test_context_window_is_checked_before_calling_the_model(tmp_path: Path):
    harness = budget_harness(tmp_path, FixedCounter(37))
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "context_overflow"
    assert "37" in str(exc.value)
    assert "64" in str(exc.value)
    assert "100" in str(exc.value)
    assert harness.fake.calls == []
    assert harness.query("SELECT role, content FROM daily_log") == [("master", "你好")]


def test_exact_window_boundary_is_allowed(tmp_path: Path):
    harness = budget_harness(tmp_path, FixedCounter(36))
    assert ask(harness.chat, "你好").text == SHORT
    assert len(harness.fake.calls) == 1


def test_estimated_counts_are_labelled_in_the_error(tmp_path: Path):
    harness = budget_harness(tmp_path, FixedCounter(200, estimated=True))
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "context_overflow"
    assert "估算" in str(exc.value)


def test_token_count_failure_is_reported_without_calling_the_model(tmp_path: Path):
    harness = budget_harness(tmp_path, BrokenCounter())
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "token_count_failed"
    assert "分词器不可用" in str(exc.value)
    assert harness.fake.calls == []
    assert harness.query("SELECT role, content FROM daily_log") == [("master", "你好")]


def test_upstream_context_overflow_is_mapped_to_a_clear_error(tmp_path: Path):
    harness = Harness(
        tmp_path,
        [RuntimeError("This model's maximum context length is 100 tokens")],
    )
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "context_overflow"
    assert "maximum context length" in str(exc.value)
    assert harness.query("SELECT role, content FROM daily_log") == [("master", "你好")]


# ---------- T-10：一轮对话的时序与跨零点 ----------

RECALL_TEXT = "昨天主人说想去海边走走。"
TODAY_TEXT = "今天有点困"


class Trace:
    """记录一轮对话里关键动作的先后顺序。"""

    def __init__(self) -> None:
        self.names: list[str] = []

    def add(self, name: str) -> None:
        self.names.append(name)


class TracedDaily:
    """只给 ChatService 用的日志代理：记录写入用户输入的时机。"""

    def __init__(self, daily: DailyLogService, trace: Trace) -> None:
        self._daily = daily
        self._trace = trace

    def __getattr__(self, name: str):
        return getattr(self._daily, name)

    def record_user(self, *args, **kwargs):
        self._trace.add("record_user")
        return self._daily.record_user(*args, **kwargs)


class FakeRecollection:
    """替身回忆服务：按日期记账，并把结果真正写进日志。"""

    def __init__(
        self,
        daily: DailyLogService | None = None,
        trace: Trace | None = None,
        *,
        ok: bool = True,
        content: str = RECALL_TEXT,
        on_ensure=None,
    ) -> None:
        self._daily = daily
        self._trace = trace
        self._ok = ok
        self._content = content
        self._on_ensure = on_ensure
        self.days: list[str] = []

    def bind(self, daily: DailyLogService, trace: Trace | None = None) -> None:
        """Harness 会补上日志服务与踪迹，方便先造替身再交给 Harness。"""
        self._daily = daily
        if trace is not None:
            self._trace = trace

    def ensure(self, day, *, persona, turn_id=None):
        target = (day - timedelta(days=1)).isoformat()
        self.days.append(target)
        if self._trace is not None:
            self._trace.add("recollection")
        body = self._content if self._ok else f"{FAILURE_PREFIX}子代理超时"
        self._daily.record_recollection(target, body, persona_id=persona.id)
        if self._on_ensure is not None:
            self._on_ensure(target)
        return Recollection(target, body, self._ok)


def contents_of(call: list) -> list[str]:
    return [message.content for message in call]


def test_input_is_logged_before_the_recollection_is_awaited(tmp_path: Path):
    clock = MutableClock(datetime(2026, 10, 18, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    trace = Trace()
    harness = Harness(tmp_path, [SHORT], clock=clock, trace=trace, recollection=FakeRecollection)
    ask(harness.chat, TODAY_TEXT)
    assert trace.names == ["record_user", "recollection", "generate"]
    system = harness.fake.calls[0][0].content
    assert f"[assistant][10:00:00]{RECALL_TEXT}" in system


def test_logged_input_still_appears_only_once_in_the_prompt(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT, SHORT])
    ask(harness.chat, "第一句")
    ask(harness.chat, "第二句")
    contents = contents_of(harness.fake.calls[1])
    assert sum(text.count("第二句") for text in contents) == 1
    assert sum(text.count("第一句") for text in contents) == 1
    assert len([text for text in contents if text.startswith("[master][")]) == 2
    assert harness.query("SELECT role, content FROM daily_log ORDER BY id") == [
        ("master", "第一句"),
        ("me", SHORT),
        ("master", "第二句"),
        ("me", SHORT),
    ]


def test_waiting_across_midnight_recalls_both_days_before_replying(tmp_path: Path):
    clock = MutableClock(datetime(2026, 10, 18, 23, 59, 30, tzinfo=ZoneInfo("Asia/Shanghai")))

    def advance(target: str) -> None:
        if target == "2026-10-17":
            clock.moment = datetime(2026, 10, 19, 0, 0, 5, tzinfo=ZoneInfo("Asia/Shanghai"))

    trace = Trace()
    recall = FakeRecollection(on_ensure=advance)
    harness = Harness(tmp_path, [SHORT], clock=clock, trace=trace, recollection=recall)
    ask(harness.chat, TODAY_TEXT)
    assert recall.days == ["2026-10-17", "2026-10-18"]
    assert trace.names == ["record_user", "recollection", "recollection", "generate"]
    assert f"[assistant][00:00:05]{RECALL_TEXT}" in harness.fake.calls[0][0].content


def test_unavailable_model_keeps_the_input_once(tmp_path: Path):
    trace = Trace()
    harness = Harness(tmp_path, [], keys={}, trace=trace, recollection=FakeRecollection)
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, TODAY_TEXT)
    assert exc.value.code == "model_unavailable"
    assert trace.names == ["record_user", "recollection"]
    assert harness.query("SELECT role, content FROM daily_log WHERE role = 'master'") == [
        ("master", TODAY_TEXT)
    ]


def test_failed_recollection_is_not_injected_and_loses_nothing(tmp_path: Path):
    harness = Harness(
        tmp_path,
        [SHORT],
        recollection=lambda daily, trace: FakeRecollection(daily, trace, ok=False),
    )
    ask(harness.chat, TODAY_TEXT)
    contents = contents_of(harness.fake.calls[0])
    assert "[assistant][" not in contents[0]
    assert sum(text.count(TODAY_TEXT) for text in contents) == 1
    assert harness.query("SELECT role, content FROM daily_log ORDER BY id") == [
        ("master", TODAY_TEXT),
        ("回忆", f"{FAILURE_PREFIX}子代理超时"),
        ("me", SHORT),
    ]


def test_record_failure_stops_the_turn_before_calling_the_model(tmp_path: Path, monkeypatch):
    """落库是回复的必要条件：失败即 record_failed，不调模型、不回复（D-21）。"""
    harness = Harness(tmp_path, [SHORT])

    def explode(text, *, at=None, persona_id=None):
        raise OSError("磁盘满了")

    monkeypatch.setattr(harness.daily, "record_user", explode)
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "record_failed"
    assert harness.fake.calls == []
    assert harness.query("SELECT role FROM daily_log") == []


def test_second_query_while_a_turn_is_running_gets_session_busy(tmp_path: Path):
    """session 占位期间第二个被动 query 直接拒绝、不排队（D-22）。"""
    entered = threading.Event()
    release = threading.Event()

    class BlockingChat(FakeChat):
        def invoke(self, messages, config=None, **kwargs):
            if not self.calls:
                entered.set()
                assert release.wait(timeout=5)
            return super().invoke(messages, config=config, **kwargs)

    harness = Harness(tmp_path, [SHORT, SHORT])
    harness.fake = BlockingChat([SHORT, SHORT])
    harness.chat = harness.new_chat()

    first: dict[str, object] = {}

    def run_first() -> None:
        try:
            first["reply"] = ask(harness.chat, "第一句")
        except Exception as exc:  # pragma: no cover - 失败时给主线程断言用
            first["error"] = exc

    worker = threading.Thread(target=run_first)
    worker.start()
    try:
        assert entered.wait(timeout=5)
        with pytest.raises(ChatError) as exc:
            ask(harness.chat, "第二句")
        assert exc.value.code == "session_busy"
    finally:
        release.set()
        worker.join(timeout=5)
    assert first.get("reply") is not None
    assert harness.query("SELECT role, content FROM daily_log ORDER BY id") == [
        ("master", "第一句"),
        ("me", SHORT),
    ]


def test_proactive_turn_is_not_blocked_by_a_running_query_slot(tmp_path: Path):
    """主动轮不绑定用户输入，不占位（Q-16）。"""
    harness = Harness(tmp_path, [SHORT, SHORT])
    harness.chat._turn_slot.acquire()
    try:
        reply = harness.chat.chat(ChatRequest(trigger_reason="使用 code 已 3 分钟"))
    finally:
        harness.chat._turn_slot.release()
    assert reply.text == SHORT


def test_consecutive_identical_queries_are_deduped_only_in_model_context(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT, SHORT])
    ask(harness.chat, "在吗")
    ask(harness.chat, "在吗")

    contents = contents_of(harness.fake.calls[1])
    assert len([text for text in contents if text.endswith("]在吗")]) == 1
    assert harness.query("SELECT role, content FROM daily_log ORDER BY id") == [
        ("master", "在吗"),
        ("me", SHORT),
        ("master", "在吗"),
        ("me", SHORT),
    ]


def test_a_b_a_keeps_both_identical_queries_in_context(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT, SHORT, SHORT])
    ask(harness.chat, "A")
    ask(harness.chat, "B")
    ask(harness.chat, "A")

    contents = contents_of(harness.fake.calls[2])
    assert len([text for text in contents if text.endswith("]A")]) == 2


def test_whole_turn_keeps_the_received_day_across_midnight(tmp_path: Path):
    """处理过程跨零点也固定使用 received_at 的日期（D-07）。"""
    clock = MutableClock(datetime(2026, 10, 18, 23, 59, 58, tzinfo=ZoneInfo("Asia/Shanghai")))
    harness = Harness(tmp_path, [SHORT], clock=clock)
    original = harness.fake.invoke

    def advance(messages, config=None, **kwargs):
        clock.moment = datetime(2026, 10, 19, 0, 0, 3, tzinfo=ZoneInfo("Asia/Shanghai"))
        return original(messages, config=config, **kwargs)

    harness.fake.invoke = advance  # type: ignore[method-assign]
    ask(harness.chat, "还没睡")

    prompt = harness.fake.calls[0][0]
    assert "2026/10/18" in prompt.content
    assert "2026/10/19" not in prompt.content
    assert harness.fake.calls[0][-1].content == "[master][23:59:58]还没睡"
    rows = harness.query("SELECT role, substr(event_time, 1, 10) FROM daily_log ORDER BY id")
    assert rows == [("master", "2026-10-18"), ("me", "2026-10-19")]
