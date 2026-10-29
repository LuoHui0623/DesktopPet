import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from config.persona import PersonaCard, PersonaConfig, card_path, load_card, validate_persona_id
from constants.agent import DEFAULT_PERSONA_ID
from module.persona.contracts import PersonaVersion

ACTIVE_KEY = "persona.active"
NAME_FIELD = "名字"


class PersonaSettings(Protocol):
    def get(self, key: str) -> str | None: ...

    def put(self, key: str, value: str) -> None: ...


class PersonaVersions(Protocol):
    def latest(self, persona_id: str) -> PersonaVersion | None: ...

    def append(self, persona_id: str, content: str, created_at: str) -> PersonaVersion: ...


class PersonaUnavailableError(Exception):
    code = "persona_unavailable"

    def __init__(self, persona_id: str, errors: tuple[str, ...]) -> None:
        self.persona_id = persona_id
        self.errors = errors
        super().__init__(f"人设 {persona_id} 不可用：{'；'.join(errors)}")


@dataclass(frozen=True)
class PersonaSnapshot:
    id: str
    config: PersonaConfig
    version: int


@dataclass(frozen=True)
class PersonaListing:
    id: str
    name: str | None
    available: bool
    reason: str | None
    active: bool


@dataclass
class _Cached:
    stamp: tuple[int, int]
    card: PersonaCard
    version: int | None = None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class PersonaService:
    def __init__(
        self,
        directory: Path,
        settings: PersonaSettings,
        versions: PersonaVersions,
        *,
        default_id: str = DEFAULT_PERSONA_ID,
        clock: Callable[[], str] = _now,
        session_marker: Callable[[str], object] | None = None,
    ) -> None:
        self._directory = directory
        self._settings = settings
        self._versions = versions
        self._default_id = default_id
        self._clock = clock
        self._session_marker = session_marker
        self._lock = threading.Lock()
        self._cache: dict[str, _Cached] = {}

    def active_id(self) -> str:
        return self._settings.get(ACTIVE_KEY) or self._default_id

    def listing(self) -> list[PersonaListing]:
        active = self.active_id()
        items: list[PersonaListing] = []
        for persona_id in self._ids_on_disk():
            with self._lock:
                card = self._card(persona_id)
            name = None
            if card.config is not None:
                name = card.config.themes["role"].values[NAME_FIELD]
            items.append(
                PersonaListing(
                    id=persona_id,
                    name=name,
                    available=card.available,
                    reason="；".join(card.errors) or None,
                    active=persona_id == active,
                )
            )
        return items

    def current(self) -> PersonaSnapshot:
        persona_id = self.active_id()
        with self._lock:
            entry = self._entry(persona_id)
            if entry.card.config is None:
                raise PersonaUnavailableError(persona_id, entry.card.errors)
            if entry.version is None:
                entry.version = self._sync(persona_id, entry.card.config)
            return PersonaSnapshot(id=persona_id, config=entry.card.config, version=entry.version)

    def activate(self, persona_id: str) -> PersonaSnapshot:
        with self._lock:
            entry = self._entry(persona_id)
            if entry.card.config is None:
                raise PersonaUnavailableError(persona_id, entry.card.errors)
            if entry.version is None:
                entry.version = self._sync(persona_id, entry.card.config)
            snapshot = PersonaSnapshot(
                id=persona_id, config=entry.card.config, version=entry.version
            )
        previous = self.active_id()
        self._settings.put(ACTIVE_KEY, persona_id)
        if previous != persona_id and self._session_marker is not None:
            self._session_marker(snapshot.config.themes["role"].values[NAME_FIELD])
        return snapshot

    def _ids_on_disk(self) -> list[str]:
        if not self._directory.is_dir():
            return []
        return sorted(
            path.stem
            for path in self._directory.iterdir()
            if path.is_file() and path.suffix == ".yaml"
        )

    def _entry(self, persona_id: str) -> _Cached:
        """Caller must hold the lock."""
        if validate_persona_id(persona_id) is not None:
            return _Cached(stamp=(0, 0), card=load_card(self._directory, persona_id))
        path = card_path(self._directory, persona_id)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return _Cached(
                stamp=(0, 0),
                card=PersonaCard(id=persona_id, config=None, errors=(f"未找到 {path.name}",)),
            )
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(persona_id)
        if cached is not None and cached.stamp == stamp:
            return cached
        entry = _Cached(stamp=stamp, card=load_card(self._directory, persona_id))
        self._cache[persona_id] = entry
        return entry

    def _card(self, persona_id: str) -> PersonaCard:
        """Caller must hold the lock."""
        return self._entry(persona_id).card

    def _sync(self, persona_id: str, config: PersonaConfig) -> int:
        content = config.to_normalized_json()
        latest = self._versions.latest(persona_id)
        if latest is not None and latest.content == content:
            return latest.version
        return self._versions.append(persona_id, content, self._clock()).version
