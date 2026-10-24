from typing import assert_never

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from infra.llm.spec import ApiParadigm, ModelSpec


def build_chat_model(spec: ModelSpec) -> BaseChatModel:
    match spec.api:
        case ApiParadigm.OPENAI_CHAT:
            return _openai_chat(spec)
        case ApiParadigm.OPENAI_RESPONSES:
            return _openai_responses(spec)
        case ApiParadigm.ANTHROPIC_MESSAGES:
            return _anthropic_messages(spec)
        case _:
            assert_never(spec.api)


def anthropic_root_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


def _openai_chat(spec: ModelSpec) -> ChatOpenAI:
    return ChatOpenAI(
        model=spec.model,
        base_url=spec.base_url,
        api_key=spec.api_key,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        default_headers=dict(spec.default_headers) or None,
    )


def _openai_responses(spec: ModelSpec) -> ChatOpenAI:
    return ChatOpenAI(
        model=spec.model,
        base_url=spec.base_url,
        api_key=spec.api_key,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        default_headers=dict(spec.default_headers) or None,
        use_responses_api=True,
    )


def _anthropic_messages(spec: ModelSpec) -> ChatAnthropic:
    return ChatAnthropic(
        model=spec.model,
        base_url=anthropic_root_url(spec.base_url),
        api_key=spec.api_key,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        default_headers=dict(spec.default_headers) or None,
    )
