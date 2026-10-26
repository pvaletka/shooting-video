from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from audio.detect import Event
from utils.io import summarize_events

WIDTH, PANEL_HEIGHT, FPS = 1080, 960, 50
HEIGHT = PANEL_HEIGHT * 2
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
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
        return self.shots[-1] - self.beep


def _probe_duration(video_path: Path) -> float:
    output = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)]
    )
    return float(output)


def _draw_panel(image: Image.Image, fonts: dict, panel: Panel, y0: int, t: float, winner: bool) -> None:
    draw = ImageDraw.Draw(image)
    seen = [shot for shot in panel.shots if shot <= t]
    finished = t >= panel.shots[-1]
    timer = 0.0 if t < panel.beep else (panel.total if finished else t - panel.beep)
    timer_color = (GREEN if winner else YELLOW) if finished else WHITE

    # Translucent backing so text stays readable over sky and foliage.
    box_height = 186 + 52 * min(len(seen), LAST_SHOTS) + (10 if seen else 0)
    image.alpha_composite(Image.new("RGBA", (470, box_height), (0, 0, 0, 120)), (24, y0 + 24))

    stroke = {"stroke_width": 3, "stroke_fill": "black"}
    draw.text((44, y0 + 30), f"{timer:5.2f}", font=fonts["timer"], fill=timer_color, **stroke)
    draw.text((48, y0 + 140), f"Shots: {len(seen)}", font=fonts["text"], fill=WHITE, **stroke)
    y = y0 + 200
    for index in range(max(0, len(seen) - LAST_SHOTS), len(seen)):
        shot = seen[index]
        line = f"{index + 1}. {shot - panel.beep:5.2f}"
        if index:
            line += f"  +{shot - seen[index - 1]:.2f}"
        color = YELLOW if t - shot < FLASH_SECONDS else WHITE
        draw.text((48, y), line, font=fonts["text"], fill=color, **stroke)
        y += 52

    draw.text((WIDTH - 40, y0 + 30), panel.label, font=fonts["label"], fill=WHITE, anchor="ra", **stroke)

    if seen and t - seen[-1] < FLASH_SECONDS:
        draw.rectangle([4, y0 + 4, WIDTH - 5, y0 + PANEL_HEIGHT - 5], outline=YELLOW, width=8)


def render_duel(
    videos: tuple[Path, Path],
    events: tuple[list[Event], list[Event]],
    labels: tuple[str, str],
    output_path: Path,
    pre_beep: float,
) -> list[Panel]:
    """Stack two clips vertically, aligned on their beeps, with a timer overlay per panel."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    starts: list[float] = []
    panels: list[Panel] = []
    for video_events, label in zip(events, labels):
        summary = summarize_events(video_events)
        start = max(0.0, float(summary["beep_time"]) - pre_beep)
        starts.append(start)
        # Panel times are on the output timeline, which begins at `start`.
        panels.append(
            Panel(label, float(summary["beep_time"]) - start, [shot - start for shot in summary["shot_times"]])
        )
    duration = max(_probe_duration(video) - start for video, start in zip(videos, starts))
    best_total = min(panel.total for panel in panels)

    # Centre crop each source to the panel aspect (drops top and bottom), then
    # hold the last frame of the shorter clip until the longer one ends.
    panel_filter = (
        f"crop=iw:iw*{PANEL_HEIGHT}/{WIDTH},scale={WIDTH}:{PANEL_HEIGHT},setsar=1,"
        f"tpad=stop_mode=clone:stop_duration={duration:.3f}"
    )
    filter_graph = (
        f"[0:v]{panel_filter}[top];[1:v]{panel_filter}[bottom];"
        f"[top][bottom]vstack=2,drawbox=y={PANEL_HEIGHT - 2}:w=iw:h=4:color=white@0.9:t=fill[stack];"
        "[stack][2:v]overlay=0:0[v];"
        "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[a]"
    )
    command = [
        "ffmpeg", "-v", "error", "-stats", "-y",
        "-ss", f"{starts[0]:.6f}", "-i", str(videos[0]),
        "-ss", f"{starts[1]:.6f}", "-i", str(videos[1]),
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
        "-filter_complex", filter_graph,
        "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(output_path),
    ]
    fonts = {
        "timer": ImageFont.truetype(FONT_PATH, 104),
        "text": ImageFont.truetype(FONT_PATH, 40),
        "label": ImageFont.truetype(FONT_PATH, 38),
    }
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for frame_index in range(int(round(duration * FPS))):
            t = frame_index / FPS
            image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            for index, panel in enumerate(panels):
                _draw_panel(image, fonts, panel, index * PANEL_HEIGHT, t, winner=panel.total == best_total)
            process.stdin.write(np.asarray(image).tobytes())
    finally:
        process.stdin.close()
    if process.wait() != 0:
        raise subprocess.CalledProcessError(process.returncode, command)
    return panels
