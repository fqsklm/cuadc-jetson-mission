from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .time_sync import monotonic_to_unix_ns, sample_clock_pair


@dataclass(frozen=True)
class CameraFrame:
    sequence: int
    monotonic_s: float
    unix_ns: int
    image: object | None
    read_started_monotonic_ns: int
    read_completed_monotonic_ns: int
    timestamp_uncertainty_ns: int
    timestamp_source: str = "v4l2-read-midpoint"
    encoded_jpeg: bytes | None = None


@dataclass(frozen=True)
class DecodedPreview:
    monotonic_ns: int
    image: object


class UsbCamera:
    def __init__(self, config: dict):
        self.config = config
        self.ready = False
        self.error: str | None = None
        self._capture = None
        self._gstreamer = None
        self._latest: CameraFrame | None = None
        self._history: deque[CameraFrame] = deque(maxlen=int(config.get("timestamp_queue_frames", 120)))
        self._previews: deque[DecodedPreview] = deque(maxlen=int(config.get("preview_queue_frames", 12)))
        self._lock = threading.Lock()
        self._preview_ready = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._preview_thread: threading.Thread | None = None

    def start(self) -> None:
        backend = str(self.config.get("backend", "gstreamer")).lower()
        if backend == "gstreamer":
            try:
                self._start_gstreamer()
                return
            except Exception:
                if not bool(self.config.get("allow_opencv_fallback", False)):
                    raise
                self.error = "GStreamer启动失败，已显式回退OpenCV；帧时间精度降低"
        if backend not in {"gstreamer", "opencv"}:
            raise ValueError(f"unsupported camera backend: {backend}")
        self._start_opencv()

    def _start_opencv(self) -> None:
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
        self._thread = threading.Thread(target=self._reader_opencv, name="usb-camera-opencv", daemon=True)
        self._thread.start()

    def _start_gstreamer(self) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        device = str(self.config.get("device", "/dev/video0"))
        width = int(self.config.get("width", 2592))
        height = int(self.config.get("height", 1944))
        fps = int(self.config.get("fps", 30))
        preview_width = int(self.config.get("preview_width", 1024))
        preview_height = int(self.config.get("preview_height", 768))
        pipeline = Gst.parse_launch(
            f"v4l2src device={device} io-mode=2 ! "
            f"image/jpeg,width={width},height={height},framerate={fps}/1 ! tee name=cuadc_tee "
            "cuadc_tee. ! queue max-size-buffers=8 leaky=downstream ! "
            "appsink name=cuadc_capture_sink max-buffers=8 drop=true sync=false "
            "cuadc_tee. ! queue max-size-buffers=2 leaky=downstream ! "
            "nvv4l2decoder mjpeg=1 ! nvvidconv ! "
            f"video/x-raw,format=BGRx,width={preview_width},height={preview_height} ! "
            "appsink name=cuadc_preview_sink max-buffers=2 drop=true sync=false"
        )
        capture_sink = pipeline.get_by_name("cuadc_capture_sink")
        preview_sink = pipeline.get_by_name("cuadc_preview_sink")
        if capture_sink is None or preview_sink is None:
            raise RuntimeError("GStreamer appsink创建失败")
        pipeline.set_state(Gst.State.PLAYING)
        if pipeline.get_state(5 * Gst.SECOND).state != Gst.State.PLAYING:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("GStreamer相机流水线未进入PLAYING")
        self._gstreamer = (Gst, pipeline, capture_sink, preview_sink)
        self._thread = threading.Thread(target=self._reader_gstreamer, name="usb-camera-gstreamer", daemon=True)
        self._preview_thread = threading.Thread(target=self._reader_preview, name="usb-camera-preview", daemon=True)
        self._thread.start()
        self._preview_thread.start()

    def _publish(self, frame: CameraFrame) -> None:
        with self._lock:
            self._latest = frame
            self._history.append(frame)
        self.ready = True

    def _reader_opencv(self) -> None:
        sequence = 0
        while not self._stop.is_set():
            read_started_ns = time.monotonic_ns()
            ok, image = self._capture.read()
            read_completed_ns = time.monotonic_ns()
            if not ok:
                self.error = "USB 摄像头读取失败"
                self.ready = False
                time.sleep(0.1)
                continue
            sequence += 1
            capture_monotonic_ns = (read_started_ns + read_completed_ns) // 2
            clock_pair = sample_clock_pair()
            frame = CameraFrame(
                sequence,
                capture_monotonic_ns / 1_000_000_000.0,
                monotonic_to_unix_ns(capture_monotonic_ns, clock_pair),
                image,
                read_started_ns,
                read_completed_ns,
                (read_completed_ns - read_started_ns) // 2 + clock_pair.sampling_uncertainty_ns,
            )
            self._publish(frame)

    def _reader_gstreamer(self) -> None:
        Gst, pipeline, sink, _preview_sink = self._gstreamer
        sequence = 0
        while not self._stop.is_set():
            read_started_ns = time.monotonic_ns()
            sample = sink.emit("try-pull-sample", Gst.SECOND)
            read_completed_ns = time.monotonic_ns()
            if sample is None:
                self.error = "GStreamer相机帧超时"
                self.ready = False
                continue
            buffer = sample.get_buffer()
            ok, mapped = buffer.map(Gst.MapFlags.READ)
            if not ok:
                self.error = "GStreamer帧内存映射失败"
                self.ready = False
                continue
            try:
                encoded_jpeg = bytes(mapped.data)
            finally:
                buffer.unmap(mapped)

            capture_monotonic_ns, source, uncertainty_ns = self._buffer_timestamp(
                Gst, pipeline, buffer, read_started_ns, read_completed_ns
            )
            clock_pair = sample_clock_pair()
            sequence += 1
            self._publish(
                CameraFrame(
                    sequence,
                    capture_monotonic_ns / 1_000_000_000.0,
                    monotonic_to_unix_ns(capture_monotonic_ns, clock_pair),
                    None,
                    read_started_ns,
                    read_completed_ns,
                    uncertainty_ns + clock_pair.sampling_uncertainty_ns,
                    source,
                    encoded_jpeg,
                )
            )

    def _buffer_timestamp(self, Gst, pipeline, buffer, started_ns: int, completed_ns: int) -> tuple[int, str, int]:
        if buffer.pts == Gst.CLOCK_TIME_NONE:
            return (
                (started_ns + completed_ns) // 2,
                "gstreamer-missing-pts-read-midpoint",
                (completed_ns - started_ns) // 2,
            )
        before_clock_ns = time.monotonic_ns()
        gst_clock_ns = int(pipeline.get_clock().get_time())
        after_clock_ns = time.monotonic_ns()
        gst_minus_monotonic_ns = gst_clock_ns - (before_clock_ns + after_clock_ns) // 2
        return (
            int(pipeline.get_base_time() + buffer.pts - gst_minus_monotonic_ns),
            "v4l2-soe-gstreamer-pts",
            (after_clock_ns - before_clock_ns) // 2
            + int(self.config.get("gstreamer_clock_uncertainty_ns", 1_000_000)),
        )

    def _reader_preview(self) -> None:
        import numpy as np

        Gst, pipeline, _capture_sink, sink = self._gstreamer
        while not self._stop.is_set():
            started_ns = time.monotonic_ns()
            sample = sink.emit("try-pull-sample", Gst.SECOND)
            completed_ns = time.monotonic_ns()
            if sample is None:
                continue
            buffer = sample.get_buffer()
            caps = sample.get_caps().get_structure(0)
            width = int(caps.get_value("width"))
            height = int(caps.get_value("height"))
            ok, mapped = buffer.map(Gst.MapFlags.READ)
            if not ok:
                continue
            try:
                image = np.frombuffer(mapped.data, dtype=np.uint8).reshape(height, width, 4)[:, :, :3].copy()
            finally:
                buffer.unmap(mapped)
            preview_ns, _source, _uncertainty = self._buffer_timestamp(
                Gst, pipeline, buffer, started_ns, completed_ns
            )
            with self._preview_ready:
                self._previews.append(DecodedPreview(preview_ns, image))
                self._preview_ready.notify_all()

    def decoded_image_with_source(self, frame: CameraFrame):
        if frame.image is not None:
            return frame.image, "frame"
        tolerance_ns = int(float(self.config.get("preview_match_tolerance_ms", 2.0)) * 1_000_000)
        wait_deadline = time.monotonic() + float(self.config.get("preview_wait_ms", 20.0)) / 1000.0
        with self._preview_ready:
            while True:
                if self._previews:
                    preview = min(
                        self._previews,
                        key=lambda item: abs(item.monotonic_ns - round(frame.monotonic_s * 1_000_000_000)),
                    )
                    if abs(preview.monotonic_ns - round(frame.monotonic_s * 1_000_000_000)) <= tolerance_ns:
                        return preview.image, "gstreamer-preview"
                remaining = wait_deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._preview_ready.wait(remaining)
        if frame.encoded_jpeg is None:
            raise RuntimeError("camera frame has neither decoded image nor JPEG data")
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(frame.encoded_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"MJPEG decode failed for frame {frame.sequence}")
        return image, "opencv-jpeg-fallback"

    def decoded_image(self, frame: CameraFrame):
        image, _source = self.decoded_image_with_source(frame)
        return image

    def latest(self) -> CameraFrame | None:
        with self._lock:
            return self._latest

    def next_after(self, sequence: int, ready_before_monotonic_s: float) -> CameraFrame | None:
        with self._lock:
            return next(
                (
                    frame
                    for frame in reversed(self._history)
                    if frame.sequence > sequence and frame.monotonic_s <= ready_before_monotonic_s
                ),
                None,
            )

    def save(self, frame: CameraFrame, directory: Path, metadata: dict | None = None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"photo_{frame.unix_ns}_{frame.sequence:08d}.jpg"
        if frame.encoded_jpeg is not None:
            path.write_bytes(frame.encoded_jpeg)
        else:
            import cv2

            if not cv2.imwrite(str(path), frame.image, [cv2.IMWRITE_JPEG_QUALITY, int(self.config.get("jpeg_quality", 92))]):
                raise RuntimeError(f"照片写入失败: {path}")
        sidecar = {
            "schema": "cuadc-photo-time-v1",
            "photo": path.name,
            "sequence": frame.sequence,
            "capture_unix_ns": frame.unix_ns,
            "capture_monotonic_ns": round(frame.monotonic_s * 1_000_000_000),
            "read_started_monotonic_ns": frame.read_started_monotonic_ns,
            "read_completed_monotonic_ns": frame.read_completed_monotonic_ns,
            "camera_timestamp_source": frame.timestamp_source,
            "camera_timestamp_uncertainty_ns": frame.timestamp_uncertainty_ns,
            "saved_unix_ns": time.time_ns(),
        }
        if metadata:
            sidecar.update(metadata)
        sidecar_path = path.with_suffix(".json")
        temporary = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
        temporary.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, sidecar_path)
        return path

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._preview_thread:
            self._preview_thread.join(timeout=2)
        if self._gstreamer:
            Gst, pipeline, _capture_sink, _preview_sink = self._gstreamer
            pipeline.set_state(Gst.State.NULL)
            self._gstreamer = None
        if self._capture:
            self._capture.release()
