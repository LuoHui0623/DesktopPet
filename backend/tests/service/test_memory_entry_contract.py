import pytest
from pydantic import ValidationError

from module.memory.contracts import TAG_MAX_CHARS, MemoryEntryCreate, MemoryEntryUpdate


def test_accepts_valid_entry_and_defaults_priority():
    entry = MemoryEntryCreate(content=" 脆柿子 ", tags=["喜欢", "水果"])
    assert entry.content == "脆柿子"
    assert entry.tags == ["喜欢", "水果"]
    assert entry.priority == 2
    assert entry.source_ref is None


@pytest.mark.parametrize("priority", [1, 2, 3])
def test_accepts_priority_range(priority: int):
    assert MemoryEntryCreate(content="值", tags=["分区"], priority=priority).priority == priority


@pytest.mark.parametrize("priority", [0, 4])
def test_rejects_priority_out_of_range(priority: int):
    with pytest.raises(ValidationError):
        MemoryEntryCreate(content="值", tags=["分区"], priority=priority)


@pytest.mark.parametrize("content", ["", "   "])
def test_rejects_empty_content(content: str):
    with pytest.raises(ValidationError, match="条目内容不能为空"):
        MemoryEntryCreate(content=content, tags=["分区"])


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


def test_update_rejects_blank_content_and_out_of_range_priority():
    with pytest.raises(ValidationError, match="条目内容不能为空"):
        MemoryEntryUpdate(content="  ")
    with pytest.raises(ValidationError):
        MemoryEntryUpdate(priority=4)
