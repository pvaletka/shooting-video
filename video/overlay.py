from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import CompositeVideoClip, VideoClip, VideoFileClip

from audio.detect import Event
from utils.io import summarize_events


def build_overlay_lines(events: list[Event], current_time: float) -> str:
    summary = summarize_events(events)
    beep_time = float(summary["beep_time"])
    time_reference = str(summary.get("time_reference", "beep"))
    # The overlay grows over time so each line appears only after its shot
    # actually happened in the video.
    visible_shots = [shot_time for shot_time in summary["shot_times"] if shot_time <= current_time]

    if not visible_shots:
        return ""

    total_time = visible_shots[-1] - beep_time
    first_shot_label = "First Shot" if time_reference == "beep" else "Start Shot"
    lines = [
        f"Total Time: {total_time:.2f}",
        f"{first_shot_label}: {visible_shots[0] - beep_time:.2f}",
        "",
    ]
    for index, shot_time in enumerate(visible_shots):
        shot_delta = shot_time - beep_time
        if index == 0:
            lines.append(f"1. {shot_delta:.2f}")
        else:
            split = shot_time - visible_shots[index - 1]
            lines.append(f"{index + 1}. {shot_delta:.2f} (+{split:.2f})")
    return "\n".join(lines)


def _load_font(config: dict) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_size = int(config["font_size"])
    configured_font = str(config.get("font_path", "")).strip()
    font_candidates = [configured_font] if configured_font else []
    font_candidates.extend(
        [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
            "/Library/Fonts/Arial.ttf",
        ]
    )
    for candidate in font_candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), font_size)
    return ImageFont.load_default()


def _render_text_frame(width: int, height: int, text: str, config: dict) -> np.ndarray:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if not text:
        return np.array(image)

    draw = ImageDraw.Draw(image)
    font = _load_font(config)
    stroke_width = int(config["stroke_width"])
    fill_color = str(config["color"])
    stroke_color = str(config["stroke_color"])
    x = int(config["x"])
    y = int(config["y"])
    spacing = int(config["line_spacing"])

    draw.multiline_text(
        (x, y),
        text,
        fill=fill_color,
        font=font,
        spacing=spacing,
        stroke_width=stroke_width,
        stroke_fill=stroke_color,
        align="left",
    )
    return np.array(image)


def render_overlay(video_path: Path, output_path: Path, events: list[Event], config: dict) -> None:
    overlay_config = config["overlay"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with VideoFileClip(str(video_path)) as clip:
        overlay_clip = VideoClip(
            frame_function=lambda t: _render_text_frame(
                clip.w,
                clip.h,
                build_overlay_lines(events, t),
                overlay_config,
            ),
            duration=clip.duration,
            is_mask=False,
        )
        final_clip = CompositeVideoClip([clip, overlay_clip])
        final_clip.write_videofile(str(output_path), codec="libx264", audio_codec="aac")
