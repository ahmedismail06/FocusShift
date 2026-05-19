# FocusShift Rewrite

A macOS menubar app that switches keyboard focus between monitors by tracking where you look, using your webcam and MediaPipe face landmarks.

## How it works

1. The camera captures your face at 640×480.
2. `HeadTracker` extracts yaw/pitch from the facial transformation matrix and iris position relative to each eye's bounding box.
3. A combined gaze score is computed and mapped to a monitor index. On a two-monitor horizontal layout, gaze left → left monitor, gaze right → right monitor. Vertical layouts use pitch.
4. A 100 ms dwell guard fires before any switch, so accidental glances are ignored.
5. `WindowSwitcher` uses Quartz to scan visible windows, then AppKit + Accessibility APIs to activate the target app and raise its window.
6. Keyboard, click, and scroll events suppress switching for 1.5 seconds and sync the current-screen state so a manual focus switch is never overridden mid-interaction.

## Requirements

- macOS (uses Quartz, AppKit, Accessibility, and AVFoundation frameworks)
- Python 3.11+
- Webcam

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The MediaPipe face landmarker model (~3 MB) is downloaded automatically to `~/.focusshift/face_landmarker.task` on first run.

Grant the app **Accessibility** and **Camera** permissions in System Settings when prompted.

## Running

```bash
source .venv/bin/activate
python -m focusshift_rewrite.main
```

If multiple cameras are detected, you will be prompted to pick one. After that, the app lives in the menubar.

## Menubar

| State | Icon |
|---|---|
| Single monitor / no face detected | `⏸ FocusShift` |
| Active, left monitor | `← L` |
| Active, right monitor | `→ R` |
| Active, top monitor (vertical) | `↑ T` |
| Active, bottom monitor (vertical) | `↓ B` |
| Paused by user | `⏸ FocusShift` |

The menu exposes **Pause/Resume Tracking**, **Refresh Monitors**, and **Quit**. Monitor layout is re-detected automatically every 5 seconds.

## Configuration

All tunables are constants at the top of `main.py`:

| Constant | Default | Description |
|---|---|---|
| `_DWELL_S` | `0.1` | Seconds gaze must stay on a new monitor before switching |
| `_INPUT_SUPPRESSION_S` | `1.5` | Seconds input events suppress switching |
| `_MONITOR_RECHECK_S` | `5` | Seconds between automatic monitor layout checks |
| `_GAZE_THRESHOLD` | `10` | Horizontal gaze angle (degrees) required to trigger a switch |
| `_VERTICAL_GAZE_THRESHOLD` | `5` | Vertical gaze angle (degrees) for stacked monitor layouts |
| `_IRIS_X_GAIN` | `22.0` | Amplifies horizontal iris movement relative to head yaw |
| `_IRIS_Y_GAIN` | `22.0` | Amplifies vertical iris movement relative to head pitch |

Enable verbose logging:

```bash
FOCUSSHIFT_DEBUG=1 python -m focusshift_rewrite.main
```

## Module overview

| File | Purpose |
|---|---|
| `main.py` | `FocusShiftRewriteApp` — rumps menubar app, tracking loop, gaze → screen mapping |
| `tracker.py` | `HeadTracker` — MediaPipe face landmarker wrapper; returns `(yaw, pitch, iris_x, iris_y)` |
| `switcher.py` | `WindowSwitcher` — Quartz window scan, AppKit activation, AX raise |
| `monitor_check.py` | Thin wrapper around `screeninfo` for monitor enumeration and layout detection |
