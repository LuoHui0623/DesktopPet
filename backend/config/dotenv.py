"""`.env` 文件的读取与格式解析。

只做文本层解析，不含任何配置语义：服务配置（`config/settings.py`）与模型配置
（`infra/llm/configuration.py`）共用这一个入口。
"""

from pathlib import Path


def read_dotenv(path: Path) -> tuple[dict[str, str], list[str]]:
    """读取 `.env`，返回键值与解析告警。"""
    if not path.exists():
        return {}, [f"未找到 {path.name}，使用空配置"]
    values: dict[str, str] = {}
    warnings: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            warnings.append(f".env 第 {lineno} 行格式无效，已忽略")
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in values:
            warnings.append(f".env 中键 {key} 重复，以最后一次为准")
        values[key] = value
    return values, warnings
