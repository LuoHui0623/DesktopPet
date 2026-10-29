import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from config.settings import ModelSettingsSource
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.approval import ApprovalDraft
from module.agent.orchestrator import Checkpoint, PlanStep
from module.agent.prompt import load_templates
from module.chat.chat_service import (
    ChatError,
    ChatRequest,
    ChatService,
    DispatchRejectedError,
    ModelOutputError,
    overlong_sentences,
    parse_chat_output,
    split_sentences,
)
from module.chat.context import KIND_DAILY, KIND_STRUCTURED, MemoryFragment
from module.interaction.interaction_service import InteractionService
from module.llm import LlmService
from module.llm.repository import db_usage_sink
from module.memory.daily_log_service import DailyLogService
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


class Raw(str):
    """模型原样输出的内容，用于验证协议错误。"""


def chat_output(text: str) -> str:
    return json.dumps({"type": "chat", "text": text}, ensure_ascii=False)


def ask(chat: ChatService, text: str, fragments=()):
    return chat.chat(ChatRequest(user_text=text, fragments=fragments))


class FakeChat:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = [
            outcome if isinstance(outcome, (Exception, Raw)) else chat_output(outcome)
            for outcome in outcomes
        ]
        self.calls: list[list] = []

    def invoke(self, messages, config=None, **kwargs):
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
    ):
        self.db_file = tmp_path / "pet.db"
        conn = connect(self.db_file)
        migrate(conn)
        conn.close()
        (tmp_path / ".env").write_text(env, encoding="utf-8")
        (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
        environ = {"DEEPSEEK_API_KEY": "sk-test"} if keys is None else keys
        self.fake = FakeChat(outcomes)
        self.llm = LlmService(
            ModelSettingsSource(tmp_path, environ=environ),
            builder=lambda spec: self.fake,
            environ=environ,
            usage_sink=db_usage_sink(self.db_file),
        )
        self.llm.start()
        self.daily = DailyLogService(
            DbDailyLog(self.db_file), SettingsService(self.db_file), ZoneInfo("Asia/Shanghai")
        )
        self.chat = ChatService(
            self.llm,
            self.daily,
            persona_service(tmp_path, self.db_file),
            load_templates(),
            db_usage_sink(self.db_file),
            events,
            timezone=ZoneInfo("Asia/Shanghai"),
            clock=clock,
            orchestrator=orchestrator,
            interactions=interactions,
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
    usage = harness.query("SELECT purpose, success, tokens_in, prompt_hash FROM llm_usage")
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


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_interactions_are_injected_once_without_being_persisted(tmp_path: Path):
    clock = FakeClock(datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    interactions = InteractionService(clock=clock)
    harness = Harness(tmp_path, [SHORT, SHORT], interactions=interactions)
    interactions.record("click")

    ask(harness.chat, "你好")

    last = harness.fake.calls[0][-1]
    assert isinstance(last, HumanMessage)
    assert last.content == "[master] 单击宠物\n\n你好"
    assert harness.query("SELECT role, content FROM daily_log ORDER BY id") == [
        ("master", "你好"),
        ("me", SHORT),
    ]

    ask(harness.chat, "在吗")
    assert harness.fake.calls[1][-1].content == "在吗"


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
    assert harness.query("SELECT COUNT(*) FROM llm_usage")[0][0] == 2


def test_overlong_twice_returns_reason_not_truncated_text(tmp_path: Path):
    harness = Harness(tmp_path, [LONG, LONG])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "sentence_too_long"
    assert "40" in str(exc.value)
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


def test_missing_key_is_clear_error_and_records_failure(tmp_path: Path):
    harness = Harness(tmp_path, [], keys={})
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "model_unavailable"
    assert "DEEPSEEK_API_KEY" in str(exc.value)
    usage = harness.query("SELECT provider, model, success FROM llm_usage")
    assert usage == [("deepseek", "deepseek-flash", 0)]
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


def test_unconfigured_chat_model_is_clear_error_and_records_failure(tmp_path: Path):
    env = ENV.replace("DEFAULT_CHAT_MODEL=deepseek-flash\n", "")
    harness = Harness(tmp_path, [], env=env)
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "model_unavailable"
    assert harness.query("SELECT provider, success FROM llm_usage") == [("-", 0)]


def test_model_failure_is_reported_without_fabricated_reply(tmp_path: Path):
    harness = Harness(tmp_path, [RuntimeError("upstream down")])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "model_failed"
    assert "upstream down" in str(exc.value)
    assert harness.query("SELECT success FROM llm_usage") == [(0,)]
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


def test_empty_model_reply_is_a_failure(tmp_path: Path):
    harness = Harness(tmp_path, [Raw("   "), Raw("   ")])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好")
    assert exc.value.code == "invalid_output"
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


def test_non_json_reply_is_regenerated_once(tmp_path: Path):
    harness = Harness(tmp_path, [Raw(SHORT), Raw(chat_output(SHORT))])
    result = ask(harness.chat, "你好")
    assert result.text == SHORT
    assert len(harness.fake.calls) == 2


def test_non_json_twice_returns_reason(tmp_path: Path):
    harness = Harness(tmp_path, [Raw(SHORT), Raw(SHORT)])
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
            {"type": "dispatch", "plan": [{"capability": "记忆写入", "task": "记下主人喜欢猫"}]},
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
                {"type": "dispatch", "plan": [{"capability": "记忆写入", "task": "记下"}] * 4},
                ensure_ascii=False,
            )
        )


def test_dispatch_accepts_only_type_and_plan():
    with pytest.raises(DispatchRejectedError, match="两个字段"):
        parse_chat_output(
            json.dumps(
                {
                    "type": "dispatch",
                    "plan": [{"capability": "记忆写入", "task": "记下"}],
                    "extra": "x",
                },
                ensure_ascii=False,
            )
        )


def test_dispatch_step_fields_are_validated():
    with pytest.raises(DispatchRejectedError, match="缺少任务描述"):
        parse_chat_output(
            json.dumps(
                {"type": "dispatch", "plan": [{"capability": "记忆写入"}]},
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


def test_temporary_mode_replies_without_writing_log(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT])
    harness.daily.set_temporary_mode(True)
    result = ask(harness.chat, "你好")
    assert result.text == SHORT
    assert result.recorded is False
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


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
    assert "现在主动说一句话" in input_message.content


def test_proactive_reply_enters_the_next_context_as_a_reply(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT, "你好呀。"])
    harness.chat.chat(ChatRequest(trigger_reason="深夜仍在使用电脑", allow_dispatch=False))
    ask(harness.chat, "你在干嘛")

    contents = [message.content for message in harness.fake.calls[1]]
    assert chat_output(SHORT) in contents
    assert "【本次触发】深夜仍在使用电脑" not in contents


def test_proactive_request_can_disable_dispatch(tmp_path: Path):
    harness = Harness(tmp_path, [Raw(DISPATCH), Raw(DISPATCH)])
    with pytest.raises(ChatError) as exc:
        harness.chat.chat(ChatRequest(trigger_reason="使用 code 已 3 分钟", allow_dispatch=False))
    assert exc.value.code == "invalid_output"
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


def test_chat_requires_user_text_or_trigger_reason(tmp_path: Path):
    harness = Harness(tmp_path, [])
    with pytest.raises(ChatError) as exc:
        harness.chat.chat(ChatRequest())
    assert exc.value.code == "empty_input"
    assert harness.fake.calls == []


def test_split_sentences_uses_all_boundaries():
    text = "你好。我在！真的吗？好的……嗯\n最后一句"
    assert split_sentences(text) == ["你好。", "我在！", "真的吗？", "好的……", "嗯", "最后一句"]


def test_sentence_length_counts_punctuation():
    assert overlong_sentences("a" * 39 + "。") == []
    assert overlong_sentences("a" * 40 + "。") == ["a" * 40 + "。"]
    assert overlong_sentences("短句一。" + "字" * 41) == ["字" * 41]


class RecordingEvents:
    def __init__(self) -> None:
        self.log: list[tuple] = []

    def status(self, state):
        self.log.append(("status", state))

    def chunk(self, message_id, text):
        self.log.append(("chunk", message_id, text))

    def done(self, message_id, text, sources):
        self.log.append(("done", message_id, text, list(sources)))


def test_events_follow_thinking_speaking_chunks_done_idle(tmp_path: Path):
    events = RecordingEvents()
    harness = Harness(tmp_path, ["第一句。第二句！"], events=events)
    result = ask(harness.chat, "你好")
    kinds = [entry[0] for entry in events.log]
    assert kinds == ["status", "status", "chunk", "chunk", "done", "status"]
    assert events.log[0] == ("status", "thinking")
    assert events.log[1] == ("status", "speaking")
    assert events.log[-1] == ("status", "idle")
    assert events.log[4] == ("done", result.message_id, "第一句。第二句！", [])


def test_chunks_join_back_to_the_reply(tmp_path: Path):
    events = RecordingEvents()
    reply = "你好。\n今天\n\n天气不错……真的！"
    harness = Harness(tmp_path, [reply], events=events)
    ask(harness.chat, "你好")
    chunks = [entry[2] for entry in events.log if entry[0] == "chunk"]
    assert "".join(chunks) == reply


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
    assert contents[0] == "第一句"
    assert contents[1] == json.dumps({"type": "chat", "text": SHORT}, ensure_ascii=False)
    assert contents[-1] == "第二句"


class MutableClock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


def test_context_ignores_daily_log_rows_written_outside_the_session(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT])
    harness.daily.record_turn("外部记录", "外部回复")

    ask(harness.chat, "你好")

    contents = [message.content for message in harness.fake.calls[0]]
    assert "外部记录" not in contents
    assert "外部回复" not in contents


def test_day_change_starts_a_new_snapshot(tmp_path: Path):
    clock = MutableClock(datetime(2026, 10, 18, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    harness = Harness(tmp_path, [SHORT, SHORT], clock=clock)
    ask(harness.chat, "第一句")

    clock.moment = datetime(2026, 10, 19, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    ask(harness.chat, "第二句")

    contents = [message.content for message in harness.fake.calls[1][1:]]
    assert contents == ["第二句"]


def test_temporary_mode_turns_stay_out_of_the_snapshot(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT, SHORT])
    harness.daily.set_temporary_mode(True)
    ask(harness.chat, "临时")

    harness.daily.set_temporary_mode(False)
    ask(harness.chat, "正式")

    contents = [message.content for message in harness.fake.calls[1][1:]]
    assert contents == ["正式"]


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
    assert [source.id for source in done[3]] == ["2026-10-18", "2026-10-17"]
    assert harness.query("SELECT content FROM daily_log WHERE role = 'me'") == [(result.text,)]


def test_injected_fragments_carry_their_dates_and_the_citation_rule(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT])
    ask(harness.chat, "你好", CITED_FRAGMENTS)
    system = harness.fake.calls[0][0].content
    assert "[结构化记忆 · 2026-10-18] 喜欢美式咖啡，不加糖" in system
    assert "[每日对话 · 2026-10-17] 昨天聊了茶" in system
    assert "[引用 日期]" in system


def test_reply_with_only_citation_markers_is_a_failure(tmp_path: Path):
    marker = "[引用 2026-10-18]"
    harness = Harness(tmp_path, [marker, marker])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "你好", CITED_FRAGMENTS)
    assert exc.value.code == "invalid_output"
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


DISPATCH = json.dumps(
    {"type": "dispatch", "plan": [{"capability": "记忆写入", "task": "记下主人喜欢猫"}]},
    ensure_ascii=False,
)


class FakeOrchestrator:
    def __init__(self, ok: bool = True) -> None:
        self.plans: list[list[PlanStep]] = []
        self.ok = ok
        self.calls: list[tuple[str, list]] = []

    def run_plan(self, plan, *, gate, plan_id=None) -> list[Checkpoint]:
        steps = list(plan)
        self.plans.append(steps)
        drafts = [
            ApprovalDraft(
                run_id=f"run-{index}",
                capability=step.capability,
                task=step.task,
                content=f"草案：{step.capability}",
                tools=("memory_append",),
            )
            for index, step in enumerate(steps, start=1)
        ]
        outcomes = gate.request(plan_id or "", drafts)
        self.calls.append((plan_id or "", list(outcomes.values())))
        return [
            Checkpoint(
                run_id=f"run-{index}",
                capability=step.capability,
                ok=self.ok,
                text=f"执行：{step.capability}",
                output="已写入" if self.ok else None,
                error=None if self.ok else "写入失败",
                status="succeeded" if self.ok else "failed",
            )
            for index, step in enumerate(steps, start=1)
        ]


class RejectingOrchestrator(FakeOrchestrator):
    """把整批草案都拒掉，返回 rejected 检查点。"""

    def __init__(self) -> None:
        super().__init__(ok=False)

    def run_plan(self, plan, *, gate, plan_id=None) -> list[Checkpoint]:
        steps = list(plan)
        self.plans.append(steps)
        drafts = [
            ApprovalDraft(
                run_id=f"run-{index}",
                capability=step.capability,
                task=step.task,
                content=f"草案：{step.capability}",
                tools=("memory_append",),
            )
            for index, step in enumerate(steps, start=1)
        ]
        gate.request(plan_id or "", drafts)
        return [
            Checkpoint(
                run_id=f"run-{index}",
                capability=step.capability,
                ok=False,
                text=f"执行：{step.capability}",
                output=None,
                error="用户拒绝执行",
                status="rejected",
            )
            for index, step in enumerate(steps, start=1)
        ]


def checkpoint_payloads(messages) -> list[dict]:
    payloads: list[dict] = []
    for message in messages:
        content = message.content
        if not isinstance(content, str):
            continue
        for line in content.splitlines():
            if line.startswith("[finished_plan]"):
                payloads.append(json.loads(line[len("[finished_plan]") :]))
    return payloads


def test_dispatch_runs_the_plan_then_answers_with_chat(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [Raw(DISPATCH), "记好啦。"], orchestrator=orchestrator)
    result = ask(harness.chat, "记一下我喜欢猫")
    assert orchestrator.plans == [[PlanStep("记忆写入", "记下主人喜欢猫")]]
    assert len(harness.fake.calls) == 2
    assert result.text == "记好啦。"
    logged = harness.query("SELECT role, content FROM daily_log ORDER BY id")
    assert logged == [("master", "记一下我喜欢猫"), ("me", "记好啦。")]


def test_checkpoints_are_injected_into_the_second_call(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    harness = Harness(tmp_path, [Raw(DISPATCH), "记好啦。"], orchestrator=orchestrator)
    ask(harness.chat, "记一下我喜欢猫")
    payloads = checkpoint_payloads(harness.fake.calls[1])
    assert len(payloads) == 1
    assert payloads[0]["capability"] == "记忆写入"
    assert payloads[0]["status"] == "succeeded"
    assert payloads[0]["output"] == "已写入"
    assert payloads[0]["plan_id"]


def test_checkpoints_share_one_plan_id_and_keep_plan_order(tmp_path: Path):
    orchestrator = FakeOrchestrator()
    plan = json.dumps(
        {
            "type": "dispatch",
            "plan": [
                {"capability": "记忆检索", "task": "找咖啡偏好"},
                {"capability": "记忆写入", "task": "记下今天喝了咖啡"},
            ],
        },
        ensure_ascii=False,
    )
    harness = Harness(tmp_path, [Raw(plan), "都做完了。"], orchestrator=orchestrator)
    ask(harness.chat, "帮我处理一下")
    payloads = checkpoint_payloads(harness.fake.calls[1])
    assert [payload["capability"] for payload in payloads] == ["记忆检索", "记忆写入"]
    assert len({payload["plan_id"] for payload in payloads}) == 1


def test_failed_checkpoint_reports_the_error_as_output(tmp_path: Path):
    orchestrator = FakeOrchestrator(ok=False)
    harness = Harness(tmp_path, [Raw(DISPATCH), "没成功。"], orchestrator=orchestrator)
    ask(harness.chat, "记一下我喜欢猫")
    payload = checkpoint_payloads(harness.fake.calls[1])[0]
    assert payload["status"] == "failed"
    assert payload["output"] == "写入失败"


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
        "capability": "记忆写入",
        "reason": "用户拒绝执行",
    }

    ask(harness.chat, "再来")
    contents = [message.content for message in harness.fake.calls[2]]
    assert "[事件] 审批被拒绝：记忆写入，用户拒绝执行" in contents


def test_invalid_dispatch_writes_an_event_then_regenerates(tmp_path: Path):
    bad = json.dumps({"type": "dispatch", "plan": []}, ensure_ascii=False)
    harness = Harness(tmp_path, [Raw(bad), "我还是直接说吧。", SHORT])
    result = ask(harness.chat, "你好")
    assert result.text == "我还是直接说吧。"
    rows = harness.query("SELECT role, content FROM daily_log ORDER BY id")
    assert rows[0][0] == "event"
    event = json.loads(rows[0][1])
    assert event["type"] == "dispatch_failed"
    assert "1–3" in event["reason"]
    assert rows[1:] == [("master", "你好"), ("me", "我还是直接说吧。")]

    ask(harness.chat, "再说一句")
    contents = [message.content for message in harness.fake.calls[2]]
    assert any(text.startswith("[事件] 派发失败：") for text in contents)


def test_dispatch_without_an_orchestrator_is_a_failure(tmp_path: Path):
    harness = Harness(tmp_path, [Raw(DISPATCH)])
    with pytest.raises(ChatError) as exc:
        ask(harness.chat, "记一下我喜欢猫")
    assert exc.value.code == "invalid_output"
    assert "执行器未配置" in str(exc.value)
