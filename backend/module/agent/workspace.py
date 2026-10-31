"""宠物的资源目录：启动时保证草稿本与表情包目录存在。

草稿本是宠物自己的便签文件，位于 `data/resources/` 下；`data/` 全部由运行时生成，
不入 Git，因此每次启动都要保证目录与初始文件就位。
"""

from pathlib import Path

from constants.path import DRAFT_PATH, EMOJI_DIR
from infra.files import io

DRAFT_TEMPLATE = "# 草稿本\n\n> 宠物自己的便签：临时计划、待办、想问主人的事。\n"


def prepare_workspace(resource_dir: Path) -> Path:
    """确保资源目录、草稿本与表情包目录存在，返回草稿本路径；已有内容不覆盖。"""
    draft = resource_dir / DRAFT_PATH.name
    if not draft.exists():
        io.write_text(draft, DRAFT_TEMPLATE)
    (resource_dir / EMOJI_DIR.name).mkdir(parents=True, exist_ok=True)
    return draft
