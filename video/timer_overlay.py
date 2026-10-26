from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
# Layout is designed for a 1080 px wide panel; other sizes pass `scale`.
BASE_WIDTH = 1080
LAST_SHOTS = 5
FLASH_SECONDS = 0.12
GREEN, WHITE, YELLOW = (80, 230, 110), (255, 255, 255), (255, 214, 60)


@dataclass
class Panel:
    label: str
    beep: float
    shots: list[float]

    @property
    def total(self) -> float:
        return self.shots[-1] - self.beep if self.shots else 0.0


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float


def probe_video(video_path: Path) -> VideoInfo:
    """Display size (rotation applied), frame rate and duration of a video."""
    output = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate:stream_side_data=rotation:format=duration",
            "-of", "json", str(video_path),
        ]
    )
    data = json.loads(output)
    stream = data["streams"][0]
    width, height = int(stream["width"]), int(stream["height"])
    rotation = next((int(side.get("rotation", 0)) for side in stream.get("side_data_list", [])), 0)
    if abs(rotation) % 180 == 90:
        width, height = height, width
    numerator, denominator = (int(part) for part in stream["avg_frame_rate"].split("/"))
    return VideoInfo(width, height, numerator / denominator, float(data["format"]["duration"]))


def load_fonts(scale: float = 1.0) -> dict:
    return {
        "timer": ImageFont.truetype(FONT_PATH, round(104 * scale)),
        "text": ImageFont.truetype(FONT_PATH, round(40 * scale)),
        "label": ImageFont.truetype(FONT_PATH, round(38 * scale)),
    }


def draw_panel(
    image: Image.Image,
    fonts: dict,
    panel: Panel,
    y0: int,
    t: float,
    winner: bool,
    width: int = BASE_WIDTH,
    height: int = 960,
    scale: float = 1.0,
) -> None:
    def px(value: float) -> int:
        return round(value * scale)

    draw = ImageDraw.Draw(image)
    seen = [shot for shot in panel.shots if shot <= t]
    finished = bool(panel.shots) and t >= panel.shots[-1]
    timer = 0.0 if t < panel.beep else (panel.total if finished else t - panel.beep)
    timer_color = (GREEN if winner else YELLOW) if finished else WHITE

    # Translucent backing so text stays readable over sky and foliage.
    box_height = px(186 + 52 * min(len(seen), LAST_SHOTS) + (10 if seen else 0))
    image.alpha_composite(Image.new("RGBA", (px(470), box_height), (0, 0, 0, 120)), (px(24), y0 + px(24)))

    stroke = {"stroke_width": max(1, px(3)), "stroke_fill": "black"}
    draw.text((px(44), y0 + px(30)), f"{timer:5.2f}", font=fonts["timer"], fill=timer_color, **stroke)
    draw.text((px(48), y0 + px(140)), f"Shots: {len(seen)}", font=fonts["text"], fill=WHITE, **stroke)
    y = y0 + px(200)
    for index in range(max(0, len(seen) - LAST_SHOTS), len(seen)):
        shot = seen[index]
        line = f"{index + 1}. {shot - panel.beep:5.2f}"
        if index:
            line += f"  +{shot - seen[index - 1]:.2f}"
        color = YELLOW if t - shot < FLASH_SECONDS else WHITE
        draw.text((px(48), y), line, font=fonts["text"], fill=color, **stroke)
        y += px(52)

    if panel.label:
        draw.text((width - px(40), y0 + px(30)), panel.label, font=fonts["label"], fill=WHITE, anchor="ra", **stroke)

    if seen and t - seen[-1] < FLASH_SECONDS:
        draw.rectangle([px(4), y0 + px(4), width - 1 - px(4), y0 + height - 1 - px(4)], outline=YELLOW, width=px(8))


def render_timer_video(
    video_path: Path,
    beep: float,
    shots: list[float],
    output_path: Path,
    progress: Callable[[float], None] | None = None,
) -> None:
    """Burn the timer overlay into a single video at its native resolution."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    info = probe_video(video_path)
    scale = min(info.width, info.height) / BASE_WIDTH
    fps = round(info.fps)
    total_frames = int(round(info.duration * fps))
    panel = Panel("", beep, sorted(shots))
    fonts = load_fonts(scale)

    command = [
        "ffmpeg", "-v", "error", "-y",
        "-i", str(video_path),
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{info.width}x{info.height}", "-r", str(fps), "-i", "-",
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
        "-map", "[v]", "-map", "0:a?", "-r", str(fps),
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(output_path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for frame_index in range(total_frames):
            image = Image.new("RGBA", (info.width, info.height), (0, 0, 0, 0))
            draw_panel(
                image, fonts, panel, 0, frame_index / fps,
                winner=False, width=info.width, height=info.height, scale=scale,
            )
            process.stdin.write(np.asarray(image).tobytes())
            if progress and frame_index % 25 == 0:
                progress(frame_index / total_frames)
    finally:
        process.stdin.close()
    if process.wait() != 0:
        raise subprocess.CalledProcessError(process.returncode, command)
    if progress:
        progress(1.0)
