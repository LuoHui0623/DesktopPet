# Copilot Instructions

- **Task scope**: Clarify the task's topic and scope.
- **Check before asking**: Verify existing materials, dependencies, and implementation before raising questions that require the user's decision.
- **Task worksheet**: Fill in the user's blank `Task*.md` template or keep the existing worksheet aligned and organized as shared context across collaboration rounds.
- **Discussion and execution**: Proactively surface open questions, discuss and confirm decisions with the user, update the task plan accordingly, and implement within the authorized scope.
- **建设性质疑**：对用户明确提出的口径、要求或方案，结合任务目标及相关材料独立审视。发现有依据的冲突、风险、遗漏或更合适的替代方案时，说明理由、依据与代价，并给出可行方案。避免无依据反驳或为形式重复质疑；涉及口径变更或重大取舍时由用户决定，用户知悉取舍后坚持原口径的，按其最终决定执行。
- **Skill guidance**: Before acting, check the available skill index and load relevant skills for guidance.
- **Root cause Before Fix**: Verify the root cause before fixing bugs; avoid speculative changes.
- **TDD**: For new features or behavior changes, write tests first, confirm they fail for the expected reason, then implement and verify they pass.
- **Documentation sync**: Once task decisions and implementation stabilize, update affected long-lived documentation to match the final implementation before delivery.
- **Completion verification**: Before delivery, run checks relevant to the changes and accurately report results and any unverified areas.

## Language Rules

- **Reasoning**: Think in English; communicate with the user in Chinese.
- **Documentation and comments**: Write and maintain documentation and code comments in Chinese.
- **表达方式**：根据内容优先用结构化符号（如 `业务主题 → 分层设计项 → 业务口径`）、表格或矩阵、无序或有序列表，以及 `**<主题>**：<说明>` 组织内容。说明符号含义，避免混淆层级与执行顺序；不为格式强行套模板。
- **提出概念**：给出概念的定义、场景范围、意义、是否已有概念重复