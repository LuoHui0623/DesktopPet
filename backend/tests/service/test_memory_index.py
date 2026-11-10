from module.memory.index import daily_memory_index


def test_daily_memory_is_grouped_by_month_with_sorted_unique_dates():
    index = daily_memory_index(["2026-10-25", "2026-10-18", "2026-10-25", "2026-09-30"])
    assert index == {
        "2026-09": ["2026-09-30"],
        "2026-10": ["2026-10-18", "2026-10-25"],
    }


def test_daily_memory_index_is_empty_without_dates():
    assert daily_memory_index([]) == {}
