import re

SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("证件号", re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])")),
    ("证件号", re.compile(r"(护照|身份证|驾驶证|社保卡)[^\n]{0,8}?[A-Za-z0-9]{6,}")),
    ("银行卡号", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("密码", re.compile(r"(密码|口令|password|passwd|pin码)", re.IGNORECASE)),
    (
        "健康细节",
        re.compile(r"(确诊|诊断|病历|过敏|抑郁|癌症|糖尿病|高血压|化疗|手术|住院)"),
    ),
)


def sensitive_reason(text: str) -> str | None:
    for label, pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            return f"包含{label}，敏感信息不写入记忆"
    return None
