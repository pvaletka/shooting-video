# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

- Python 3 in `.venv/` (created via `python3 -m venv .venv && pip install -r requirements.txt`).
- `ffmpeg` must be on `PATH`; the code shells out to it for audio extraction and trimming.
- macOS-only font fallbacks are hardcoded in `video/overlay.py` (`/System/Library/Fonts/...`).

## Running the tool

The shell wrapper `bin/ipsc-analyzer` invokes `.venv/bin/python ipsc_analyzer.py`. Prefer running the wrapper rather than activating the venv. It also injects `--beep-profile` from `$IPSC_BEEP_PROFILE` if set, prepending it before user args (so any explicit `--beep-profile` on the CLI overrides it).

```bash
./bin/ipsc-analyzer run INPUT/stage-9.mov                                # analyze + render in one pass
./bin/ipsc-analyzer analyze INPUT/stage.mov --csv tmp/stage.csv          # detect only, save editable CSV
./bin/ipsc-analyzer overlay INPUT/stage.mov --csv tmp/stage.csv          # render from existing CSV
./bin/ipsc-analyzer trim-to-beep INPUT/stage.mov --pre-beep 1.5          # trim source so beep sits 1.5s in
./bin/ipsc-analyzer trim-and-overlay INPUT/stage.mov --pre-beep 1.5      # trim, then overlay on the trim
./bin/ipsc-analyzer duel A.mov B.mov --csv-top a.csv --csv-bottom b.csv  # two shooters stacked, synced on beep
./bin/ipsc-analyzer serve --port 8000                                   # local web UI: review shots, render
```

Default outputs go to `./out/` relative to the **current working directory**, not the project root. Filenames are `<video_stem>.events.csv` / `<video_stem>.overlay.mp4` (and `.trimmed.*` variants for trim commands).

There is no test suite, linter config, or build step — verification is by running the CLI on a sample video and inspecting the CSV/overlay.

## Architecture

The pipeline is one-directional: `video → ffmpeg → wav → librosa features → events (CSV) → moviepy overlay → mp4`. Each stage is a thin module; the CLI in `ipsc_analyzer.py` orchestrates them.

### Two time-reference modes

The tool has two fundamentally different operating modes, selected by `analysis.time_reference` in config or `--time-reference`:

- **`beep`** — `detect_beep` finds the timer signal in the search window; shots after it are measured against beep time. CSV starts with a `beep` row.
- **`first_shot`** — the timer is ignored. The first detected shot becomes time zero. CSV starts with a `shot_zero` row. `detect_events` synthesizes a `provisional_beep` just before the earliest onset so the existing post-beep guard logic in `detect_shots` still applies.

Both modes flow through the same `DetectionSummary` dataclass and CSV format. Downstream code (`summarize_events`, `build_overlay_lines`) branches on `time_reference` to swap labels (`First Shot` vs `Start Shot`) and to reinsert the zero shot into `shot_times` for `first_shot` mode.

### Beep detection (`audio/detect.py:detect_beep`)

Beep detection is profile-based. `configs/default.yaml` defines named profiles (`sg`, `commander`) with `center_hz` + `bandwidth_hz`. The selected profile (or all profiles in `auto` mode) is scored against the STFT as `tonal_ratio × frame_rms × (1 − spectral_flatness)`. In `auto` mode, the profile whose strongest peak has the highest score wins.

Among detected peaks above a prominence threshold, the chooser prefers the **earliest** peak that (a) is followed by a shot within `min_first_shot_delay_seconds..plausible_first_shot_max_delay_seconds`, and (b) scores at least `earliest_peak_score_ratio × best_score`. This biases away from later, louder peaks (echoes, range commands, neighboring stages).

`reference_point` (`start` / `peak` / `end`) controls which edge of the detected beep envelope becomes the timestamp; `offset_seconds` (or `--beep-offset`) is added afterwards for fine calibration.

### Shot detection (`audio/detect.py:detect_shots`)

Onsets come from `librosa.onset.onset_detect`. Candidates within `beep_guard = beep_time + min_first_shot_delay_seconds` are dropped to avoid counting the beep itself or its echoes.

Each candidate is scored from peak amplitude, RMS, and ZCR (with ZCR distance from the median acting as a small penalty). Candidates inside `first_shot_search_window_seconds` after the beep get a more lenient peak floor (`first_shot_peak_level_ratio`) — the first live round is often quieter than the rest of the string.

Final selection enforces `min_gap_seconds` between accepted shots, keeping the higher-scored candidate within any conflicting pair.

### CSV is the editable interface

`utils/io.py` is the contract between detection and rendering. Users routinely hand-edit the CSV between `analyze` and `overlay` to delete false positives (echoes, neighbor shots) or nudge timestamps. The overlay always recomputes splits from `timestamp`; the stored `split_time` column is informational and may be stale after editing. `read_events_csv` accepts `beep`, `shot`, or `shot_zero` and ignores anything else.

### Overlay rendering (`video/overlay.py`)

The overlay grows progressively: `build_overlay_lines(events, t)` filters to shots whose timestamp ≤ current time `t`. Each frame is rendered as RGBA via PIL and composited over the source clip with moviepy. Output is always `libx264` + `aac`.

### `trim-and-overlay` flow

`trim-and-overlay` writes a temporary `.tmp.trimmed.mp4` next to the final output, runs the overlay against that trimmed clip, and shifts all event timestamps by `trim_start` via `shift_events` before writing the CSV — so the saved CSV matches the trimmed timeline, not the original.

### Web UI (`web/`)

`serve` runs FastAPI (`web/app.py`) with a no-build static page (`web/static/`). Each upload gets `out/web/<id>/` with `source.*`, `state.json` (status, `beep`, `signals[]` with `t`, `source` = detector/candidate/manual, `enabled`) and `final.mp4`. Analysis and render run in daemon threads that update `state.json`; on startup, jobs left in `analyzing`/`rendering` are marked as errors. Analysis widens the beep search window to 10 s, refines the beep with `refine_beep_onset`, and adds unchecked `find_shot_candidates` (both in `audio/detect.py`). Render uses `video/timer_overlay.py:render_timer_video`. The canvas overlay in `web/static/app.js` duplicates the layout constants of `video/timer_overlay.py` — change both together.

### `duel` flow (`video/duel.py`)

`duel` reads beep/shot times only from the two CSVs (no detection), so the expected workflow is `analyze` → hand-fix CSVs → `duel`. A single ffmpeg pass seeks each source to `beep − pre_beep`, centre-crops to 1080x960 panels, `vstack`s them, and overlays RGBA timer frames (drawn by `video/timer_overlay.py:draw_panel`) piped in on stdin. Layout constants are module-level, not config.

### Audio/video offset

The camera records sound late: on a hand-marked gold set the slide starts moving ~67 ms (median) before the shot's audio onset. `audio.av_offset_seconds` (default `-0.076`, chosen so marks land on the gold frame for 19/21 shots) is added to every detected time — beep and shots — via `to_video_time` in `detect_events`, so CSVs, overlays and the web UI are on the picture's timeline. Splits and totals are unaffected (beep shifts too). The audio in rendered videos is not shifted. The web analysis calls `detect_events(..., apply_av_offset=False)`, refines beep/shot onsets on the audio, then applies the offset itself.

## Config

Config is YAML, loaded once and mutated in `main()` to apply CLI overrides (`--beep-offset`, `--beep-profile`, `--time-reference`) before being passed down. `--config` paths are resolved relative to CWD if not absolute. There's no schema validation; modules access keys directly and will `KeyError` on missing config.
