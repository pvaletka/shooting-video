from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from audio.detect import Event
from utils.io import summarize_events
from video.timer_overlay import BASE_WIDTH, Panel, draw_panel, load_fonts, probe_video

WIDTH, PANEL_HEIGHT, FPS = BASE_WIDTH, 960, 50
HEIGHT = PANEL_HEIGHT * 2


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
    duration = max(probe_video(video).duration - start for video, start in zip(videos, starts))
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
    fonts = load_fonts()
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for frame_index in range(int(round(duration * FPS))):
            t = frame_index / FPS
            image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            for index, panel in enumerate(panels):
                draw_panel(image, fonts, panel, index * PANEL_HEIGHT, t, winner=panel.total == best_total)
            process.stdin.write(np.asarray(image).tobytes())
    finally:
        process.stdin.close()
    if process.wait() != 0:
        raise subprocess.CalledProcessError(process.returncode, command)
    return panels
