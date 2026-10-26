from __future__ import annotations

import argparse
from pathlib import Path

from audio.detect import detect_events
from audio.detect import Event
from audio.load import extract_audio_to_wav, load_audio
from utils.io import load_config, read_events_csv, shift_events, summarize_events, write_events_csv
from video.duel import render_duel
from video.overlay import render_overlay
from video.trim import trim_video_to_start

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "default.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze IPSC videos and render timing overlays.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to YAML config.")
    parser.add_argument(
        "--beep-offset",
        type=float,
        default=None,
        help="Override beep offset in seconds after detection.",
    )
    parser.add_argument(
        "--beep-profile",
        default=None,
        help=(
            "Beep profile to use from config, or 'auto' to try all configured profiles. "
            "Falls back to $IPSC_BEEP_PROFILE, then to config 'beep.profile'."
        ),
    )
    parser.add_argument(
        "--shots-profile",
        default=None,
        help=(
            "Shot detection profile to use from config (e.g. 'pistol', 'carbine'). "
            "Falls back to $IPSC_SHOTS_PROFILE, then to config 'shots.profile' (default: pistol)."
        ),
    )
    parser.add_argument(
        "--time-reference",
        choices=["beep", "first_shot"],
        default=None,
        help="Use timer beep or first shot as the zero time reference. Default comes from config 'analysis.time_reference'.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Detect beep and shots, then save CSV.")
    analyze_parser.add_argument("input", help="Input video file.")
    analyze_parser.add_argument("--csv", help="Path to output CSV.")

    overlay_parser = subparsers.add_parser("overlay", help="Render overlay from edited CSV.")
    overlay_parser.add_argument("input", help="Input video file.")
    overlay_parser.add_argument("--csv", help="Path to input CSV.")
    overlay_parser.add_argument("--out", help="Output video path.")

    run_parser = subparsers.add_parser("run", help="Analyze video and render overlay in one pass.")
    run_parser.add_argument("input", help="Input video file.")
    run_parser.add_argument("--csv", help="Path to output CSV.")
    run_parser.add_argument("--out", help="Output video path.")

    trim_parser = subparsers.add_parser(
        "trim-to-beep",
        help="Trim video so a fixed amount of time remains before the timer beep.",
    )
    trim_parser.add_argument("input", help="Input video file.")
    trim_parser.add_argument(
        "--pre-beep",
        type=float,
        required=True,
        help="How many seconds should remain before the detected timer beep.",
    )
    trim_parser.add_argument("--out", help="Output video path.")

    trim_overlay_parser = subparsers.add_parser(
        "trim-and-overlay",
        help="Trim video to a fixed pre-beep offset and render overlay on the trimmed output.",
    )
    trim_overlay_parser.add_argument("input", help="Input video file.")
    trim_overlay_parser.add_argument(
        "--pre-beep",
        type=float,
        required=True,
        help="How many seconds should remain before the detected timer beep.",
    )
    trim_overlay_parser.add_argument("--csv", help="Path to output CSV for the trimmed timeline.")
    trim_overlay_parser.add_argument("--out", help="Output video path.")

    duel_parser = subparsers.add_parser(
        "duel",
        help="Stack two videos vertically, synced on their beeps, with a timer per shooter (from edited CSVs).",
    )
    duel_parser.add_argument("top", help="Top video file.")
    duel_parser.add_argument("bottom", help="Bottom video file.")
    duel_parser.add_argument("--csv-top", required=True, help="Events CSV for the top video.")
    duel_parser.add_argument("--csv-bottom", required=True, help="Events CSV for the bottom video.")
    duel_parser.add_argument(
        "--labels",
        nargs=2,
        metavar=("TOP", "BOTTOM"),
        help="Panel labels. Default: video file stems.",
    )
    duel_parser.add_argument(
        "--pre-beep",
        type=float,
        default=1.0,
        help="Seconds to keep before the beep (default: 1.0).",
    )
    duel_parser.add_argument("--out", help="Output video path.")

    return parser.parse_args()


def print_summary(summary: dict[str, float | list[float]]) -> None:
    reference_label = "Start shot" if summary.get("time_reference") == "first_shot" else "Beep"
    print(f"{reference_label}: {summary['beep_time']:.3f}s")
    shot_times = summary["shot_times"]
    if not shot_times:
        print("Shots: none detected")
        return
    print(f"Shots: {', '.join(f'{shot:.3f}s' for shot in shot_times)}")
    total_time = shot_times[-1] - summary["beep_time"]
    print(f"Total time: {total_time:.3f}s")
    first_shot = summary["first_shot"]
    if first_shot is not None:
        print(f"First shot: {first_shot:.3f}s")
    splits = summary["splits"]
    if splits:
        print(f"Splits: {', '.join(f'{split:.3f}s' for split in splits)}")


def default_output_paths(input_path: Path) -> tuple[Path, Path]:
    out_dir = Path.cwd() / "out"
    csv_path = out_dir / f"{input_path.stem}.events.csv"
    video_path = out_dir / f"{input_path.stem}.overlay.mp4"
    return csv_path, video_path


def default_trim_output_path(input_path: Path) -> Path:
    out_dir = Path.cwd() / "out"
    return out_dir / f"{input_path.stem}.trimmed.mp4"


def default_trim_overlay_paths(input_path: Path) -> tuple[Path, Path]:
    out_dir = Path.cwd() / "out"
    csv_path = out_dir / f"{input_path.stem}.trimmed.events.csv"
    video_path = out_dir / f"{input_path.stem}.trimmed.overlay.mp4"
    return csv_path, video_path


def analyze_video(input_path: Path, csv_path: Path, config: dict) -> None:
    print(f"Extracting audio from: {input_path}")
    wav_path = extract_audio_to_wav(
        input_path,
        sample_rate=int(config["audio"]["sample_rate"]),
        audio_codec=str(config["audio"]["ffmpeg_audio_codec"]),
        channels=int(config["audio"]["ffmpeg_audio_channels"]),
    )
    print("Loading audio track...")
    signal, sr = load_audio(wav_path, int(config["audio"]["sample_rate"]))
    print("Detecting beep and shots...")
    detection = detect_events(signal, sr, config)
    write_events_csv(csv_path, detection)
    summary = summarize_events(read_events_csv(csv_path))
    print_summary(summary)


def detect_video_events(input_path: Path, config: dict):
    print(f"Extracting audio from: {input_path}")
    wav_path = extract_audio_to_wav(
        input_path,
        sample_rate=int(config["audio"]["sample_rate"]),
        audio_codec=str(config["audio"]["ffmpeg_audio_codec"]),
        channels=int(config["audio"]["ffmpeg_audio_channels"]),
    )
    print("Loading audio track...")
    signal, sr = load_audio(wav_path, int(config["audio"]["sample_rate"]))
    print("Detecting timing events...")
    return detect_events(signal, sr, config)


def detection_to_events(detection) -> list[Event]:
    signal_type = "shot_zero" if detection.time_reference == "first_shot" else "beep"
    events = [Event(signal_type=signal_type, timestamp=float(detection.beep_time))]
    events.extend(Event(signal_type="shot", timestamp=float(shot_time)) for shot_time in detection.shot_times)
    events.sort(key=lambda item: item.timestamp)
    return events


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    config = load_config(config_path)
    if args.beep_offset is not None:
        config.setdefault("beep", {})
        config["beep"]["offset_seconds"] = float(args.beep_offset)
    if args.beep_profile is not None:
        config.setdefault("beep", {})
        config["beep"]["profile"] = str(args.beep_profile)
    if args.shots_profile is not None:
        config.setdefault("shots", {})
        config["shots"]["profile"] = str(args.shots_profile)
    if args.time_reference is not None:
        config.setdefault("analysis", {})
        config["analysis"]["time_reference"] = str(args.time_reference)

    if args.command == "analyze":
        input_path = Path(args.input)
        default_csv, _ = default_output_paths(input_path)
        csv_path = Path(args.csv) if args.csv else default_csv
        analyze_video(input_path, csv_path, config)
        print(f"CSV saved to: {csv_path}")
        return

    if args.command == "overlay":
        input_path = Path(args.input)
        default_csv, default_out = default_output_paths(input_path)
        csv_path = Path(args.csv) if args.csv else default_csv
        out_path = Path(args.out) if args.out else default_out
        events = read_events_csv(csv_path)
        print(f"Rendering overlay to: {out_path}")
        render_overlay(input_path, out_path, events, config)
        print_summary(summarize_events(events))
        print(f"Video saved to: {out_path}")
        return

    if args.command == "run":
        input_path = Path(args.input)
        default_csv, default_out = default_output_paths(input_path)
        csv_path = Path(args.csv) if args.csv else default_csv
        out_path = Path(args.out) if args.out else default_out
        analyze_video(input_path, csv_path, config)
        events = read_events_csv(csv_path)
        print(f"Rendering overlay to: {out_path}")
        render_overlay(input_path, out_path, events, config)
        print(f"CSV saved to: {csv_path}")
        print(f"Video saved to: {out_path}")
        return

    if args.command == "trim-to-beep":
        input_path = Path(args.input)
        out_path = Path(args.out) if args.out else default_trim_output_path(input_path)
        detection = detect_video_events(input_path, config)
        trim_start = max(0.0, float(detection.beep_time) - float(args.pre_beep))
        print(f"Trimming video to: {out_path}")
        trim_video_to_start(input_path, out_path, trim_start)
        print(f"Beep: {detection.beep_time:.3f}s")
        print(f"Trim start: {trim_start:.3f}s")
        print(f"Pre-beep preserved: {float(args.pre_beep):.3f}s")
        print(f"Video saved to: {out_path}")
        return

    if args.command == "trim-and-overlay":
        input_path = Path(args.input)
        default_csv, default_out = default_trim_overlay_paths(input_path)
        csv_path = Path(args.csv) if args.csv else default_csv
        out_path = Path(args.out) if args.out else default_out
        trimmed_video_path = out_path.with_suffix(".tmp.trimmed.mp4")

        detection = detect_video_events(input_path, config)
        trim_start = max(0.0, float(detection.beep_time) - float(args.pre_beep))

        print(f"Trimming video to: {trimmed_video_path}")
        trim_video_to_start(input_path, trimmed_video_path, trim_start)

        shifted_events = shift_events(detection_to_events(detection), trim_start)
        write_events_csv(
            csv_path,
            type(detection)(
                beep_time=shifted_events[0].timestamp,
                shot_times=[event.timestamp for event in shifted_events if event.signal_type == "shot"],
                time_reference=detection.time_reference,
                debug=detection.debug,
            ),
        )

        print(f"Rendering overlay to: {out_path}")
        render_overlay(trimmed_video_path, out_path, shifted_events, config)
        print(f"Beep: {detection.beep_time:.3f}s")
        print(f"Trim start: {trim_start:.3f}s")
        print(f"Pre-beep preserved: {float(args.pre_beep):.3f}s")
        print(f"CSV saved to: {csv_path}")
        print(f"Video saved to: {out_path}")
        return

    if args.command == "duel":
        videos = (Path(args.top), Path(args.bottom))
        labels = tuple(args.labels) if args.labels else (videos[0].stem, videos[1].stem)
        out_path = Path(args.out) if args.out else Path.cwd() / "out" / f"{videos[0].stem}.vs.{videos[1].stem}.mp4"
        events = (read_events_csv(Path(args.csv_top)), read_events_csv(Path(args.csv_bottom)))
        print(f"Rendering duel to: {out_path}")
        panels = render_duel(videos, events, labels, out_path, float(args.pre_beep))
        for panel in panels:
            print(f"{panel.label}: {len(panel.shots)} shots, total {panel.total:.2f}s")
        print(f"Video saved to: {out_path}")
        return


if __name__ == "__main__":
    main()
