"""记忆索引：只保留每日对话的月份分组；结构化条目已由 memory_structured 直接查询。"""


def daily_memory_index(daily_dates: list[str]) -> dict[str, list[str]]:
    months: dict[str, list[str]] = {}
    for day in sorted(set(daily_dates)):
        months.setdefault(day[:7], []).append(day)
    return months
