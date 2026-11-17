"""分层依赖：`agent/spec` 是契约层，不反向依赖任何实现包。

`spec` 只定义数据形状与校验（任务上下文、派发计划、运行记录、工具结果）。技能域单向
使用它：`AgentRunRecord.skill` 只保存技能名字符串，不引用技能类型。方向由本测试校验，
不靠约定——新增 import 一旦越过边界即失败。
"""

import ast
from pathlib import Path

import agent.spec

SPEC_DIR = Path(agent.spec.__file__).parent
FORBIDDEN_PACKAGES = (
    "agent.capabilities",
    "agent.files",
    "agent.harness",
    "agent.memory",
    "agent.prompts",
    "agent.skills",
    "agent.tools",
)


def imported_modules(path: Path) -> set[str]:
    """收集一份源码里的绝对导入模块名；相对导入不参与本检查。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_spec_never_imports_implementation_packages():
    offenders = [
        f"{path.name} → {module}"
        for path in sorted(SPEC_DIR.glob("*.py"))
        for module in sorted(imported_modules(path))
        if any(
            module == package or module.startswith(f"{package}.") for package in FORBIDDEN_PACKAGES
        )
    ]
    assert offenders == [], f"agent/spec 反向依赖了实现层：{offenders}"


def test_spec_records_skill_as_a_plain_name():
    """技能引用只以名称表达，不引入技能类型，保证 spec 不认识技能实现。"""
    from agent.spec.contracts import AgentRunRecord

    assert AgentRunRecord.__annotations__["skill"] is str
