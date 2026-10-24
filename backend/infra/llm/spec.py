from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ApiParadigm(StrEnum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    MESSAGES = "messages"


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    api: ApiParadigm
    base_url: str
    api_key: str = field(repr=False)
    temperature: float = 0.7
    max_tokens: int = 1024
    default_headers: Mapping[str, str] = field(default_factory=dict)
