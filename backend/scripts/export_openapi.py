import json
from pathlib import Path

from api.app import create_app
from constants.path import BACKEND_DIR


def export_openapi(target: Path) -> None:
    app = create_app(token="openapi-export")
    content = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True)
    target.write_text(content + "\n", encoding="utf-8")


def main() -> int:
    target = BACKEND_DIR / "openapi.json"
    export_openapi(target)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
