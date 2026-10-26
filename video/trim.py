from __future__ import annotations

import subprocess
from pathlib import Path


def trim_video_to_start(video_path: Path, output_path: Path, start_time: float) -> None:
    """Trim a video so the output starts at `start_time` seconds from the source."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    safe_start_time = max(0.0, float(start_time))
    command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{safe_start_time:.6f}",
        "-i",
        str(video_path),
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(output_path),
    ]
    subprocess.run(command, check=True)
