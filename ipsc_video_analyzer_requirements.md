# IPSC Video Analyzer – Requirements Specification

## 1. Overview
This project is a Python-based mini application that analyzes IPSC match videos and overlays shooting statistics on the video.

The system processes the audio track to detect:
- Start signal (timer beep)
- Individual gunshots

Then computes:
- First shot time (from beep)
- Splits between consecutive shots

Finally, it renders an output video with overlayed statistics.

---

## 2. Functional Requirements

### 2.1 Input
- Accept input video file (MP4, MOV, etc.)
- CLI interface:
  ```bash
  python ipsc_analyzer.py input.mp4 --out output.mp4
  ```

### 2.2 Audio Processing
- Extract audio using ffmpeg
- Convert to mono
- Resample to 16 kHz or 22.05 kHz

### 2.3 Event Detection

#### 2.3.1 Timer Beep Detection
- Detect short tonal signal near start
- Characteristics:
  - Narrowband frequency peak
  - Short duration (~50–200 ms)
- Output:
  - Single timestamp (seconds)

#### 2.3.2 Shot Detection
- Detect impulse-like events:
  - High energy peaks
  - Broadband spectrum
- Use:
  - Onset detection (librosa)
  - Peak picking
- Apply:
  - Minimum gap (refractory period): 60–100 ms

#### 2.3.3 False Positive Reduction
- Use simple heuristics:
  - RMS threshold
  - Spectral rolloff
  - Spectral centroid
- Optional:
  - Small classifier later (NOT required in MVP)

---

## 3. Signal Processing Pipeline

1. Load audio
2. Normalize signal
3. Compute onset envelope
4. Detect candidate peaks
5. Extract short windows around peaks
6. Compute features
7. Classify events (shot / other)
8. Filter by minimum spacing

---

## 4. Statistics Calculation

Given:
- beep_time
- shot_times[]

Compute:
- first_shot = shot_times[0] - beep_time
- splits[i] = shot_times[i] - shot_times[i-1]

---

## 5. Video Overlay

### 5.1 Requirements
- Overlay text onto video
- Update progressively as shots occur

### 5.2 Display Example
```
First Shot: 0.87

1: 0.87
2: +0.19
3: +0.24
4: +0.17
```

### 5.3 Tools
- moviepy OR opencv
- ffmpeg for final rendering

---

## 6. Output

- Output video with overlay
- Optional JSON file:
```json
{
  "beep_time": 0.52,
  "shots": [1.39, 1.58, 1.82],
  "first_shot": 0.87,
  "splits": [0.19, 0.24]
}
```

---

## 7. Project Structure

```
project/
│
├── ipsc_analyzer.py
├── audio/
│   ├── load.py
│   ├── detect.py
│   └── features.py
├── video/
│   └── overlay.py
├── utils/
│   └── io.py
└── configs/
    └── default.yaml
```

---

## 8. Dependencies

- numpy
- scipy
- librosa
- ffmpeg (external)
- moviepy OR opencv-python

---

## 9. Performance Requirements

- Should process 1 video (1–2 min) in under 30 seconds on CPU
- No GPU required
- Memory usage < 1 GB

---

## 10. Future Extensions (Optional)

- ML classifier for noise filtering
- Support multiple shooters
- GUI interface
- Real-time processing
- Shot classification (steel vs paper)

---

## 11. Deliverables

- Working CLI tool
- Source code
- Example input/output
- README with usage instructions

---

## 12. Non-Goals (MVP)

- Perfect accuracy
- Real-time streaming
- Deep learning models

---

## 13. Notes

- Prioritize robustness over complexity
- Prefer deterministic DSP over ML in MVP
- Ensure easy debugging (log intermediate events)
