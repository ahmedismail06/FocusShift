import math
import time
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as _mpt
from mediapipe.tasks.python import vision as _mpv

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)
MODEL_PATH = Path.home() / ".focusshift" / "face_landmarker.task"


def _eye_relative_iris(lm) -> tuple[float, float]:
    """Iris position normalized within each eye's bounding box, averaged across eyes.

    Returns (x, y) in [0, 1] where 0.5 ≈ looking straight ahead. This is much more
    sensitive to pure eye movement than absolute image-space iris coordinates.
    """
    # MediaPipe FaceMesh: left eye corners 33/133, right eye corners 263/362,
    # left eye top/bottom 159/145, right eye top/bottom 386/374,
    # left iris 468, right iris 473.
    def rel(p, a, b):
        lo, hi = (a, b) if a < b else (b, a)
        if hi - lo < 1e-6:
            return 0.5
        return max(0.0, min(1.0, (p - lo) / (hi - lo)))

    le_x = rel(lm[468].x, lm[33].x, lm[133].x)
    re_x = rel(lm[473].x, lm[263].x, lm[362].x)
    le_y = rel(lm[468].y, lm[159].y, lm[145].y)
    re_y = rel(lm[473].y, lm[386].y, lm[374].y)
    return (le_x + re_x) / 2.0, (le_y + re_y) / 2.0


def _ensure_model() -> str:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MODEL_PATH.exists():
        print(f"Downloading MediaPipe face landmarker model (~3 MB) to {MODEL_PATH} …")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("Download complete.")
    return str(MODEL_PATH)


class HeadTracker:
    def __init__(self) -> None:
        model_path = _ensure_model()
        options = _mpv.FaceLandmarkerOptions(
            base_options=_mpt.BaseOptions(model_asset_path=model_path),
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,
            num_faces=1,
            running_mode=_mpv.RunningMode.VIDEO,
        )
        self._landmarker = _mpv.FaceLandmarker.create_from_options(options)

    def process_frame(
        self, bgr_frame: np.ndarray
    ) -> tuple[float, float, float, float] | None:
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = int(time.monotonic() * 1000)

        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)

        if not result.face_landmarks or not result.facial_transformation_matrixes:
            return None

        # Yaw and pitch from the 4x4 facial transformation matrix.
        # Sign convention: positive yaw = looking right, positive pitch = looking up.
        R = np.array(result.facial_transformation_matrixes[0])[:3, :3]
        yaw_deg = -math.degrees(
            math.atan2(-R[2, 0], math.sqrt(R[2, 1] ** 2 + R[2, 2] ** 2))
        )
        pitch_deg = math.degrees(math.atan2(R[2, 1], R[2, 2]))

        # Iris position relative to each eye's bounding box — far more sensitive
        # than the absolute image-space iris position. 0.5 = looking forward.
        lm = result.face_landmarks[0]
        if len(lm) >= 478:
            iris_x, iris_y = _eye_relative_iris(lm)
        else:
            iris_x = iris_y = 0.5

        return yaw_deg, pitch_deg, iris_x, iris_y

    def close(self) -> None:
        self._landmarker.close()


class DebounceTracker:
    def __init__(self, threshold_s: float = 0.6) -> None:
        self._threshold = threshold_s
        self._last: int | None = None
        self._since: float = 0.0
        self._fired: bool = False

    def update(self, predicted: int) -> int | None:
        now = time.monotonic()
        if predicted != self._last:
            self._last = predicted
            self._since = now
            self._fired = False
            return None
        if not self._fired and now - self._since >= self._threshold:
            self._fired = True
            return predicted
        return None

    def reset(self) -> None:
        self._last = None
        self._since = 0.0
        self._fired = False
