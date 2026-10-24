import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from infra.llm import ApiParadigm, ModelSpec, anthropic_root_url, build_chat_model

HEADERS = {"x-opencode-session": "ec50d820-8c4d-497f-a79b-7645b217eb87"}


def make_spec(api: ApiParadigm, base_url: str = "https://opencode.ai/zen/go/v1") -> ModelSpec:
    return ModelSpec(
        provider="opencode",
        model="m",
        api=api,
        base_url=base_url,
        api_key="sk-test",
        default_headers=HEADERS,
    )


def test_chat_completions_uses_chat_api_with_headers():
    llm = build_chat_model(make_spec(ApiParadigm.CHAT_COMPLETIONS))
    assert isinstance(llm, ChatOpenAI)
    assert llm.use_responses_api is not True
    assert llm.openai_api_base == "https://opencode.ai/zen/go/v1"
    assert llm.default_headers == HEADERS


def test_responses_enables_responses_api():
    llm = build_chat_model(make_spec(ApiParadigm.RESPONSES))
    assert isinstance(llm, ChatOpenAI)
    assert llm.use_responses_api is True
    assert llm.openai_api_base == "https://opencode.ai/zen/go/v1"
    assert llm.default_headers == HEADERS


def test_messages_strips_v1_from_base_url():
    llm = build_chat_model(make_spec(ApiParadigm.MESSAGES))
    assert isinstance(llm, ChatAnthropic)
    assert llm.anthropic_api_url == "https://opencode.ai/zen/go"
    assert llm.default_headers == HEADERS


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://opencode.ai/zen/go/v1", "https://opencode.ai/zen/go"),
        ("https://opencode.ai/zen/go/v1/", "https://opencode.ai/zen/go"),
        ("https://api.anthropic.com", "https://api.anthropic.com"),
        ("https://example.com/proxy", "https://example.com/proxy"),
    ],
)
def test_anthropic_root_url(base_url: str, expected: str):
    assert anthropic_root_url(base_url) == expected


def test_api_key_is_not_in_repr():
    spec = make_spec(ApiParadigm.CHAT_COMPLETIONS)
    assert "sk-test" not in repr(spec)


def test_no_headers_passes_none():
    spec = ModelSpec(
        provider="deepseek",
        model="deepseek-flash",
        api=ApiParadigm.CHAT_COMPLETIONS,
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
    )
    llm = build_chat_model(spec)
    assert isinstance(llm, ChatOpenAI)
    assert not llm.default_headers
