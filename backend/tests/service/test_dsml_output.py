import sys

import pytest

from module.agent.chat_output import DispatchRejectedError, ModelOutputError, parse_chat_output
from module.agent.plan import PlanStep
from module.agent.subagent import parse_action

MARK = "｜｜DSML｜｜"


def dsml_invoke(name: str, parameters: str, mark: str = MARK) -> str:
    return f'<{mark} invoke name="{name}">{parameters}</{mark} invoke>'


def dsml_param(name: str, value: str, string: str = "true", mark: str = MARK) -> str:
    return f'<{mark} parameter name="{name}" string="{string}">{value}</{mark} parameter>'


def dsml_calls(invocations: str, mark: str = MARK) -> str:
    return f"<{mark} calls>\n{invocations}\n</{mark} calls>"


PLAN = (
    '[{"topic": "记录主人喜好", "task": "在结构化记忆中新增或更新一条关于主人的信息：'
    '主人喜欢玩《我的世界》(MC)，喜欢挖矿和盖房子类的玩法。只写这一条，不要改动其他条目。"}]'
)
DISPATCH = dsml_calls(dsml_invoke("dispatch", dsml_param("plan", PLAN, "false")))
TOOL = dsml_calls(
    dsml_invoke(
        "tool",
        dsml_param("tool", "echo") + dsml_param("args", '{"text":"你好"}', "false"),
    )
)


def test_dsml_dispatch_user_sample_is_mapped_to_plan():
    output = parse_chat_output(DISPATCH)
    assert output.kind == "dispatch"
    assert output.plan == (
        PlanStep(
            "记录主人喜好",
            "在结构化记忆中新增或更新一条关于主人的信息：主人喜欢玩《我的世界》(MC)，"
            "喜欢挖矿和盖房子类的玩法。只写这一条，不要改动其他条目。",
        ),
    )


def test_dsml_dispatch_reuses_plan_validation():
    invalid = dsml_calls(dsml_invoke("dispatch", dsml_param("plan", "[]", "false")))
    with pytest.raises(DispatchRejectedError, match="1–3"):
        parse_chat_output(invalid)


def test_dsml_dispatch_rejects_non_dispatch_or_extra_parameters():
    with pytest.raises(ModelOutputError):
        parse_chat_output(TOOL)
    extra = dsml_calls(
        dsml_invoke("dispatch", dsml_param("plan", PLAN, "false") + dsml_param("extra", "x"))
    )
    with pytest.raises(ModelOutputError):
        parse_chat_output(extra)


def test_dsml_generic_tool_action_is_mapped():
    assert parse_action(TOOL) == {"action": "tool", "tool": "echo", "args": {"text": "你好"}}


def test_dsml_direct_tool_parameters_keep_types_and_raw_text():
    params = dsml_param("text", '你好 <世界> & "朋友"\n第二行') + dsml_param(
        "options", '{"enabled":true,"count":2,"items":[1,null]}', "false"
    )
    output = parse_action(dsml_calls(dsml_invoke("echo", params)))
    assert output == {
        "action": "tool",
        "tool": "echo",
        "args": {
            "text": '你好 <世界> & "朋友"\n第二行',
            "options": {"enabled": True, "count": 2, "items": [1, None]},
        },
    }


def test_dsml_accepts_ascii_marker_and_reordered_attributes():
    mark = "|DSML|"
    param = f'<{mark} parameter string="true" name="text">hello</{mark} parameter>'
    assert parse_action(dsml_calls(dsml_invoke("echo", param, mark), mark)) == {
        "action": "tool",
        "tool": "echo",
        "args": {"text": "hello"},
    }


@pytest.mark.parametrize(
    "content",
    [
        dsml_calls(dsml_invoke("echo", dsml_param("text", "one")) * 2),
        TOOL + TOOL,
        TOOL.replace(f"</{MARK} parameter>", f"</{MARK} invoke>", 1),
        TOOL.replace(f"</{MARK} calls>", ""),
        TOOL.replace('string="true"', 'string="maybe"', 1),
        TOOL.replace('{"text":"你好"}', '{"text":}'),
        TOOL.replace('{"text":"你好"}', '{"text":"one","text":"two"}'),
        TOOL.replace('{"text":"你好"}', '{"text":NaN}'),
        TOOL.replace(f"</{MARK} calls>", "</|DSML| calls>"),
        TOOL + "额外文本",
        f'<{MARK} calls>{{"action":"finish","output":"不应执行"}}',
        dsml_calls(
            dsml_invoke(
                "unknown", dsml_param("args", '{"action":"finish","output":"不应执行"}', "false")
            )
        ).replace(f"</{MARK} invoke>", ""),
    ],
)
def test_dsml_invalid_or_ambiguous_never_falls_back(content):
    with pytest.raises(ModelOutputError):
        parse_chat_output(content)
    assert parse_action(content) is None


def test_dsml_duplicate_parameters_are_rejected():
    content = dsml_calls(dsml_invoke("echo", dsml_param("text", "one") + dsml_param("text", "two")))
    assert parse_action(content) is None
    with pytest.raises(ModelOutputError):
        parse_chat_output(content)


@pytest.mark.parametrize("kind", ["integer", "nesting"])
def test_dsml_extreme_json_is_rejected_without_uncaught_exception(kind):
    depth = sys.getrecursionlimit() * 4
    value = "9" * 5000 if kind == "integer" else "[" * depth + "0" + "]" * depth
    content = dsml_calls(dsml_invoke("echo", dsml_param("text", value, "false")))
    with pytest.raises(ModelOutputError):
        parse_chat_output(content)
    assert parse_action(content) is None


def test_dsml_unknown_call_cannot_extract_nested_action():
    content = dsml_calls(
        dsml_invoke(
            "unknown", dsml_param("args", '{"action":"finish","output":"不应执行"}', "false")
        )
    )
    assert parse_action(content) == {
        "action": "tool",
        "tool": "unknown",
        "args": {"args": {"action": "finish", "output": "不应执行"}},
    }


def test_dsml_tool_action_requires_valid_envelope():
    invalid = dsml_calls(dsml_invoke("tool", dsml_param("tool", "echo")))
    assert parse_action(invalid) is None


def test_dsml_marker_inside_ordinary_json_text_is_preserved():
    content = '{"type":"chat","text":["DSML 是一种标记。"]}'
    assert parse_chat_output(content).text == "DSML 是一种标记。"
