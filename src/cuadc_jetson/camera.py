from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CameraFrame:
    sequence: int
    monotonic_s: float
    unix_ns: int
    image: object


class UsbCamera:
    def __init__(self, config: dict):
        self.config = config
        self.ready = False
        self.error: str | None = None
        self._capture = None
        self._latest: CameraFrame | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import cv2

        device = self.config.get("device", "/dev/video0")
        try:
            index = int(device) if str(device).isdigit() else device
        except ValueError:
            index = device
        capture = cv2.VideoCapture(index, cv2.CAP_V4L2)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.config.get("width", 1920)))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.config.get("height", 1080)))
        capture.set(cv2.CAP_PROP_FPS, int(self.config.get("fps", 30)))
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.config.get("fourcc", "MJPG")))
        if not capture.isOpened():
            raise RuntimeError(f"无法打开 USB 摄像头 {device}")
        self._capture = capture
        self._thread = threading.Thread(target=self._reader, name="usb-camera", daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        sequence = 0
        while not self._stop.is_set():
            ok, image = self._capture.read()
            if not ok:
                self.error = "USB 摄像头读取失败"
                self.ready = False
                time.sleep(0.1)
                continue
            sequence += 1
            frame = CameraFrame(sequence, time.monotonic(), time.time_ns(), image)
            with self._lock:
                self._latest = frame
            self.ready = True

    def latest(self) -> CameraFrame | None:
        with self._lock:
            return self._latest

    def save(self, frame: CameraFrame, directory: Path) -> Path:
        import cv2

        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"photo_{frame.unix_ns}_{frame.sequence:08d}.jpg"
        if not cv2.imwrite(str(path), frame.image, [cv2.IMWRITE_JPEG_QUALITY, int(self.config.get("jpeg_quality", 92))]):
            raise RuntimeError(f"照片写入失败: {path}")
        return path

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._capture:
            self._capture.release()

