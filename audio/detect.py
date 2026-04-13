from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np
from scipy.signal import find_peaks

from audio.features import peak_amplitude, rms_energy, safe_window, zero_crossing_rate


@dataclass(slots=True)
class Event:
    signal_type: str
    timestamp: float


@dataclass(slots=True)
class DetectionSummary:
    beep_time: float
    shot_times: list[float]
    time_reference: str
    debug: dict[str, float | int | list[float]]


def _build_beep_profile_score(
    stft: np.ndarray,
    freqs: np.ndarray,
    config: dict,
) -> tuple[np.ndarray, dict[str, float | str]]:
    band_mask = (freqs >= float(config["min_frequency_hz"])) & (freqs <= float(config["max_frequency_hz"]))
    total_energy = stft.sum(axis=0) + 1e-8
    frame_rms = librosa.feature.rms(S=stft)[0]
    spectral_flatness = librosa.feature.spectral_flatness(S=stft + 1e-8)[0]

    profiles = config.get("profiles") or []
    profile_mode = str(config.get("profile", "auto")).strip().lower()
    if profile_mode != "auto":
        profiles = [profile for profile in profiles if str(profile.get("name", "")).strip().lower() == profile_mode]
    if not profiles:
        band_energy = stft[band_mask].sum(axis=0)
        tonal_ratio = band_energy / total_energy
        score = tonal_ratio * frame_rms * (1.0 - spectral_flatness)
        return score, {
            "profile_mode": "broadband",
            "profile_center_hz": float((float(config["min_frequency_hz"]) + float(config["max_frequency_hz"])) / 2.0),
            "profile_bandwidth_hz": float(float(config["max_frequency_hz"]) - float(config["min_frequency_hz"])),
        }

    best_score = None
    best_meta: dict[str, float | str] = {}
    for profile in profiles:
        center_hz = float(profile["center_hz"])
        bandwidth_hz = float(profile["bandwidth_hz"])
        half_bandwidth = bandwidth_hz / 2.0
        profile_mask = (freqs >= center_hz - half_bandwidth) & (freqs <= center_hz + half_bandwidth)
        if not np.any(profile_mask):
            continue

        profile_energy = stft[profile_mask].sum(axis=0)
        profile_ratio = profile_energy / total_energy
        profile_score = profile_ratio * frame_rms * (1.0 - spectral_flatness)

        # In auto mode we keep the profile whose strongest peak is the best fit
        # for this recording and defer the exact timestamp choice to later logic.
        if best_score is None or float(np.max(profile_score)) > float(np.max(best_score)):
            best_score = profile_score
            best_meta = {
                "profile_mode": str(profile.get("name", "unnamed")),
                "profile_center_hz": center_hz,
                "profile_bandwidth_hz": bandwidth_hz,
            }

    if best_score is None:
        band_energy = stft[band_mask].sum(axis=0)
        tonal_ratio = band_energy / total_energy
        fallback_score = tonal_ratio * frame_rms * (1.0 - spectral_flatness)
        return fallback_score, {
            "profile_mode": "fallback",
            "profile_center_hz": float((float(config["min_frequency_hz"]) + float(config["max_frequency_hz"])) / 2.0),
            "profile_bandwidth_hz": float(float(config["max_frequency_hz"]) - float(config["min_frequency_hz"])),
        }

    return best_score, best_meta


def detect_shot_onsets(signal: np.ndarray, sr: int, config: dict) -> np.ndarray:
    hop_length = 256
    onset_envelope = librosa.onset.onset_strength(y=signal, sr=sr, hop_length=hop_length)
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_envelope,
        sr=sr,
        hop_length=hop_length,
        units="frames",
        backtrack=False,
        pre_max=int(config["onset_pre_max"]),
        post_max=int(config["onset_post_max"]),
        pre_avg=int(config["onset_pre_avg"]),
        post_avg=int(config["onset_post_avg"]),
        wait=int(config["onset_wait"]),
        delta=float(config["onset_delta"]),
    )
    return librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop_length)


def detect_beep(
    signal: np.ndarray,
    sr: int,
    config: dict,
    shot_config: dict,
    rough_shot_times: np.ndarray | None = None,
) -> tuple[float, dict[str, float]]:
    search_seconds = float(config["search_window_seconds"])
    min_time_seconds = float(config.get("min_time_seconds", 0.0))
    min_frequency = float(config["min_frequency_hz"])
    max_frequency = float(config["max_frequency_hz"])
    min_prominence = float(config["min_prominence"])
    reference_point = str(config.get("reference_point", "start")).strip().lower()
    edge_threshold_ratio = float(config.get("edge_threshold_ratio", 0.35))
    earliest_peak_score_ratio = float(config.get("earliest_peak_score_ratio", 0.5))
    max_duration_seconds = float(config.get("max_duration_seconds", 0.25))
    offset_seconds = float(config.get("offset_seconds", 0.0))

    search_samples = min(signal.size, int(search_seconds * sr))
    search_signal = signal[:search_samples]
    if search_signal.size == 0:
        raise ValueError("Audio track is empty; beep detection cannot continue.")

    n_fft = 2048
    hop_length = 256
    stft = np.abs(librosa.stft(search_signal, n_fft=n_fft, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    band_mask = (freqs >= min_frequency) & (freqs <= max_frequency)
    if not np.any(band_mask):
        raise ValueError("Configured beep band is empty.")

    score, profile_meta = _build_beep_profile_score(stft, freqs, config)

    prominence = max(min_prominence, np.percentile(score, 85) * 0.35)
    peaks, properties = find_peaks(score, prominence=prominence)
    if rough_shot_times is None:
        rough_shot_times = detect_shot_onsets(signal, sr, shot_config)

    if peaks.size == 0:
        peak_index = int(np.argmax(score))
    else:
        best_peak = int(peaks[np.argmax(properties["prominences"])])
        best_score = float(score[best_peak])
        minimum_allowed_score = best_score * earliest_peak_score_ratio
        min_frame = int(librosa.time_to_frames(min_time_seconds, sr=sr, hop_length=hop_length))
        eligible_peaks = [
            int(peak)
            for peak in peaks
            if int(peak) >= min_frame and float(score[int(peak)]) >= minimum_allowed_score
        ]
        plausible_max_delay = float(config.get("plausible_first_shot_max_delay_seconds", 3.0))
        min_first_shot_delay = float(shot_config.get("min_first_shot_delay_seconds", 0.0))

        plausible_candidates: list[tuple[float, int]] = []
        for peak in eligible_peaks:
            candidate_time = float(librosa.frames_to_time(peak, sr=sr, hop_length=hop_length))
            future_shots = rough_shot_times[rough_shot_times > candidate_time]
            if future_shots.size == 0:
                continue
            first_delay = float(future_shots[0] - candidate_time)
            # A timer candidate is more believable if the next shot arrives in
            # a human-plausible reaction window rather than many seconds later.
            if min_first_shot_delay <= first_delay <= plausible_max_delay:
                plausible_candidates.append((candidate_time, peak))

        peak_index = min(plausible_candidates, key=lambda item: item[0])[1] if plausible_candidates else (min(eligible_peaks) if eligible_peaks else best_peak)

    peak_score = float(score[peak_index])
    edge_threshold = peak_score * edge_threshold_ratio
    # Timer beeps are short. Capping the expansion window prevents a strong
    # candidate from smearing backward into speech or range commands.
    max_duration_frames = max(1, int(librosa.time_to_frames(max_duration_seconds, sr=sr, hop_length=hop_length)))

    start_index = peak_index
    min_start_index = max(0, peak_index - max_duration_frames)
    while start_index > min_start_index and float(score[start_index - 1]) >= edge_threshold:
        start_index -= 1

    end_index = peak_index
    last_index = len(score) - 1
    max_end_index = min(last_index, peak_index + max_duration_frames)
    while end_index < max_end_index and float(score[end_index + 1]) >= edge_threshold:
        end_index += 1

    beep_start = float(librosa.frames_to_time(start_index, sr=sr, hop_length=hop_length))
    beep_peak = float(librosa.frames_to_time(peak_index, sr=sr, hop_length=hop_length))
    beep_end = float(librosa.frames_to_time(end_index, sr=sr, hop_length=hop_length))

    if reference_point == "end":
        beep_time = beep_end
    elif reference_point == "peak":
        beep_time = beep_peak
    else:
        beep_time = beep_start

    beep_time = max(0.0, beep_time + offset_seconds)

    return beep_time, {
        "beep_score": peak_score,
        "beep_peak_frame": float(peak_index),
        "beep_prominence_threshold": float(prominence),
        "beep_start": beep_start,
        "beep_peak": beep_peak,
        "beep_end": beep_end,
        "beep_offset_seconds": offset_seconds,
        "beep_profile_center_hz": float(profile_meta["profile_center_hz"]),
        "beep_profile_bandwidth_hz": float(profile_meta["profile_bandwidth_hz"]),
        "beep_reference": 0.0 if reference_point == "start" else 1.0 if reference_point == "peak" else 2.0,
    }


def detect_shots(signal: np.ndarray, sr: int, beep_time: float, config: dict) -> tuple[list[float], dict]:
    hop_length = 256
    onset_times = detect_shot_onsets(signal, sr, config)

    pre_roll = int(float(config["pre_roll_seconds"]) * sr)
    post_roll = int(float(config["post_roll_seconds"]) * sr)
    # Ignore candidates immediately after the timer to avoid counting the beep
    # itself, its harmonics, or echoes as the first shot.
    beep_guard = beep_time + max(0.03, float(config.get("min_first_shot_delay_seconds", 0.0)))

    candidates: list[dict[str, float]] = []
    for timestamp in onset_times:
        if float(timestamp) <= beep_guard:
            continue
        center = int(timestamp * sr)
        window = safe_window(signal, center - pre_roll, center + post_roll)
        candidate = {
            "timestamp": float(timestamp),
            "peak": peak_amplitude(window),
            "rms": rms_energy(window),
            "zcr": zero_crossing_rate(window),
        }
        candidates.append(candidate)

    if not candidates:
        return [], {"candidate_count": 0, "selected_count": 0}

    peaks = np.array([item["peak"] for item in candidates], dtype=np.float64)
    rms_values = np.array([item["rms"] for item in candidates], dtype=np.float64)
    zcr_values = np.array([item["zcr"] for item in candidates], dtype=np.float64)

    peak_reference = max(np.percentile(peaks, 90), 1e-6)
    rms_reference = max(np.percentile(rms_values, 90), 1e-6)
    peak_floor = peak_reference * float(config["peak_level_ratio"])
    # The first live round is often recorded softer than the later string, so
    # we give the early search window a more forgiving amplitude floor.
    first_shot_peak_floor = peak_reference * float(config.get("first_shot_peak_level_ratio", config["peak_level_ratio"]))
    first_shot_search_limit = beep_guard + float(config.get("first_shot_search_window_seconds", 2.0))

    scored_candidates = []
    for item in candidates:
        candidate_floor = first_shot_peak_floor if item["timestamp"] <= first_shot_search_limit else peak_floor
        if item["peak"] < candidate_floor:
            continue
        score = (item["peak"] / peak_reference) * 0.7 + (item["rms"] / rms_reference) * 0.3
        score -= abs(item["zcr"] - np.median(zcr_values)) * 0.05
        scored_candidates.append({**item, "score": float(score)})

    scored_candidates.sort(key=lambda item: item["score"], reverse=True)
    scored_candidates = scored_candidates[: int(config["max_candidates"])]
    scored_candidates.sort(key=lambda item: item["timestamp"])

    min_gap = float(config["min_gap_seconds"])
    selected: list[dict[str, float]] = []
    for candidate in scored_candidates:
        if not selected:
            selected.append(candidate)
            continue
        gap = candidate["timestamp"] - selected[-1]["timestamp"]
        if gap >= min_gap:
            selected.append(candidate)
            continue
        if candidate["score"] > selected[-1]["score"]:
            selected[-1] = candidate

    shot_times = [round(item["timestamp"], 6) for item in selected]
    return shot_times, {
        "candidate_count": len(candidates),
        "scored_count": len(scored_candidates),
        "selected_count": len(shot_times),
        "peak_floor": float(peak_floor),
        "first_shot_peak_floor": float(first_shot_peak_floor),
    }


def detect_events(signal: np.ndarray, sr: int, config: dict) -> DetectionSummary:
    time_reference = str(config.get("analysis", {}).get("time_reference", "beep")).strip().lower()
    rough_shot_times = detect_shot_onsets(signal, sr, config["shots"])
    if time_reference == "first_shot":
        if rough_shot_times.size == 0:
            raise ValueError("No shot candidates found; cannot use first shot as time reference.")
        # Place the synthetic guard just before the earliest onset so that the
        # first detected shot survives the post-beep exclusion window.
        provisional_beep = float(rough_shot_times[0] - float(config["shots"].get("min_first_shot_delay_seconds", 0.0)) - 1e-4)
        shot_times, shot_debug = detect_shots(signal, sr, provisional_beep, config["shots"])
        if not shot_times:
            raise ValueError("No shots detected; cannot use first shot as time reference.")
        # In this mode the first detected shot becomes the zero point that the
        # rest of the CSV and overlay are measured from.
        beep_time = shot_times[0]
        beep_debug = {
            "beep_score": 0.0,
            "beep_peak_frame": 0.0,
            "beep_prominence_threshold": 0.0,
            "beep_start": beep_time,
            "beep_peak": beep_time,
            "beep_end": beep_time,
            "beep_offset_seconds": 0.0,
            "beep_profile_center_hz": 0.0,
            "beep_profile_bandwidth_hz": 0.0,
            "beep_reference": 3.0,
        }
    else:
        beep_time, beep_debug = detect_beep(signal, sr, config["beep"], config["shots"], rough_shot_times)
        shot_times, shot_debug = detect_shots(signal, sr, beep_time, config["shots"])
    debug = {}
    debug.update(beep_debug)
    debug.update(shot_debug)
    return DetectionSummary(beep_time=round(beep_time, 6), shot_times=shot_times, time_reference=time_reference, debug=debug)
