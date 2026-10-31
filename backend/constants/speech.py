from unicodedata import east_asian_width

SENTENCE_MAX_CHARS = 40
SENTENCE_MAX_WIDTH = 80
REPLY_MAX_ITEMS = 4
PERSONA_HINT_CHARS = 20

# 东亚宽度为 W（宽）或 F（全角）的字符在界面上占两格，其余占一格。
WIDE_WIDTHS = frozenset({"W", "F"})


def visible_width(text: str) -> int:
    """按显示宽度计量文本：中文与全角标点算 2，英文、数字与半角符号算 1。"""
    return sum(2 if east_asian_width(char) in WIDE_WIDTHS else 1 for char in text)
