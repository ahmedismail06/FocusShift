from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass

try:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowListExcludeDesktopElements,
        kCGWindowListOptionOnScreenOnly,
    )

    _QUARTZ_OK = True
except ImportError:
    _QUARTZ_OK = False

try:
    from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

    _APPKIT_OK = True
except ImportError:
    _APPKIT_OK = False

try:
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        AXUIElementCreateSystemWide,
        AXUIElementCopyElementAtPosition,
        AXUIElementGetPid,
        AXUIElementPerformAction,
        AXUIElementSetAttributeValue,
        AXValueGetValue,
    )

    kAXFocusedAttribute = "AXFocused"
    kAXMainAttribute = "AXMain"
    kAXPositionAttribute = "AXPosition"
    kAXRaiseAction = "AXRaise"
    kAXSizeAttribute = "AXSize"
    kAXValueCGPointType = 1
    kAXValueCGSizeType = 2
    kAXWindowsAttribute = "AXWindows"
    _AX_OK = True
except ImportError:
    _AX_OK = False

_ACTIVATE_SETTLE_S = 0.08
_SKIP_APPS = frozenset({"", "Dock", "Finder", "SystemUIServer", "Window Server"})
_DEBUG = os.environ.get("FOCUSSHIFT_DEBUG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _debug(msg: str) -> None:
    if _DEBUG:
        print(f"[FocusShift:rewrite:switcher] {msg}", flush=True)


@dataclass(frozen=True)
class WindowEntry:
    app_name: str
    pid: int
    bounds: dict

    @property
    def center(self) -> tuple[float, float]:
        return (
            self.bounds.get("X", 0.0) + self.bounds.get("Width", 0.0) / 2.0,
            self.bounds.get("Y", 0.0) + self.bounds.get("Height", 0.0) / 2.0,
        )


class WindowSwitcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._monitors: list = []
        self._windows: list[WindowEntry] = []
        self._top_window_by_screen: dict[int, WindowEntry] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, monitors: list) -> None:
        with self._lock:
            self._monitors = list(monitors)
        self.refresh()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="window-poll-rewrite",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def update_monitors(self, monitors: list) -> None:
        with self._lock:
            self._monitors = list(monitors)
        self.refresh()

    def refresh(self) -> None:
        windows, top_window_by_screen = self._scan_windows()
        with self._lock:
            self._windows = windows
            self._top_window_by_screen = top_window_by_screen

    def current_screen(self) -> int | None:
        self.refresh()
        with self._lock:
            windows = list(self._windows)
        if not windows:
            return None
        return self._screen_for_bounds(windows[0].bounds)

    def switch_to_screen(self, screen_idx: int) -> bool:
        self.refresh()
        with self._lock:
            monitors = list(self._monitors)
            windows = list(self._windows)
            target = self._top_window_by_screen.get(screen_idx)

        if not monitors:
            _debug("No monitors available.")
            return False
        if not (0 <= screen_idx < len(monitors)):
            _debug(f"Screen index {screen_idx} is out of range.")
            return False
        if target is None:
            target = self._closest_window_on_screen(windows, screen_idx)
        if target is None:
            _debug(f"No visible window found on screen {screen_idx}.")
            return False

        target_x, target_y = target.center
        activated = self._activate_pid(target.pid)
        raised = self._raise_window(target.pid, target_x, target_y, target.bounds)

        # Quartz window ordering is not a reliable proxy for keyboard focus on macOS,
        # so treat a successful AX raise as success even if the post-check disagrees.
        self.refresh()
        after = self._frontmost_screen_without_refresh()
        success = raised or activated or after == screen_idx
        _debug(
            f"Switch screen={screen_idx} app={target.app_name!r} pid={target.pid} "
            f"activated={activated} raised={raised} after={after} success={success}"
        )
        return success

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            time.sleep(0.25)

    def _frontmost_screen_without_refresh(self) -> int | None:
        with self._lock:
            if not self._windows:
                return None
            bounds = self._windows[0].bounds
        return self._screen_for_bounds(bounds)

    def _scan_windows(self) -> tuple[list[WindowEntry], dict[int, WindowEntry]]:
        if not _QUARTZ_OK:
            return [], {}
        try:
            raw = CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
                kCGNullWindowID,
            )
        except Exception as exc:
            _debug(f"Quartz scan failed: {exc!r}")
            return [], {}

        windows: list[WindowEntry] = []
        top_window_by_screen: dict[int, WindowEntry] = {}
        for item in raw or []:
            if item.get("kCGWindowLayer", 1) != 0:
                continue
            if not item.get("kCGWindowIsOnscreen", False):
                continue
            app_name = item.get("kCGWindowOwnerName", "")
            if not app_name or app_name in _SKIP_APPS:
                continue
            bounds = dict(item.get("kCGWindowBounds", {}))
            if bounds.get("Width", 0) <= 0 or bounds.get("Height", 0) <= 0:
                continue
            entry = WindowEntry(
                app_name=app_name,
                pid=int(item.get("kCGWindowOwnerPID", 0)),
                bounds=bounds,
            )
            windows.append(entry)
            screen_idx = self._screen_for_bounds(bounds)
            if screen_idx not in top_window_by_screen:
                top_window_by_screen[screen_idx] = entry

        return windows, top_window_by_screen

    def _screen_for_bounds(self, bounds: dict) -> int:
        with self._lock:
            monitors = list(self._monitors)
        if not monitors:
            return 0

        # screeninfo returns NSScreen y-up coordinates (y=0 at primary bottom-left),
        # but Quartz window bounds use y-down (y=0 at primary top-left). Convert the
        # window center to NSScreen y so the range checks against monitor bounds work.
        primary_h = next(m.height for m in monitors if m.y == min(m.y for m in monitors))
        center_x = bounds.get("X", 0.0) + bounds.get("Width", 0.0) / 2.0
        center_y = primary_h - (bounds.get("Y", 0.0) + bounds.get("Height", 0.0) / 2.0)

        best_idx = 0
        best_dist = float("inf")
        for idx, monitor in enumerate(monitors):
            if (
                monitor.x <= center_x < monitor.x + monitor.width
                and monitor.y <= center_y < monitor.y + monitor.height
            ):
                return idx
            monitor_x = monitor.x + monitor.width / 2.0
            monitor_y = monitor.y + monitor.height / 2.0
            dist = abs(center_x - monitor_x) + abs(center_y - monitor_y)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        return best_idx

    def _closest_window_on_screen(
        self, windows: list[WindowEntry], screen_idx: int
    ) -> WindowEntry | None:
        best: WindowEntry | None = None
        best_dist = float("inf")
        with self._lock:
            monitors = list(self._monitors)
        if not (0 <= screen_idx < len(monitors)):
            return None
        monitor = monitors[screen_idx]
        target_x = monitor.x + monitor.width / 2.0
        target_y = monitor.y + monitor.height / 2.0

        for window in windows:
            if self._screen_for_bounds(window.bounds) != screen_idx:
                continue
            center_x, center_y = window.center
            dist = abs(center_x - target_x) + abs(center_y - target_y)
            if dist < best_dist:
                best_dist = dist
                best = window
        return best

    def _activate_pid(self, pid: int) -> bool:
        if not _APPKIT_OK:
            return False
        running = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if running is None:
            return False
        try:
            activated = bool(
                running.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            )
        except Exception as exc:
            _debug(f"App activation failed for pid={pid}: {exc!r}")
            activated = False
        if not activated:
            activated = self._activate_via_applescript(running)
        if activated:
            time.sleep(_ACTIVATE_SETTLE_S)
        return activated

    def _activate_via_applescript(self, running) -> bool:
        try:
            bundle_id = str(running.bundleIdentifier() or "").strip()
            app_name = str(running.localizedName() or "").strip()
        except Exception as exc:
            _debug(f"Could not inspect running app for AppleScript fallback: {exc!r}")
            return False

        target = bundle_id if bundle_id else app_name
        if not target:
            return False

        if bundle_id:
            script = f'tell application id "{bundle_id}" to activate'
        else:
            escaped_name = app_name.replace('"', '\\"')
            script = f'tell application "{escaped_name}" to activate'

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
        except Exception as exc:
            _debug(f"AppleScript activate failed for {target!r}: {exc!r}")
            return False

        if result.returncode != 0:
            _debug(
                f"AppleScript activate returned {result.returncode} for {target!r}: "
                f"{result.stderr.strip()}"
            )
            return False
        _debug(f"AppleScript activate succeeded for {target!r}.")
        return True

    def _raise_window(
        self,
        pid: int,
        target_x: float,
        target_y: float,
        bounds: dict | None = None,
    ) -> bool:
        if not _AX_OK:
            return False
        try:
            app_ref = AXUIElementCreateApplication(pid)
        except Exception as exc:
            _debug(f"AX setup failed for pid={pid}: {exc!r}")
            return False

        windows = None
        err = 0
        for _ in range(3):
            err, windows = AXUIElementCopyAttributeValue(app_ref, kAXWindowsAttribute, None)
            if err == 0 and windows:
                break
            if err != 0 and err != -25211:
                break
            time.sleep(0.1)

        if not windows:
            _debug(f"AX window fetch failed for pid={pid} err={err}; trying position fallback.")
            return self._raise_from_position(pid, target_x, target_y, bounds)

        best = None
        best_dist = float("inf")
        for window_ref in windows:
            err_pos, pos_value = AXUIElementCopyAttributeValue(
                window_ref,
                kAXPositionAttribute,
                None,
            )
            if err_pos != 0 or pos_value is None:
                continue
            ok_pos, point = AXValueGetValue(pos_value, kAXValueCGPointType, None)
            if not ok_pos or point is None:
                continue

            err_size, size_value = AXUIElementCopyAttributeValue(
                window_ref,
                kAXSizeAttribute,
                None,
            )
            width = 0.0
            height = 0.0
            if err_size == 0 and size_value is not None:
                ok_size, size = AXValueGetValue(size_value, kAXValueCGSizeType, None)
                if ok_size and size is not None:
                    width = float(size.width)
                    height = float(size.height)

            center_x = float(point.x) + width / 2.0
            center_y = float(point.y) + height / 2.0
            dist = abs(center_x - target_x) + abs(center_y - target_y)
            if dist < best_dist:
                best_dist = dist
                best = window_ref

        if best is None:
            return False

        try:
            AXUIElementPerformAction(best, kAXRaiseAction)
        except Exception as exc:
            _debug(f"AXRaise failed for pid={pid}: {exc!r}")
            return False
        try:
            AXUIElementSetAttributeValue(best, kAXMainAttribute, True)
        except Exception:
            pass
        try:
            AXUIElementSetAttributeValue(best, kAXFocusedAttribute, True)
        except Exception:
            pass
        return True

    def _raise_from_position(
        self,
        pid: int,
        target_x: float,
        target_y: float,
        bounds: dict | None,
    ) -> bool:
        try:
            sys_wide = AXUIElementCreateSystemWide()
            if bounds:
                probe_x = bounds.get("X", 0.0) + bounds.get("Width", 0.0) / 2.0
                probe_y = bounds.get("Y", 0.0) + bounds.get("Height", 0.0) / 2.0
            else:
                probe_x = target_x
                probe_y = target_y
            err, element = AXUIElementCopyElementAtPosition(
                sys_wide,
                probe_x,
                probe_y,
                None,
            )
            if err != 0 or element is None:
                return False
            pid_err, element_pid = AXUIElementGetPid(element, None)
            if pid_err != 0 or element_pid != pid:
                return False
            AXUIElementPerformAction(element, kAXRaiseAction)
            try:
                AXUIElementSetAttributeValue(element, kAXMainAttribute, True)
            except Exception:
                pass
            try:
                AXUIElementSetAttributeValue(element, kAXFocusedAttribute, True)
            except Exception:
                pass
            return True
        except Exception as exc:
            _debug(f"Position fallback failed for pid={pid}: {exc!r}")
            return False
