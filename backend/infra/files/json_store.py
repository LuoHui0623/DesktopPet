import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(target: Path) -> Any:
    return json.loads(target.read_text(encoding="utf-8"))


def write_json_atomic(target: Path, data: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    descriptor, temp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


class JsonDirectory:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory

    def names(self) -> list[str]:
        if not self._directory.exists():
            return []
        return sorted(path.stem for path in self._directory.glob("*.json") if path.is_file())

    def read(self, name: str) -> Any:
        return read_json(self._path(name))

    def write(self, name: str, data: Any) -> None:
        write_json_atomic(self._path(name), data)

    def delete(self, name: str) -> None:
        self._path(name).unlink()

    def _path(self, name: str) -> Path:
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise ValueError(f"非法的文件名：{name!r}")
        return self._directory / f"{name}.json"
