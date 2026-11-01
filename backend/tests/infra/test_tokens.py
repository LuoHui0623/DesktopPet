import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from infra.llm.tokens import (
    FallbackCounter,
    HeuristicCounter,
    TiktokenCounter,
    TokenCount,
    TokenCountError,
    create_counter,
)

MESSAGES = [
    SystemMessage(content="你是桌宠。"),
    HumanMessage(content="你好，今天过得怎么样？"),
    AIMessage(content="还不错，谢谢你。"),
]


def test_tiktoken_counter_is_positive_and_not_marked_estimated():
    counted = TiktokenCounter("cl100k_base").count(MESSAGES)
    assert counted.tokens > 0
    assert counted.estimated is False


def test_unknown_encoding_falls_back_to_an_estimated_count():
    counted = create_counter("not-a-real-encoding").count(MESSAGES)
    assert counted.tokens > 0
    assert counted.estimated is True


def test_heuristic_counter_is_always_marked_estimated():
    counted = HeuristicCounter().count(MESSAGES)
    assert counted.tokens > 0
    assert counted.estimated is True


def test_more_text_produces_more_estimated_tokens():
    short = HeuristicCounter().count([HumanMessage(content="你好")])
    long = HeuristicCounter().count([HumanMessage(content="你好" * 100)])
    assert long.tokens > short.tokens


class Broken:
    def count(self, messages) -> TokenCount:
        raise TokenCountError("primary down")


class Fixed:
    def count(self, messages) -> TokenCount:
        return TokenCount(12, estimated=True)


def test_fallback_counter_uses_the_fallback_and_keeps_its_label():
    counted = FallbackCounter(Broken(), Fixed()).count(MESSAGES)
    assert counted == TokenCount(12, estimated=True)


def test_fallback_failure_is_reported():
    with pytest.raises(TokenCountError, match="primary down"):
        FallbackCounter(Broken(), Broken()).count(MESSAGES)


def test_create_counter_with_the_estimate_keyword_skips_tiktoken():
    counted = create_counter("estimate").count(MESSAGES)
    assert counted.estimated is True
