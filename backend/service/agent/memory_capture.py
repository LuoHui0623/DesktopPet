from dataclasses import dataclass

from service.agent.orchestrator import Checkpoint, Orchestrator
from service.agent.skills import MEMORY_WRITE_CAPABILITY, SkillRegistry
from service.memory.memory_service import MemoryWriter
from service.memory.sensitive import sensitive_reason

DEFAULT_NOTICE = "我记住啦。"
WRITE_TOOLS = ("memory_create", "memory_update")


@dataclass(frozen=True)
class CaptureResult:
    triggered: bool
    ok: bool
    notice: str | None = None
    checkpoint: Checkpoint | None = None


class MemoryCapture:
    def __init__(
        self,
        skills: SkillRegistry,
        orchestrator: Orchestrator,
        writer: MemoryWriter,
        capability: str = MEMORY_WRITE_CAPABILITY,
    ) -> None:
        self._skills = skills
        self._orchestrator = orchestrator
        self._writer = writer
        self._capability = capability

    def matches(self, user_text: str) -> bool:
        return any(skill.name == self._capability for skill in self._skills.match(user_text))

    def handle(self, user_text: str) -> CaptureResult:
        if not self.matches(user_text):
            return CaptureResult(triggered=False, ok=False)
        if self._writer.temporary_mode():
            return CaptureResult(True, False, notice="临时模式开启，本次不写入记忆")
        reason = sensitive_reason(user_text)
        if reason is not None:
            return CaptureResult(True, False, notice=f"这条没有记下：{reason}")

        checkpoint = self._orchestrator.dispatch(self._capability, user_text)
        if not checkpoint.ok:
            return CaptureResult(
                True, False, notice=f"这次没能记住：{checkpoint.error}", checkpoint=checkpoint
            )
        failed_write = next(
            (
                step
                for step in checkpoint.steps
                if step.get("action") == "tool"
                and step.get("tool") in WRITE_TOOLS
                and not step.get("ok")
            ),
            None,
        )
        if failed_write is not None:
            return CaptureResult(
                True,
                False,
                notice=f"这次没能记住：{failed_write.get('error')}",
                checkpoint=checkpoint,
            )
        return CaptureResult(
            True, True, notice=checkpoint.output or DEFAULT_NOTICE, checkpoint=checkpoint
        )
