"""Clean rewrite of the FocusShift menubar app."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

import cv2
import rumps

try:
    from .monitor_check import (
        get_monitors,
        get_monitors_sorted,
        is_multi_monitor,
        is_vertical_layout,
    )
    from .tracker import HeadTracker
    from .switcher import WindowSwitcher
except ImportError:
    from monitor_check import (  # type: ignore[no-redef]
        get_monitors,
        get_monitors_sorted,
        is_multi_monitor,
        is_vertical_layout,
    )
    from tracker import HeadTracker  # type: ignore[no-redef]
    from switcher import WindowSwitcher  # type: ignore[no-redef]

_CAMERA_CONFIG = Path.home() / ".focusshift" / "config.json"

_DWELL_S = 0.1
_INPUT_SUPPRESSION_S = 1.5
_MONITOR_RECHECK_S = 5
_GAZE_THRESHOLD = 10
_VERTICAL_GAZE_THRESHOLD = 5
_IRIS_X_GAIN = 22.0
_IRIS_Y_GAIN = 22.0
_IRIS_X_SIGN = +1
_IRIS_Y_SIGN = +1
_PITCH_SIGN = -1
_DEBUG = os.environ.get("FOCUSSHIFT_DEBUG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _debug(msg: str) -> None:
    if _DEBUG:
        print(f"[FocusShift:rewrite] {msg}", flush=True)


def _load_camera_config() -> int | None:
    try:
        return int(json.loads(_CAMERA_CONFIG.read_text())["camera_index"])
    except Exception:
        return None


def _save_camera_config(idx: int) -> None:
    _CAMERA_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    _CAMERA_CONFIG.write_text(json.dumps({"camera_index": idx}))


def _av_camera_names() -> list[str]:
    try:
        from AVFoundation import AVCaptureDevice

        devices = AVCaptureDevice.devicesWithMediaType_("vide")
        return [device.localizedName() for device in devices]
    except Exception:
        return []


def _enumerate_working_cameras(max_idx: int = 5) -> list[tuple[int, str, tuple[int, int]]]:
    names = _av_camera_names()
    cameras: list[tuple[int, str, tuple[int, int]]] = []
    for idx in range(max_idx + 1):
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        width = height = 0
        ok = False
        for _ in range(10):
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                height, width = frame.shape[:2]
                ok = True
                break
            time.sleep(0.05)
        cap.release()
        if ok:
            name = names[idx] if idx < len(names) else f"Camera {idx}"
            cameras.append((idx, name, (width, height)))
    return cameras


def _pick_camera_index(force: bool = False) -> int | None:
    if not force:
        saved = _load_camera_config()
        if saved is not None:
            return saved

    cameras = _enumerate_working_cameras()
    if not cameras:
        return None

    if len(cameras) == 1:
        idx, name, size = cameras[0]
        print(
            f"[FocusShift] Using only available camera: {name} ({size[0]}x{size[1]}) [index {idx}]",
            flush=True,
        )
        _save_camera_config(idx)
        return idx

    if not sys.stdin.isatty():
        idx = cameras[0][0]
        print(f"[FocusShift] No TTY — defaulting to first camera (index {idx}).", flush=True)
        _save_camera_config(idx)
        return idx

    print("\nAvailable cameras:")
    for offset, (idx, name, size) in enumerate(cameras):
        print(f"  [{offset}] {name} {size[0]}x{size[1]} (cv index {idx})")
    while True:
        try:
            raw = input(f"Pick a camera [0-{len(cameras) - 1}, default 0]: ").strip()
        except (EOFError, KeyboardInterrupt):
            idx = cameras[0][0]
            _save_camera_config(idx)
            return idx
        if raw == "":
            idx = cameras[0][0]
            _save_camera_config(idx)
            return idx
        try:
            choice = int(raw)
        except ValueError:
            choice = -1
        if 0 <= choice < len(cameras):
            idx = cameras[choice][0]
            _save_camera_config(idx)
            return idx
        print(f"  Enter 0-{len(cameras) - 1}.")


def _gaze_x(yaw: float, iris_x: float) -> float:
    return yaw + _IRIS_X_SIGN * (iris_x - 0.5) * _IRIS_X_GAIN


def _gaze_y(pitch: float, iris_y: float) -> float:
    return _PITCH_SIGN * pitch + _IRIS_Y_SIGN * (0.5 - iris_y) * _IRIS_Y_GAIN


def _monitor_signature(monitors: list) -> tuple[tuple[int, int, int, int], ...]:
    return tuple(
        (int(m.x), int(m.y), int(m.width), int(m.height))
        for m in get_monitors_sorted(monitors)
    )


def _screen_label(idx: int, count: int, layout: str, paused: bool = False) -> str:
    if count == 0:
        return "⏸ FocusShift"
    if paused:
        return "⏸ FocusShift"
    if count == 1:
        return "● FocusShift"
    if layout == "vertical":
        arrows = ["↑" if i == 0 else ("↓" if i == count - 1 else "·") for i in range(count)]
        name = "T" if idx == 0 else ("B" if idx == count - 1 else str(idx + 1))
    else:
        arrows = ["←" if i == 0 else ("→" if i == count - 1 else "·") for i in range(count)]
        name = "L" if idx == 0 else ("R" if idx == count - 1 else str(idx + 1))
    return f"{arrows[idx]} {name}"


def _decide_target(
    yaw: float,
    pitch: float,
    iris_x: float,
    iris_y: float,
    count: int,
    layout: str,
) -> int | None:
    if count <= 1:
        return 0
    if layout == "vertical":
        gaze = _gaze_y(pitch, iris_y)
        if count == 2:
            if gaze >= _VERTICAL_GAZE_THRESHOLD:
                return 0
            if gaze <= -_VERTICAL_GAZE_THRESHOLD:
                return 1
            return None
        span = 90.0
        normalized = max(-45.0, min(45.0, gaze))
        idx = int((45.0 - normalized) / (span / count))
        return max(0, min(count - 1, idx))

    gaze = _gaze_x(yaw, iris_x)
    if count == 2:
        if gaze <= -_GAZE_THRESHOLD:
            return 0
        if gaze >= _GAZE_THRESHOLD:
            return 1
        return None
    span = 90.0
    normalized = max(-45.0, min(45.0, gaze))
    idx = int((normalized + 45.0) / (span / count))
    return max(0, min(count - 1, idx))


class FocusShiftRewriteApp(rumps.App):
    def __init__(self, camera_index: int) -> None:
        super().__init__("FocusShift", title="⏸ FocusShift")
        self._camera_index = camera_index
        self._tracker = HeadTracker()
        self._switcher = WindowSwitcher()

        self._monitors: list = []
        self._layout = "horizontal"
        self._active = False
        self._stop = threading.Event()
        self._tracking_thread: threading.Thread | None = None

        self._current_screen = 0
        self._candidate_screen: int | None = None
        self._candidate_since = 0.0
        self._last_input_time = 0.0
        self._last_input_sync_time = 0.0
        self._last_monitor_signature: tuple[tuple[int, int, int, int], ...] = ()
        self._pending_title: str | None = None
        self._state_lock = threading.Lock()
        self._kb_started = False
        self._user_paused = False

        self._try_activate()

    def _build_menu(self) -> None:
        self.menu.clear()
        if self._active:
            count = len(self._monitors)
            if self._layout == "vertical":
                screen_name = "Top" if self._current_screen == 0 else ("Bottom" if self._current_screen == count - 1 else f"Screen {self._current_screen + 1}")
            else:
                screen_name = "Left" if self._current_screen == 0 else ("Right" if self._current_screen == count - 1 else f"Screen {self._current_screen + 1}")
            pause_label = "Resume Tracking" if self._user_paused else "Pause Tracking"
            self.menu = [
                rumps.MenuItem(f"Screen: {screen_name}"),
                rumps.MenuItem(f"Layout: {self._layout} · {count} monitors"),
                None,
                rumps.MenuItem(pause_label, callback=self._on_toggle_pause),
                rumps.MenuItem("Refresh Monitors", callback=self._on_refresh_monitors),
                None,
                rumps.MenuItem("Quit", callback=self._on_quit),
            ]
        else:
            self.menu = [
                rumps.MenuItem("No second monitor detected"),
                None,
                rumps.MenuItem("Refresh Monitors", callback=self._on_refresh_monitors),
                None,
                rumps.MenuItem("Quit", callback=self._on_quit),
            ]

    def _on_toggle_pause(self, _sender) -> None:
        self._user_paused = not self._user_paused
        self._build_menu()
        if self._user_paused:
            self._pending_title = "⏸ FocusShift"

    def _on_refresh_monitors(self, _sender) -> None:
        self._deactivate()
        self._try_activate()

    def _try_activate(self) -> None:
        if not is_multi_monitor():
            self.title = "⏸ FocusShift"
            self._active = False
            self._build_menu()
            return

        monitors = get_monitors()
        self._layout = "vertical" if is_vertical_layout(monitors) else "horizontal"
        self._monitors = get_monitors_sorted(monitors)
        self._last_monitor_signature = _monitor_signature(self._monitors)
        self._switcher.start(self._monitors)
        current = self._switcher.current_screen()
        self._current_screen = current if current is not None else 0
        self._candidate_screen = None
        self._candidate_since = 0.0
        self._active = True
        self._stop.clear()
        self._tracking_thread = threading.Thread(
            target=self._tracking_loop,
            daemon=True,
            name="tracking-rewrite",
        )
        self._tracking_thread.start()
        self._build_menu()
        _debug(
            f"Activated layout={self._layout} monitors={len(self._monitors)} "
            f"current_screen={self._current_screen}"
        )

    def _deactivate(self) -> None:
        self._stop.set()
        if self._tracking_thread is not None:
            self._tracking_thread.join(timeout=2.0)
            self._tracking_thread = None
        self._switcher.stop()
        self._active = False
        self.title = "⏸ FocusShift"

    @rumps.timer(0.5)
    def _kb_start_timer(self, _sender) -> None:
        if not self._kb_started:
            self._kb_started = True
            self._start_input_listener()

    @rumps.timer(0.1)
    def _title_flush(self, _sender) -> None:
        title = self._pending_title
        if title is not None and title != self.title:
            self.title = title
            self._pending_title = None

    @rumps.timer(_MONITOR_RECHECK_S)
    def _monitor_recheck(self, _sender) -> None:
        monitors = get_monitors()
        has_multi = len(monitors) >= 2
        signature = _monitor_signature(monitors)
        if self._active:
            if not has_multi:
                self._deactivate()
                self._build_menu()
                return
            if signature != self._last_monitor_signature:
                _debug("Monitor layout changed; restarting rewrite app.")
                self._deactivate()
                self._try_activate()
            return
        if has_multi:
            self._try_activate()

    def _tracking_loop(self) -> None:
        cap = cv2.VideoCapture(self._camera_index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if not cap.isOpened():
            print(f"[FocusShift] Could not open camera index {self._camera_index}", flush=True)
            return

        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue

                result = self._tracker.process_frame(frame)
                if result is None:
                    continue

                if self._user_paused:
                    self._pending_title = "⏸ FocusShift"
                    self._clear_candidate()
                    continue

                yaw, pitch, iris_x, iris_y = result
                target = _decide_target(
                    yaw,
                    pitch,
                    iris_x,
                    iris_y,
                    len(self._monitors),
                    self._layout,
                )

                with self._state_lock:
                    last_input = self._last_input_time
                    current_screen = self._current_screen

                suppressed = time.monotonic() - last_input < _INPUT_SUPPRESSION_S
                if not suppressed and last_input > self._last_input_sync_time:
                    self._sync_after_manual_input()
                    with self._state_lock:
                        current_screen = self._current_screen
                display_idx = target if target is not None else current_screen
                self._pending_title = _screen_label(
                    display_idx,
                    len(self._monitors),
                    self._layout,
                    paused=suppressed,
                )

                if suppressed:
                    self._clear_candidate()
                    continue

                if target is None or target == current_screen:
                    self._clear_candidate()
                    continue

                fired = self._update_candidate(target)
                if not fired:
                    continue

                switched = self._switcher.switch_to_screen(target)
                if switched:
                    with self._state_lock:
                        self._current_screen = target
                    self._clear_candidate()
                else:
                    _debug(f"Switch failed for target={target}; will retry after dwell.")
                    self._candidate_since = time.monotonic()
        finally:
            cap.release()

    def _update_candidate(self, target: int) -> bool:
        now = time.monotonic()
        if target != self._candidate_screen:
            self._candidate_screen = target
            self._candidate_since = now
            return False
        return now - self._candidate_since >= _DWELL_S

    def _clear_candidate(self) -> None:
        self._candidate_screen = None
        self._candidate_since = 0.0

    def _sync_after_manual_input(self) -> None:
        self._switcher.refresh()
        current = self._switcher.current_screen()
        with self._state_lock:
            if current is not None:
                self._current_screen = current
            self._last_input_sync_time = self._last_input_time

    def _start_input_listener(self) -> None:
        try:
            import HIServices

            if not callable(getattr(HIServices, "AXIsProcessTrusted", None)):
                HIServices.AXIsProcessTrusted = lambda: True
        except Exception:
            pass

        from pynput import keyboard, mouse

        def suppress_switching(*_args, **_kwargs) -> None:
            now = time.monotonic()
            self._switcher.refresh()
            current = self._switcher.current_screen()
            with self._state_lock:
                self._last_input_time = now
                if current is not None:
                    self._current_screen = current
            self._clear_candidate()

        self._press_listener = keyboard.Listener(on_press=suppress_switching)
        self._press_listener.start()
        self._mouse_listener = mouse.Listener(
            on_click=suppress_switching,
            on_scroll=suppress_switching,
        )
        self._mouse_listener.start()

    def _on_quit(self, _sender) -> None:
        self._deactivate()
        self._tracker.close()
        rumps.quit_application()


def main() -> None:
    parser = argparse.ArgumentParser(description="FocusShift — gaze-driven monitor switcher")
    parser.add_argument(
        "--select-camera",
        action="store_true",
        help="Re-run camera selection, save the choice, and exit",
    )
    args = parser.parse_args()

    if args.select_camera:
        idx = _pick_camera_index(force=True)
        if idx is None:
            print("[FocusShift] No working camera found.", flush=True)
        else:
            print(f"[FocusShift] Camera {idx} saved to {_CAMERA_CONFIG}.", flush=True)
        return

    camera_index = _pick_camera_index()
    if camera_index is None:
        print("[FocusShift] No working camera found.", flush=True)
        return
    app = FocusShiftRewriteApp(camera_index=camera_index)
    app.run()


if __name__ == "__main__":
    main()
