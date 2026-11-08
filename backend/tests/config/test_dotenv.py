from pathlib import Path

from config import settings
from config.dotenv import read_dotenv


def test_missing_file_returns_empty_config_with_warning(tmp_path: Path):
    assert read_dotenv(tmp_path / ".env") == ({}, ["未找到 .env，使用空配置"])


def test_comments_blank_lines_and_quotes_are_parsed(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text(
        """# 注释

PET_TOKEN="abc"
PET_HOST='127.0.0.1'
""",
        encoding="utf-8",
    )
    assert read_dotenv(path) == ({"PET_TOKEN": "abc", "PET_HOST": "127.0.0.1"}, [])


def test_invalid_line_and_duplicate_key_warn(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("NO_EQUALS\nPET_TOKEN=a\nPET_TOKEN=b\n", encoding="utf-8")
    values, warnings = read_dotenv(path)
    assert values == {"PET_TOKEN": "b"}
    assert warnings == [".env 第 1 行格式无效，已忽略", ".env 中键 PET_TOKEN 重复，以最后一次为准"]


def test_settings_keeps_read_dotenv_entry():
    assert settings.read_dotenv is read_dotenv
