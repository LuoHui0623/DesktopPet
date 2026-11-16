import pytest
from pydantic import ValidationError

from agent.memory.contracts import TAG_MAX_CHARS, MemoryEntryCreate, MemoryEntryUpdate


def test_accepts_valid_entry():
    entry = MemoryEntryCreate(content=" 脆柿子 ", tags=["喜欢", "水果"])
    assert entry.content == "脆柿子"
    assert entry.tags == ["喜欢", "水果"]
    assert entry.source_ref is None


def test_create_and_update_contracts_have_no_priority_field():
    """Q26/T-11：priority 已退役，写入与人工修改契约都不再接受该字段。"""
    assert "priority" not in MemoryEntryCreate.model_fields
    assert "priority" not in MemoryEntryUpdate.model_fields


@pytest.mark.parametrize("content", ["", "   "])
def test_rejects_empty_content(content: str):
    with pytest.raises(ValidationError, match="条目内容不能为空"):
        MemoryEntryCreate(content=content, tags=["分区"])


@pytest.mark.parametrize(
    "content",
    [
        "喜欢美式\n备注",
        "喜欢美式\r备注",
        "喜欢美式\t备注",
        "脆柿子、葡萄",
        "# 标题",
        "- 列表",
        "/路径",
        "> 引用",
        "*强调",
        "一" * 31,
    ],
)
def test_create_rejects_content_violating_r12(content: str):
    """R12：单行、≤30 字、禁换行/制表/顿号、禁行首 Markdown 结构字符；失败拒绝整条。"""
    with pytest.raises(ValidationError):
        MemoryEntryCreate(content=content, tags=["分区"])


def test_create_accepts_exactly_thirty_characters():
    content = "一" * 30
    assert MemoryEntryCreate(content=content, tags=["分区"]).content == content


def test_create_keeps_valid_content_verbatim():
    """合法正文原样保留：内部空格与全角逗号不转义、不截断（顿号才禁）。"""
    content = "不喜欢 太甜的甜点，除非是生日"
    assert MemoryEntryCreate(content=content, tags=["分区"]).content == content


@pytest.mark.parametrize(
    "content",
    ["喜欢美式\n备注", "脆柿子、葡萄", "# 标题", "一" * 31],
)
def test_update_rejects_content_violating_r12(content: str):
    with pytest.raises(ValidationError):
        MemoryEntryUpdate(content=content)


@pytest.mark.parametrize("tags", [[], [""], ["   "]])
def test_rejects_empty_tags(tags: list[str]):
    with pytest.raises(ValidationError):
        MemoryEntryCreate(content="值", tags=tags)


def test_accepts_ten_character_tag_and_rejects_eleven():
    ten = "一二三四五六七八九十"
    assert len(ten) == TAG_MAX_CHARS
    assert MemoryEntryCreate(content="值", tags=[ten]).tags == [ten]
    with pytest.raises(ValidationError, match="超过"):
        MemoryEntryCreate(content="值", tags=[ten + "一"])


@pytest.mark.parametrize(
    "tag",
    [
        "喜欢、水果",
        "喜欢；水果",
        "喜欢,水果",
        "喜欢，水果",
        "喜欢 水果",
        "喜欢-水果",
        "喜欢！",
        "😀",
    ],
)
def test_rejects_separators_and_non_chinese_alnum(tag: str):
    with pytest.raises(ValidationError):
        MemoryEntryCreate(content="值", tags=[tag])


def test_accepts_english_and_digits_and_dedupes_tags():
    entry = MemoryEntryCreate(content="值", tags=["AI2026", "ai2026", "AI2026", "水果"])
    assert entry.tags == ["AI2026", "水果"]


def test_requires_tags_field():
    with pytest.raises(ValidationError):
        MemoryEntryCreate(content="值")  # type: ignore[call-arg]


def test_update_requires_at_least_one_change():
    with pytest.raises(ValidationError, match="至少需要修改"):
        MemoryEntryUpdate()


def test_update_validates_and_dedupes_tags():
    update = MemoryEntryUpdate(tags=["水果", "水果 ", "饮品"])
    assert update.tags == ["水果", "饮品"]
    with pytest.raises(ValidationError):
        MemoryEntryUpdate(tags=["喜、欢"])


def test_update_rejects_blank_content():
    with pytest.raises(ValidationError, match="条目内容不能为空"):
        MemoryEntryUpdate(content="  ")
