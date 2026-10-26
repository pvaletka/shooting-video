from __future__ import annotations

import copy
import json
import re
import shutil
import threading
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from audio.detect import (
    detect_events,
    find_shot_candidates,
    refine_beep_onset,
    refine_shot_onsets,
    resolve_shots_config,
    to_video_time,
)
from audio.load import extract_audio_to_wav, load_audio
from video.timer_overlay import probe_video, render_timer_video

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Head-cam clips often start several seconds before the beep; the CLI default
# window (2s) misses it.
WEB_BEEP_SEARCH_WINDOW_SECONDS = 10.0
# Candidates closer than this to the beep are the beep's own tail.
CANDIDATE_MIN_DELAY_SECONDS = 0.5
# Detector and candidate times this close are the same impulse.
SAME_IMPULSE_SECONDS = 0.06

_config: dict = {}
_data_dir = Path.cwd() / "out" / "web"
_state_lock = threading.Lock()


class Signal(BaseModel):
    id: str
    t: float
    source: Literal["detector", "candidate", "manual"]
    enabled: bool


class SignalsUpdate(BaseModel):
    beep: float
    signals: list[Signal]


def configure(config: dict, data_dir: Path) -> None:
    global _config, _data_dir
    _config = copy.deepcopy(config)
    _config.setdefault("beep", {})["search_window_seconds"] = max(
        float(_config["beep"].get("search_window_seconds", 0.0)), WEB_BEEP_SEARCH_WINDOW_SECONDS
    )
    _data_dir = data_dir


def _video_dir(video_id: str) -> Path:
    if not re.fullmatch(r"[\w-]+", video_id) or not (_data_dir / video_id / "state.json").exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return _data_dir / video_id


def _read_state(video_id: str) -> dict:
    return json.loads((_video_dir(video_id) / "state.json").read_text(encoding="utf-8"))


def _write_state(video_dir: Path, state: dict) -> None:
    tmp_path = video_dir / "state.json.tmp"
    tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp_path.replace(video_dir / "state.json")


def _update_state(video_id: str, **changes) -> dict:
    with _state_lock:
        state = _read_state(video_id)
        state.update(changes)
        _write_state(_data_dir / video_id, state)
        return state


def build_signals(shots: list[float], candidates: list[float], min_gap: float) -> list[dict]:
    """Detector shots (checked) plus extra candidates (unchecked).

    The detector already keeps shots `min_gap` apart; candidates within
    `min_gap` after any kept impulse are echoes and are dropped.
    """
    signals: list[dict] = []
    kept: list[float] = []
    for t, source in sorted([(t, "detector") for t in shots] + [(t, "candidate") for t in candidates]):
        if source == "candidate" and any(-SAME_IMPULSE_SECONDS <= t - other < min_gap for other in kept):
            continue
        kept.append(t)
        signals.append({"id": f"s{len(signals) + 1}", "t": t, "source": source, "enabled": source == "detector"})
    return signals


def _analyze(video_id: str) -> None:
    video_dir = _data_dir / video_id
    try:
        state = _read_state(video_id)
        source = video_dir / state["source"]
        info = probe_video(source)
        audio_config = _config["audio"]
        wav_path = extract_audio_to_wav(
            source,
            sample_rate=int(audio_config["sample_rate"]),
            audio_codec=str(audio_config["ffmpeg_audio_codec"]),
            channels=int(audio_config["ffmpeg_audio_channels"]),
        )
        signal, sr = load_audio(wav_path, int(audio_config["sample_rate"]))
        # Refine on the audio timeline, then map everything to the picture.
        detection = detect_events(signal, sr, _config, apply_av_offset=False)
        beep = detection.beep_time
        center_hz = float(detection.debug["beep_profile_center_hz"])
        if center_hz > 0:  # 0 in first_shot mode: no timer tone to refine on
            beep = refine_beep_onset(
                signal, sr, beep, center_hz, float(detection.debug["beep_profile_bandwidth_hz"])
            )
        shots = refine_shot_onsets(signal, sr, [shot for shot in detection.shot_times if shot > beep])
        candidates = refine_shot_onsets(
            signal,
            sr,
            find_shot_candidates(signal, sr, beep + CANDIDATE_MIN_DELAY_SECONDS, shots, tolerance=SAME_IMPULSE_SECONDS),
        )
        min_gap = float(resolve_shots_config(_config["shots"])["min_gap_seconds"])
        signals = build_signals(to_video_time(shots, _config), to_video_time(candidates, _config), min_gap)
        beep = to_video_time([beep], _config)[0]
        _update_state(
            video_id,
            status="ready",
            fps=info.fps,
            duration=info.duration,
            width=info.width,
            height=info.height,
            beep=round(beep, 6),
            signals=signals,
        )
    except Exception as error:  # noqa: BLE001 - surfaced to the UI
        traceback.print_exc()
        _update_state(video_id, status="error", error=f"Analysis failed: {error}")


def _render(video_id: str) -> None:
    video_dir = _data_dir / video_id
    try:
        state = _read_state(video_id)
        shots = [item["t"] for item in state["signals"] if item["enabled"]]

        def report(fraction: float) -> None:
            _update_state(video_id, progress=round(fraction, 3))

        render_timer_video(video_dir / state["source"], state["beep"], shots, video_dir / "final.mp4", report)
        _update_state(video_id, status="done", progress=1.0)
    except Exception as error:  # noqa: BLE001 - surfaced to the UI
        traceback.print_exc()
        _update_state(video_id, status="error", error=f"Render failed: {error}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Background threads do not survive a restart; don't leave jobs spinning forever.
    _data_dir.mkdir(parents=True, exist_ok=True)
    for state_path in _data_dir.glob("*/state.json"):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["status"] in {"analyzing", "rendering"}:
            state.update(status="error", error="Interrupted by server restart; run it again.")
            _write_state(state_path.parent, state)
    yield


app = FastAPI(title="IPSC Analyzer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/videos")
def list_videos() -> list[dict]:
    videos = []
    for state_path in sorted(_data_dir.glob("*/state.json"), reverse=True):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        videos.append({"id": state_path.parent.name, "name": state["name"], "status": state["status"]})
    return videos


@app.post("/api/videos")
def upload_video(file: UploadFile) -> dict:
    name = Path(file.filename or "video.mp4").name
    video_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    video_dir = _data_dir / video_id
    video_dir.mkdir(parents=True)
    source_name = f"source{Path(name).suffix.lower() or '.mp4'}"
    with (video_dir / source_name).open("wb") as handle:
        shutil.copyfileobj(file.file, handle, length=4 * 1024 * 1024)
    state = {"name": name, "source": source_name, "status": "analyzing", "error": None, "progress": 0.0,
             "beep": None, "signals": []}
    _write_state(video_dir, state)
    threading.Thread(target=_analyze, args=(video_id,), daemon=True).start()
    return {"id": video_id, **state}


@app.get("/api/videos/{video_id}")
def get_video(video_id: str) -> dict:
    return {"id": video_id, **_read_state(video_id)}


@app.get("/api/videos/{video_id}/source")
def get_source(video_id: str) -> FileResponse:
    video_dir = _video_dir(video_id)
    return FileResponse(video_dir / _read_state(video_id)["source"])


@app.put("/api/videos/{video_id}/signals")
def save_signals(video_id: str, update: SignalsUpdate) -> dict:
    state = _read_state(video_id)
    if state["status"] in {"analyzing", "rendering"}:
        raise HTTPException(status_code=409, detail=f"Video is {state['status']}")
    signals = sorted((signal.model_dump() for signal in update.signals), key=lambda item: item["t"])
    # Edits invalidate a previous render.
    status = "ready" if state["status"] in {"done", "error"} and state["beep"] is not None else state["status"]
    return {"id": video_id, **_update_state(video_id, beep=update.beep, signals=signals, status=status)}


@app.post("/api/videos/{video_id}/render")
def start_render(video_id: str) -> dict:
    state = _read_state(video_id)
    if state["status"] not in {"ready", "done", "error"} or state["beep"] is None:
        raise HTTPException(status_code=409, detail=f"Video is {state['status']}")
    state = _update_state(video_id, status="rendering", progress=0.0, error=None)
    threading.Thread(target=_render, args=(video_id,), daemon=True).start()
    return {"id": video_id, **state}


@app.get("/api/videos/{video_id}/result")
def get_result(video_id: str) -> FileResponse:
    video_dir = _video_dir(video_id)
    state = _read_state(video_id)
    result = video_dir / "final.mp4"
    if state["status"] != "done" or not result.exists():
        raise HTTPException(status_code=404, detail="No rendered video yet")
    download_name = f"{Path(state['name']).stem}.timer.mp4"
    return FileResponse(result, media_type="video/mp4", filename=download_name)
