from pathlib import Path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_lines(path: Path, start: int = 1, end: int | None = None) -> list[str]:
    if start < 1:
        raise ValueError(f"起始行必须从 1 开始，当前为 {start}")
    if end is not None and end < start:
        raise ValueError(f"结束行 {end} 不能小于起始行 {start}")
    lines = read_text(path).splitlines()
    return lines[start - 1 : end]


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def replace_paragraph(path: Path, index: int, content: str) -> None:
    lines = read_text(path).split("\n")
    spans = _paragraph_spans(lines)
    if not 1 <= index <= len(spans):
        raise ValueError(f"段落 {index} 不存在，共 {len(spans)} 段")
    start, end = spans[index - 1]
    lines[start : end + 1] = content.split("\n")
    write_text(path, "\n".join(lines))


def clear(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")
    write_text(path, "")


def _paragraph_spans(lines: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for position, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = position
        elif start is not None:
            spans.append((start, position - 1))
            start = None
    if start is not None:
        spans.append((start, len(lines) - 1))
    return spans
