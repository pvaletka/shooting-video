from __future__ import annotations

import csv
from pathlib import Path

import yaml

from audio.detect import DetectionSummary, Event


def load_config(config_path: Path) -> dict:
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_events_csv(csv_path: Path, detection: DetectionSummary) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["signal_type", "timestamp", "split_time"])
        # `shot_zero` keeps the same CSV shape while making it explicit that
        # this file was generated without using the timer beep as time zero.
        signal_type = "shot_zero" if detection.time_reference == "first_shot" else "beep"
        writer.writerow([signal_type, f"{detection.beep_time:.6f}", ""])

        previous_time = detection.beep_time
        for index, shot_time in enumerate(detection.shot_times):
            if detection.time_reference == "first_shot" and index == 0:
                continue
            split = shot_time - previous_time
            writer.writerow(["shot", f"{shot_time:.6f}", f"{split:.6f}"])
            previous_time = shot_time


def read_events_csv(csv_path: Path) -> list[Event]:
    events: list[Event] = []
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            signal_type = (row.get("signal_type") or "").strip().lower()
            timestamp_raw = (row.get("timestamp") or "").strip()
            if signal_type not in {"beep", "shot", "shot_zero"} or not timestamp_raw:
                continue
            events.append(Event(signal_type=signal_type, timestamp=float(timestamp_raw)))
    events.sort(key=lambda item: item.timestamp)
    return events


def summarize_events(events: list[Event]) -> dict[str, float | list[float]]:
    zero_event = next((event for event in events if event.signal_type in {"beep", "shot_zero"}), None)
    if zero_event is None:
        raise ValueError("CSV does not contain a beep or shot_zero event.")

    reference_mode = "first_shot" if zero_event.signal_type == "shot_zero" else "beep"
    shot_times = [event.timestamp for event in events if event.signal_type == "shot" and event.timestamp > zero_event.timestamp]
    if reference_mode == "first_shot":
        # Reinsert the zero shot so downstream code can treat both modes the same.
        shot_times = [zero_event.timestamp] + shot_times
        first_shot = 0.0
    else:
        first_shot = round(shot_times[0] - zero_event.timestamp, 6) if shot_times else None
    splits = [round(shot_times[index] - shot_times[index - 1], 6) for index in range(1, len(shot_times))]
    return {
        "beep_time": zero_event.timestamp,
        "time_reference": reference_mode,
        "shot_times": shot_times,
        "first_shot": first_shot,
        "splits": splits,
    }
