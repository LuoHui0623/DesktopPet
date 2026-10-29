from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PerceptionEventKind(StrEnum):
    PRESENCE_AWAY = "presence_away"
    PRESENCE_RETURN = "presence_return"
    SIT_LONG = "sit_long"
    BUSY_ENTER = "busy_enter"
    BUSY_EXIT = "busy_exit"
    LATE_NIGHT = "late_night"
    APP_IN_USE = "app_in_use"
    APP_LAUNCHED = "app_launched"
    APP_QUIT = "app_quit"


IN_CONTEXT_KINDS = frozenset(
    {
        PerceptionEventKind.PRESENCE_RETURN,
        PerceptionEventKind.BUSY_EXIT,
        PerceptionEventKind.APP_IN_USE,
        PerceptionEventKind.APP_LAUNCHED,
        PerceptionEventKind.APP_QUIT,
    }
)


@dataclass(frozen=True)
class PerceptionEvent:
    kind: PerceptionEventKind
    at: datetime
    exe: str | None = None
    minutes: int | None = None

    @property
    def in_context(self) -> bool:
        return self.kind in IN_CONTEXT_KINDS

    @property
    def topic(self) -> str | None:
        if self.kind is PerceptionEventKind.PRESENCE_RETURN:
            return "greeting_return"
        if self.kind is PerceptionEventKind.SIT_LONG:
            return "sit_long"
        if self.kind is PerceptionEventKind.BUSY_EXIT:
            return "busy_released"
        if self.kind is PerceptionEventKind.LATE_NIGHT:
            return "late_night"
        if self.kind is PerceptionEventKind.APP_IN_USE:
            return f"app_in_use.{self.exe}"
        if self.kind is PerceptionEventKind.APP_LAUNCHED:
            return f"app_launched.{self.exe}"
        if self.kind is PerceptionEventKind.APP_QUIT:
            return f"app_quit.{self.exe}"
        return None

    @property
    def summary(self) -> str:
        if self.kind is PerceptionEventKind.PRESENCE_AWAY:
            return f"离开（空闲 {self.minutes} 分钟）"
        if self.kind is PerceptionEventKind.PRESENCE_RETURN:
            return f"离开 {self.minutes} 分钟后回来"
        if self.kind is PerceptionEventKind.SIT_LONG:
            return f"已连续坐着 {self.minutes} 分钟"
        if self.kind is PerceptionEventKind.BUSY_ENTER:
            return "进入忙碌（全屏或锁屏）"
        if self.kind is PerceptionEventKind.BUSY_EXIT:
            return f"忙碌 {self.minutes} 分钟后结束"
        if self.kind is PerceptionEventKind.LATE_NIGHT:
            return "深夜仍在使用电脑"
        if self.kind is PerceptionEventKind.APP_IN_USE:
            return f"使用 {self.exe} 已 {self.minutes} 分钟"
        if self.kind is PerceptionEventKind.APP_LAUNCHED:
            return f"打开 {self.exe}"
        return f"关闭 {self.exe}（运行 {self.minutes} 分钟）"


@dataclass(frozen=True)
class Trigger:
    topic: str
    reason: str
