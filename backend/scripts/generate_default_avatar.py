"""生成默认形象包的帧图（仅用标准库）。

默认形象是原先内联 SVG 占位表情的位图版：同一组颜色、同样的圆形轮廓、
两只眼睛与一条嘴。每个状态输出一组循环帧，`disconnected` 是虚线单帧。
改动尺寸、形状或节奏后重新运行本脚本，它会同时重写 `manifest.json`：

    uv run python scripts/generate_default_avatar.py
"""

from __future__ import annotations

import json
import math
import struct
import zlib
from pathlib import Path

SIZE = 240
SUPERSAMPLE = 4
SCALE = SIZE // 120 * SUPERSAMPLE

CENTER = 60 * SCALE
RADIUS = 50 * SCALE
STROKE = 3 * SCALE
DASH = 6 * SCALE

EYE_RADIUS = 5 * SCALE
EYE_LEFT = (44, 52)
EYE_RIGHT = (76, 52)
MOUTH_START = (42, 74)
MOUTH_CONTROL = (60, 88)
MOUTH_END = (78, 74)

STROKE_COLOR = (68, 68, 68)
DISCONNECTED_STROKE = (154, 165, 177)
FACE_COLOR = (51, 51, 51)

STATES = {
    "idle": (142, 197, 255),
    "thinking": (195, 166, 255),
    "speaking": (255, 209, 102),
    "executing": (123, 211, 137),
    "sleeping": (154, 165, 177),
}

# 每个状态的每一帧只声明与静态基准图的差异：上下浮动、眼珠偏移、闭眼、张嘴。
STATE_FRAMES = {
    "idle": (
        160,
        (
            {},
            {"bob": -0.75},
            {"bob": -1.5},
            {"bob": -0.75},
            {},
            {"bob": 0.75},
            {"bob": 1.5},
            {"bob": 0.75},
        ),
    ),
    "thinking": (
        220,
        (
            {},
            {"eye_dx": 2.0, "eye_dy": -1.0},
            {"eye_dx": 3.0},
            {"eye_dx": 2.0, "eye_dy": 1.0},
            {},
            {"eye_dx": -2.0},
        ),
    ),
    "speaking": (
        140,
        (
            {},
            {"mouth_open": 2.0},
            {"mouth_open": 4.0},
            {"mouth_open": 1.0},
        ),
    ),
    "executing": (
        140,
        (
            {},
            {"bob": -1.5},
            {"bob": -3.0},
            {"bob": -1.5},
        ),
    ),
    "sleeping": (
        600,
        (
            {"eyes_closed": True},
            {"bob": -0.5, "eyes_closed": True},
            {"bob": -1.0, "eyes_closed": True},
            {"bob": -0.5, "eyes_closed": True},
        ),
    ),
}

DISCONNECTED_FRAME_MS = 1000

_CIRCLE_CACHE: dict[tuple, list[int]] = {}


def _blend(target: list[int], x: int, y: int, color: tuple[int, int, int]) -> None:
    index = (y * SIZE * SUPERSAMPLE + x) * 4
    target[index] = color[0]
    target[index + 1] = color[1]
    target[index + 2] = color[2]
    target[index + 3] = 255


def _circle_layer(fill: tuple[int, int, int] | None, dashed: bool, bob: float) -> list[int]:
    cache_key = (fill, dashed, bob)
    cached = _CIRCLE_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)

    dim = SIZE * SUPERSAMPLE
    pixels = [0] * (dim * dim * 4)
    center = CENTER + bob * SCALE
    for y in range(dim):
        dy = y + 0.5 - center
        for x in range(dim):
            dx = x + 0.5 - center
            distance = math.hypot(dx, dy)
            inside_stroke = abs(distance - RADIUS) <= STROKE / 2
            if inside_stroke:
                if dashed:
                    theta = math.atan2(dy, dx) % (2 * math.pi)
                    on = int(theta * RADIUS / DASH) % 2 == 0
                    if not on:
                        continue
                    _blend(pixels, x, y, DISCONNECTED_STROKE)
                else:
                    _blend(pixels, x, y, STROKE_COLOR)
            elif distance < RADIUS and fill is not None:
                _blend(pixels, x, y, fill)

    _CIRCLE_CACHE[cache_key] = pixels
    return list(pixels)


def _fill_disc(
    pixels: list[int], cx: float, cy: float, radius: float, color: tuple[int, int, int]
) -> None:
    for y in range(int(cy - radius), int(cy + radius) + 1):
        for x in range(int(cx - radius), int(cx + radius) + 1):
            if math.hypot(x + 0.5 - cx, y + 0.5 - cy) <= radius:
                _blend(pixels, x, y, color)


def _stroke_path(
    pixels: list[int], points: list[tuple[float, float]], half: float = STROKE / 2
) -> None:
    for px, py in points:
        _fill_disc(pixels, px, py, half, FACE_COLOR)


def _mouth_points(control_y: float) -> list[tuple[float, float]]:
    start = (MOUTH_START[0] * SCALE, MOUTH_START[1] * SCALE)
    control = (MOUTH_CONTROL[0] * SCALE, control_y * SCALE)
    end = (MOUTH_END[0] * SCALE, MOUTH_END[1] * SCALE)
    points = []
    for step in range(97):
        t = step / 96
        x = (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t**2 * end[0]
        y = (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t**2 * end[1]
        points.append((x, y))
    return points


def render(
    fill: tuple[int, int, int] | None,
    dashed: bool = False,
    *,
    bob: float = 0.0,
    eye_dx: float = 0.0,
    eye_dy: float = 0.0,
    eyes_closed: bool = False,
    mouth_open: float = 0.0,
) -> list[int]:
    pixels = _circle_layer(fill, dashed, bob)

    for eye in (EYE_LEFT, EYE_RIGHT):
        cx = (eye[0] + eye_dx) * SCALE
        cy = (eye[1] + eye_dy + bob) * SCALE
        if eyes_closed:
            _stroke_path(
                pixels,
                [(cx - 5 * SCALE + step * 4, cy) for step in range(21)],
                STROKE / 2,
            )
        else:
            _fill_disc(pixels, cx, cy, EYE_RADIUS, FACE_COLOR)

    _stroke_path(pixels, _mouth_points(MOUTH_CONTROL[1] + mouth_open + bob))
    return pixels


def downsample(pixels: list[int]) -> bytes:
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)
        for x in range(SIZE):
            alpha_sum = 0
            color_sum = [0, 0, 0]
            for sy in range(SUPERSAMPLE):
                base = ((y * SUPERSAMPLE + sy) * SIZE * SUPERSAMPLE + x * SUPERSAMPLE) * 4
                for sx in range(SUPERSAMPLE):
                    index = base + sx * 4
                    alpha = pixels[index + 3]
                    alpha_sum += alpha
                    color_sum[0] += pixels[index] * alpha
                    color_sum[1] += pixels[index + 1] * alpha
                    color_sum[2] += pixels[index + 2] * alpha
            if alpha_sum == 0:
                rows.extend((0, 0, 0, 0))
                continue
            rows.extend(
                (
                    round(color_sum[0] / alpha_sum),
                    round(color_sum[1] / alpha_sum),
                    round(color_sum[2] / alpha_sum),
                    round(alpha_sum / (SUPERSAMPLE * SUPERSAMPLE)),
                )
            )
    return bytes(rows)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(
    path: Path,
    fill: tuple[int, int, int] | None,
    dashed: bool = False,
    **frame: float | bool,
) -> None:
    rows = downsample(render(fill, dashed, **frame))
    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(rows, 9))
        + _chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def build_states() -> dict[str, dict[str, object]]:
    states: dict[str, dict[str, object]] = {}
    for state, (frame_ms, frames) in STATE_FRAMES.items():
        states[state] = {
            "frames": [f"{state}_{index}.png" for index in range(len(frames))],
            "frame_ms": frame_ms,
        }
    states["disconnected"] = {
        "frames": ["disconnected.png"],
        "frame_ms": DISCONNECTED_FRAME_MS,
    }
    return states


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "resources" / "avatars" / "default"
    output.mkdir(parents=True, exist_ok=True)
    for stale in output.glob("*.png"):
        stale.unlink()

    states = build_states()
    for state, (_, frames) in STATE_FRAMES.items():
        color = STATES[state]
        for index, frame in enumerate(frames):
            write_png(output / f"{state}_{index}.png", color, **frame)
    write_png(output / "disconnected.png", None, dashed=True)

    manifest = {
        "id": "default",
        "name": "默认形象",
        "frame": {"width": SIZE, "height": SIZE},
        "fallback": "idle",
        "states": states,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"默认形象帧已生成：{output}")


if __name__ == "__main__":
    main()
