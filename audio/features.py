from __future__ import annotations

import numpy as np


def safe_window(signal: np.ndarray, start: int, end: int) -> np.ndarray:
    start = max(0, start)
    end = min(signal.size, end)
    if end <= start:
        return np.zeros(1, dtype=np.float32)
    return signal[start:end]


def rms_energy(window: np.ndarray) -> float:
    if window.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(window), dtype=np.float64)))


def peak_amplitude(window: np.ndarray) -> float:
    if window.size == 0:
        return 0.0
    return float(np.max(np.abs(window)))


def zero_crossing_rate(window: np.ndarray) -> float:
    if window.size < 2:
        return 0.0
    crossings = np.count_nonzero(np.diff(np.signbit(window)))
    return float(crossings / (window.size - 1))
