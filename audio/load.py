from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import librosa
import numpy as np


def extract_audio_to_wav(
    video_path: Path,
    sample_rate: int,
    audio_codec: str = "pcm_s16le",
    channels: int = 1,
) -> Path:
    """Extract a mono WAV track from a source video using ffmpeg."""
    temp_dir = Path(tempfile.mkdtemp(prefix="ipsc_audio_"))
    wav_path = temp_dir / f"{video_path.stem}_{sample_rate}hz.wav"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        audio_codec,
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        str(wav_path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    return wav_path


def load_audio(audio_path: Path, sample_rate: int) -> tuple[np.ndarray, int]:
    """Load audio as mono float32 and normalize to unit peak."""
    signal, sr = librosa.load(audio_path, sr=sample_rate, mono=True)
    signal = signal.astype(np.float32)
    peak = np.max(np.abs(signal)) if signal.size else 0.0
    if peak > 0:
        signal = signal / peak
    return signal, sr
