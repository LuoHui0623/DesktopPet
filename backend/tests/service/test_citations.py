from agent.capabilities.chat.citations import resolve_sources, split_citations
from agent.capabilities.chat.context import MemoryFragment


def test_markers_are_removed_with_their_leading_space_and_ids_kept_in_order():
    raw = "喜欢美式咖啡 [引用 2026-10-18]。昨天聊过茶 [引用 2026-10-17]，又说了 [引用 2026-10-18]。"
    text, ids = split_citations(raw)
    assert text == "喜欢美式咖啡。昨天聊过茶，又说了。"
    assert ids == ["2026-10-18", "2026-10-17"]


def test_malformed_markers_are_left_in_place_and_ignored():
    raw = "好的 [引用] 继续 [引用 无效 id] 结束"
    text, ids = split_citations(raw)
    assert text == raw
    assert ids == []


def test_recall_fragments_produce_no_user_visible_sources():
    """第 26 条/T-03：召回片段只作提示词内行 id 锚点，不产生用户可见 sources。"""
    injected = [
        MemoryFragment(id=17, role="master", text="昨天聊了茶"),
        MemoryFragment(id=18, role="me", text="喜欢美式咖啡，不加糖"),
    ]
    assert resolve_sources(["18", "17", "99"], injected) == []


def test_nothing_is_cited_without_injected_memories():
    assert resolve_sources(["18"], []) == []
