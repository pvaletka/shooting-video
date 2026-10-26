# IPSC Video Analyzer

Small Python CLI for analyzing IPSC-style match videos from audio and rendering timing overlays on top of the source video.

The tool can work in two modes:

- `beep` mode: measure everything from the timer signal
- `first_shot` mode: ignore the timer and treat the first detected shot as time zero

## Features

- Extracts audio from `mp4` / `mov` with `ffmpeg`
- Detects timer beep using configurable frequency profiles
- Detects shots using onset analysis plus simple loudness heuristics
- Saves editable intermediate CSV
- Renders progressive overlay back onto the source video
- Supports per-run timer calibration with `--beep-offset`

## Project Layout

- [ipsc_analyzer.py](/Users/pavelvaletka/WORK/private/workspaces/shooting-video/ipsc_analyzer.py): CLI entrypoint
- [audio/detect.py](/Users/pavelvaletka/WORK/private/workspaces/shooting-video/audio/detect.py): beep and shot detection
- [video/overlay.py](/Users/pavelvaletka/WORK/private/workspaces/shooting-video/video/overlay.py): overlay rendering
- [utils/io.py](/Users/pavelvaletka/WORK/private/workspaces/shooting-video/utils/io.py): config and CSV helpers
- [configs/default.yaml](/Users/pavelvaletka/WORK/private/workspaces/shooting-video/configs/default.yaml): runtime settings
- [bin/ipsc-analyzer](/Users/pavelvaletka/WORK/private/workspaces/shooting-video/bin/ipsc-analyzer): wrapper script for convenient shell usage

## Requirements

- macOS
- Python 3
- `ffmpeg` installed and available in `PATH`

Python dependencies are installed into the local virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Shell Wrapper

The easiest way to run the tool is through the wrapper script:

```bash
./bin/ipsc-analyzer --help
```

To make it available from anywhere, add this to `~/.zshrc`:

```bash
export PATH="/Users/pavelvaletka/WORK/private/workspaces/shooting-video/bin:$PATH"
```

Then reload your shell:

```bash
source ~/.zshrc
```

After that you can run:

```bash
ipsc-analyzer run INPUT/stage-9.mov
```

## Default Outputs

If you do not pass `--csv` or `--out`, the tool writes into `./out` relative to the directory where you launch the command:

- CSV: `./out/<video_stem>.events.csv`
- overlay video: `./out/<video_stem>.overlay.mp4`

Example:

```bash
ipsc-analyzer run /path/to/video.mov
```

## Commands

### `analyze`

Detect events and save CSV:

```bash
ipsc-analyzer analyze INPUT/stage-8-new.mov
ipsc-analyzer analyze INPUT/stage-8-new.mov --csv tmp/stage8.csv
```

### `overlay`

Render overlay from an existing CSV:

```bash
ipsc-analyzer overlay INPUT/stage-8-new.mov --csv tmp/stage8.csv
ipsc-analyzer overlay INPUT/stage-8-new.mov --csv tmp/stage8.csv --out tmp/stage8_overlay.mp4
```

### `run`

Analyze and render in one step:

```bash
ipsc-analyzer run INPUT/stage-8-new.mov
ipsc-analyzer run INPUT/stage-8-new.mov --csv tmp/stage8.csv --out tmp/stage8_overlay.mp4
```

### `duel`

Compare two shooters: both videos are synced on their beeps, centre-cropped (top and bottom dropped) and stacked into one vertical 1080x1920 video with a running timer, shot count and last splits on each half. Beep and shot times come from the CSVs, so analyze both videos and fix the CSVs first:

```bash
ipsc-analyzer analyze INPUT/a.mov --csv tmp/a.csv
ipsc-analyzer analyze INPUT/b.mov --csv tmp/b.csv
# edit both CSVs, then:
ipsc-analyzer duel INPUT/a.mov INPUT/b.mov --csv-top tmp/a.csv --csv-bottom tmp/b.csv --labels Alice Bob
```

`--pre-beep` (default `1.0`) sets how much video is kept before the beep. The faster total turns green once both shooters have finished.

### `serve` (web UI)

Local web page for reviewing shots without editing CSV by hand:

```bash
ipsc-analyzer serve            # http://127.0.0.1:8000
ipsc-analyzer serve --port 8765
```

1. Upload a video (button or drag & drop). The beep and shots are detected in the background; the beep search window is widened to 10 s and the beep start is refined on its tone.
2. The table lists the detector's shots (checked) plus extra loud transients as unchecked *candidates*, since the detector misses fast follow-up shots. Check the real shots, uncheck echoes, neighbours and reload noise.
3. Scrub the video: the timer overlay is drawn live, and the row of the current signal is highlighted. Keys: Space play/pause, ←/→ one frame, Shift+←/→ 0.5 s, `A` add a shot at the current frame, `B` move the beep here.
4. Press **Сгенерировать видео** to render the final clip with the same overlay as `duel`, at the source resolution.

Edits are saved automatically. Everything lives in `out/web/<id>/` (`source.*`, `state.json`, `final.mp4`).

## Global Options

### `--config`

Override the YAML config file:

```bash
ipsc-analyzer --config custom.yaml run INPUT/stage-8-new.mov
```

### `--beep-offset`

Apply a small timing correction after beep detection. Positive values move the beep later and reduce `Total time`. Negative values move it earlier.

Example:

```bash
ipsc-analyzer --beep-offset 0.02 run INPUT/stage-8-new.mov
```

### `--beep-profile`

Choose a timer profile from config, or use `auto` to try all known profiles.

Examples:

```bash
ipsc-analyzer --beep-profile sg run INPUT/stage-8-new.mov
ipsc-analyzer --beep-profile commander run INPUT/stage-9.mov
ipsc-analyzer --beep-profile auto run INPUT/stage-9.mov
```

You can also set a default profile through the wrapper script with an environment variable:

```bash
IPSC_BEEP_PROFILE=sg ipsc-analyzer run INPUT/stage-8-new.mov
IPSC_BEEP_PROFILE=commander ipsc-analyzer run INPUT/stage-9.mov
```

### `--shots-profile`

Choose a shot detection profile from config. Defaults to `pistol`.

Use `carbine` for fast strings where shot envelopes overlap and the default onset threshold drops valid shots.

Examples:

```bash
ipsc-analyzer --shots-profile carbine run /path/to/carbine-run.mov
ipsc-analyzer --shots-profile pistol run INPUT/stage-9.mov
```

You can also set a default profile through the wrapper script with an environment variable:

```bash
IPSC_SHOTS_PROFILE=carbine ipsc-analyzer run /path/to/carbine-run.mov
```

### `--time-reference`

Choose what acts as time zero:

- `beep`: normal timer-based mode
- `first_shot`: ignore timer and use the first detected shot as zero

Examples:

```bash
ipsc-analyzer --time-reference beep run INPUT/stage-8-new.mov
ipsc-analyzer --time-reference first_shot run /Users/pavelvaletka/Downloads/shooting_trim.mp4
```

## CSV Format

The intermediate CSV is intentionally easy to read and edit:

```csv
signal_type,timestamp,split_time
beep,0.775000,
shot,1.939000,1.164000
shot,2.159000,0.220000
```

In `first_shot` mode the first line uses `shot_zero` instead of `beep`:

```csv
signal_type,timestamp,split_time
shot_zero,6.838277,
shot,7.082086,0.243809
```

Columns:

- `signal_type`: `beep`, `shot`, or `shot_zero`
- `timestamp`: absolute event time in seconds
- `split_time`: informational field only

The overlay is recalculated from `timestamp`, not from the stored `split_time`.

## Overlay Format

The overlay appears progressively as the run unfolds.

Example:

```text
Total Time: 9.43
First Shot: 1.16

1. 1.16
2. 1.38 (+0.22)
3. 2.11 (+0.73)
```

In `first_shot` mode the header changes to `Start Shot`.

## Timer Profiles

Timer profiles live in [configs/default.yaml](/Users/pavelvaletka/WORK/private/workspaces/shooting-video/configs/default.yaml).

Current examples:

- `sg`
- `commander`

Each profile defines:

- `name`
- `center_hz`
- `bandwidth_hz`

Example:

```yaml
profiles:
  - name: sg
    center_hz: 2700
    bandwidth_hz: 120
```

## Shots Profiles

Shot detection profiles live under `shots.profiles` in [configs/default.yaml](/Users/pavelvaletka/WORK/private/workspaces/shooting-video/configs/default.yaml). Each profile is a set of overrides applied on top of the base `shots:` values.

Current profiles:

- `pistol` (default) — empty, uses base values tuned for typical pistol cadence
- `carbine` — lowers `onset_delta` to catch tightly-spaced shots and raises `min_gap_seconds` to suppress double detections

Example:

```yaml
shots:
  profile: pistol
  onset_delta: 0.18
  min_gap_seconds: 0.05
  profiles:
    pistol: {}
    carbine:
      onset_delta: 0.10
      min_gap_seconds: 0.10
```

## Config Notes

Useful settings in [configs/default.yaml](/Users/pavelvaletka/WORK/private/workspaces/shooting-video/configs/default.yaml):

- `audio.av_offset_seconds` — the camera records sound late; this shift (default `-0.076`) is added to every detected time so marks land on the frame where the slide starts moving. Splits and totals are unaffected. Set `0` for a camera without lag.
- `shots.min_gap_seconds` — shots closer than this (default `0.15`) are treated as echoes
- `analysis.time_reference`
- `beep.search_window_seconds`
- `beep.profile`
- `beep.offset_seconds`
- `shots.profile`
- `shots.min_first_shot_delay_seconds`
- `shots.first_shot_peak_level_ratio`
- `overlay.font_size`

## Typical Workflows

### 1. Fast default run

```bash
ipsc-analyzer run INPUT/stage-9.mov
```

### 2. Force a specific timer profile

```bash
ipsc-analyzer --beep-profile commander run INPUT/stage-9.mov
```

### 3. Fine-tune total time

```bash
ipsc-analyzer --beep-profile sg --beep-offset 0.02 run INPUT/stage-8-new.mov
```

### 4. Ignore the timer and measure from the first shot

```bash
ipsc-analyzer --time-reference first_shot run /Users/pavelvaletka/Downloads/shooting_trim.mp4
```

### 5. Edit CSV manually before rendering

```bash
ipsc-analyzer analyze INPUT/stage-8-new.mov --csv tmp/manual.csv
ipsc-analyzer overlay INPUT/stage-8-new.mov --csv tmp/manual.csv --out tmp/manual_overlay.mp4
```

Typical manual correction flow:

1. Run `analyze` first and save the intermediate CSV.
2. Open the CSV in a text editor or spreadsheet app.
3. Keep the first `beep` or `shot_zero` row unchanged.
4. Remove false `shot` rows that came from neighboring stages, echoes, or other unwanted sounds.
5. If needed, adjust a `timestamp` manually when the detected event is slightly early or late.
6. Ignore the stored `split_time` values while editing. They are informational only.
7. Run `overlay` again using the corrected CSV.

Example for false shots from a neighboring stage:

Original CSV:

```csv
signal_type,timestamp,split_time
beep,0.775000,
shot,1.939000,1.164000
shot,2.159000,0.220000
shot,2.310000,0.151000
shot,2.401000,0.091000
shot,2.774000,0.373000
```

Suppose `2.310000` and `2.401000` are not your shots. Delete those rows and leave the rest:

```csv
signal_type,timestamp,split_time
beep,0.775000,
shot,1.939000,1.164000
shot,2.159000,0.220000
shot,2.774000,0.373000
```

Then rebuild the overlay:

```bash
ipsc-analyzer overlay INPUT/stage-8-new.mov --csv tmp/manual.csv --out tmp/manual_overlay.mp4
```

The overlay will be recalculated from the edited `timestamp` values, so it is safe if the old `split_time` column is no longer accurate.

## Known Limitations

- Timer detection still depends on profile quality and search window length
- Different timer brands may need dedicated profiles
- Neighboring shooters and echoes can still create false positives
- `first_shot` mode currently assumes the first accepted shot candidate is the intended zero point

## Troubleshooting

### Wrong beep time

- try `--beep-profile sg` or `--beep-profile commander`
- increase `beep.search_window_seconds`
- apply a small `--beep-offset`

### First shot is missing

- lower `shots.min_first_shot_delay_seconds`
- lower `shots.first_shot_peak_level_ratio`

### Files saved in an unexpected place

Remember that default output goes to `./out` relative to the shell directory where you run the command.
