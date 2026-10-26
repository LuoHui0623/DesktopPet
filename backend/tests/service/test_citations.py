from service.chat.citations import resolve_sources, split_citations
from service.chat.context import KIND_DAILY, KIND_STRUCTURED, MemoryFragment


def test_markers_are_removed_with_their_leading_space_and_ids_kept_in_order():
    raw = (
        "喜欢美式咖啡 [引用 m_20261018_001]。"
        "昨天聊过茶 [引用 2026-10-17]，又说了 [引用 m_20261018_001]。"
    )
    text, ids = split_citations(raw)
    assert text == "喜欢美式咖啡。昨天聊过茶，又说了。"
    assert ids == ["m_20261018_001", "2026-10-17"]


def test_malformed_markers_are_left_in_place_and_ignored():
    raw = "好的 [引用] 继续 [引用 无效 id] 结束"
    text, ids = split_citations(raw)
    assert text == raw
    assert ids == []


def test_only_fragments_that_were_injected_become_sources():
    injected = [
        MemoryFragment(
            kind=KIND_STRUCTURED,
            date="2026-10-18",
            text="喜欢美式咖啡，不加糖",
            category="preference",
            memory_id="m_20261018_001",
        ),
        MemoryFragment(kind=KIND_DAILY, date="2026-10-17", text="昨天聊了茶"),
    ]
    sources = resolve_sources(
        ["2026-10-17", "m_20261018_999", "m_20261018_001", "2026-10-17"], injected
    )
    assert [source.id for source in sources] == ["2026-10-17", "m_20261018_001", "2026-10-17"]
    assert sources[1].text == "喜欢美式咖啡，不加糖"
    assert sources[1].category == "preference"
    assert sources[1].date == "2026-10-18"
    assert sources[0].kind == KIND_DAILY


def test_nothing_is_cited_without_injected_memories():
    assert resolve_sources(["m_20261018_001"], []) == []
