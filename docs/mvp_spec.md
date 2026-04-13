# IPSC Video Analyzer MVP Spec

## Goal

Build a macOS-only Python CLI that:

1. Extracts audio from a match or training video.
2. Detects the start beep and the shooter's shots using audio only.
3. Saves detected events into a human-editable CSV file.
4. Renders a video overlay from the CSV, showing shot timing progressively during playback.

## Scope

### In scope

- Offline processing only.
- Single shooter per analyzed video.
- One main start beep near the beginning of the run.
- Shot selection based primarily on loudness, with simple DSP heuristics.
- Manual correction by editing CSV between detection and overlay.
- CLI workflow with separate commands for analysis and rendering.

### Out of scope

- Real-time processing.
- GUI.
- Cross-platform support.
- ML classifier.
- Perfect robustness against all match noise conditions.

## Input Assumptions

- Input is a local video file such as `mp4` or `mov`.
- Audio quality is good enough to clearly capture the main shooter's shots.
- Neighboring shots may exist, but are usually quieter.
- No meaningful secondary timer beeps are expected.
- Practical minimum real split is about `0.10` seconds, so detections closer than that are likely echo or false positives.

## Accuracy Target

- Target timing error for beep and shot detection: about `+/- 0.05s` or better when source audio is usable.
- When confidence is low, deterministic and editable output is preferred over aggressive auto-correction.

## CLI Shape

Primary command:

```bash
python ipsc_analyzer.py analyze input.mp4 --csv events.csv
python ipsc_analyzer.py overlay input.mp4 --csv events.csv --out output.mp4
python ipsc_analyzer.py run input.mp4 --csv events.csv --out output.mp4
```

## Intermediate CSV Format

CSV is the editable interface between detection and rendering.

Columns:

- `signal_type`: `beep` or `shot`
- `timestamp`: event time in seconds from media start
- `split_time`: informational field only

Rules:

- `beep` row has empty `split_time`
- first `shot` row uses `shot_time - beep_time`
- later `shot` rows use `shot_time - previous_shot_time`
- overlay logic must recompute timing from `timestamp`, not trust `split_time`

Example:

```csv
signal_type,timestamp,split_time
beep,0.523,
shot,1.394,0.871
shot,1.582,0.188
shot,1.817,0.235
```

## Detection Pipeline

### Audio preprocessing

1. Extract audio with `ffmpeg`.
2. Convert to mono WAV.
3. Resample to `22050 Hz` for the first version.
4. Normalize waveform amplitude.

### Beep detection

Approach:

- Search early part of the audio first.
- Use short-time spectral analysis.
- Prefer a narrowband, short-duration, high-energy event.

Heuristic direction:

- find frame windows with high energy and strong tonal dominance
- prefer peaks with lower spectral flatness and concentrated frequency energy
- return the best single candidate

### Shot detection

Approach:

- Use onset envelope plus amplitude-driven peak detection.
- Score candidates with simple features from a short local window.
- Keep louder broadband transients that survive spacing rules.

Heuristic direction:

- onset strength
- local RMS / peak amplitude
- spectral centroid
- spectral rolloff
- optional distance from beep

Filtering:

- ignore events before beep, except for beep search itself
- enforce minimum shot gap of `0.10s`
- within too-close clusters, keep the stronger candidate
- optionally drop weak candidates far below the dominant shot level

## Overlay Behavior

- Overlay appears on the left side of the video.
- Display updates progressively as shots happen.
- Text should be legible and functional first, not polished.

Display rules:

- `First Shot: X.XX` appears after the first shot happens
- each shot line appears when that shot time is reached
- line 1 shows first-shot time as absolute delta from beep
- following lines show split values prefixed with `+`

Example on screen:

```text
First Shot: 0.87

1: 0.87
2: +0.19
3: +0.24
4: +0.17
```

## Project Structure

```text
shooting-video/
├── ipsc_analyzer.py
├── audio/
│   ├── __init__.py
│   ├── detect.py
│   ├── features.py
│   └── load.py
├── video/
│   ├── __init__.py
│   └── overlay.py
├── utils/
│   ├── __init__.py
│   └── io.py
├── configs/
│   └── default.yaml
├── docs/
│   └── mvp_spec.md
└── requirements.txt
```

## Dependencies

- `numpy`
- `scipy`
- `librosa`
- `moviepy`
- `PyYAML`
- external `ffmpeg`

## Debugging Support

Useful but non-blocking for MVP:

- optional verbose logs
- optional dump of candidate peaks and selected peaks
- clear console summary of detected beep, shots, first shot, and splits

## Deliverable Definition

MVP is complete when:

1. `analyze` creates a valid editable CSV from a source video.
2. user can manually edit the CSV.
3. `overlay` renders a video with progressive shot timing overlay based on the CSV.
4. `run` performs both steps in one command.
