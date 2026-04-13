from __future__ import annotations

import argparse
from pathlib import Path

from audio.detect import detect_events
from audio.load import extract_audio_to_wav, load_audio
from utils.io import load_config, read_events_csv, summarize_events, write_events_csv
from video.overlay import render_overlay

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
        help="Beep profile to use from config, or 'auto' to try all configured profiles.",
    )
    parser.add_argument(
        "--time-reference",
        choices=["beep", "first_shot"],
        default=None,
        help="Use timer beep or first shot as the zero time reference.",
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


def analyze_video(input_path: Path, csv_path: Path, config: dict) -> None:
    wav_path = extract_audio_to_wav(
        input_path,
        sample_rate=int(config["audio"]["sample_rate"]),
        audio_codec=str(config["audio"]["ffmpeg_audio_codec"]),
        channels=int(config["audio"]["ffmpeg_audio_channels"]),
    )
    signal, sr = load_audio(wav_path, int(config["audio"]["sample_rate"]))
    detection = detect_events(signal, sr, config)
    write_events_csv(csv_path, detection)
    summary = summarize_events(read_events_csv(csv_path))
    print_summary(summary)


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
        render_overlay(input_path, out_path, events, config)
        print(f"CSV saved to: {csv_path}")
        print(f"Video saved to: {out_path}")
        return


if __name__ == "__main__":
    main()
