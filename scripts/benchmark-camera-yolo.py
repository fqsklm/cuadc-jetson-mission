#!/usr/bin/env python3
"""Benchmark a real UVC camera and TensorRT YOLO engine end to end on Jetson."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
from dataclasses import dataclass


@dataclass
class LatestFrame:
    sequence: int = 0
    captured_s: float = 0.0
    image: object | None = None


class CaptureThread:
    def __init__(self, capture):
        self.capture = capture
        self.latest = LatestFrame()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.failures = 0
        self.started_s = 0.0
        self.ended_s = 0.0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.started_s = time.perf_counter()
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            ok, image = self.capture.read()
            now = time.perf_counter()
            if not ok:
                self.failures += 1
                continue
            with self.lock:
                self.latest = LatestFrame(self.latest.sequence + 1, now, image)
        self.ended_s = time.perf_counter()

    def get(self):
        with self.lock:
            return self.latest

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        self.capture.release()


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def open_camera(args):
    import cv2

    device = int(args.device) if args.device.isdigit() else args.device
    capture = cv2.VideoCapture(device, cv2.CAP_V4L2)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    capture.set(cv2.CAP_PROP_FPS, args.camera_fps)
    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc))
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open camera {args.device}")
    actual = {
        "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps_reported": capture.get(cv2.CAP_PROP_FPS),
        "fourcc_reported": int(capture.get(cv2.CAP_PROP_FOURCC)),
    }
    return capture, actual


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument(
        "--model",
        help="TensorRT .engine generated on this Jetson; omit for camera/decode-only testing",
    )
    parser.add_argument("--width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=1440)
    parser.add_argument("--camera-fps", type=int, default=30)
    parser.add_argument("--fourcc", default="MJPG")
    parser.add_argument("--imgsz", type=int, default=1024)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--warmup", type=int, default=30)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--target-fps", type=float, default=30.0)
    args = parser.parse_args()

    capture, actual = open_camera(args)
    producer = CaptureThread(capture)
    model = None
    if args.model:
        from ultralytics import YOLO

        model = YOLO(args.model)
    producer.start()
    deadline = time.perf_counter() + 5
    frame = producer.get()
    while frame.image is None and time.perf_counter() < deadline:
        time.sleep(0.01)
        frame = producer.get()
    if frame.image is None:
        producer.close()
        raise RuntimeError("camera produced no frames")

    predict_args = dict(imgsz=args.imgsz, conf=args.confidence, verbose=False)
    if model is not None:
        for _ in range(args.warmup):
            model.predict(frame.image, **predict_args)
            frame = producer.get()

    start_s = time.perf_counter()
    end_s = start_s + args.seconds
    processed = 0
    last_sequence = 0
    first_sequence = 0
    inference_ms: list[float] = []
    e2e_age_ms: list[float] = []
    while time.perf_counter() < end_s:
        frame = producer.get()
        if frame.image is None or frame.sequence == last_sequence:
            time.sleep(0.0005)
            continue
        if first_sequence == 0:
            first_sequence = frame.sequence
        last_sequence = frame.sequence
        before = time.perf_counter()
        if model is not None:
            model.predict(frame.image, **predict_args)
        after = time.perf_counter()
        inference_ms.append((after - before) * 1000)
        e2e_age_ms.append((after - frame.captured_s) * 1000)
        processed += 1

    elapsed = time.perf_counter() - start_s
    final_sequence = producer.get().sequence
    captured_during_test = max(0, final_sequence - first_sequence + 1)
    producer.close()
    processed_fps = processed / elapsed
    camera_elapsed = max(1e-9, producer.ended_s - producer.started_s)
    camera_fps = final_sequence / camera_elapsed
    result = {
        "camera_requested": {
            "device": args.device,
            "width": args.width,
            "height": args.height,
            "fps": args.camera_fps,
            "fourcc": args.fourcc,
        },
        "camera_actual": actual,
        "model": args.model or "camera-only",
        "imgsz": args.imgsz,
        "duration_s": elapsed,
        "camera_fps_observed": camera_fps,
        "processed_fps": processed_fps,
        "processed_frames": processed,
        "capture_frames_during_test": captured_during_test,
        "capture_frames_skipped": max(0, captured_during_test - processed),
        "skip_ratio": max(0.0, (captured_during_test - processed) / max(1, captured_during_test)),
        "inference_ms_mean": statistics.fmean(inference_ms),
        "inference_ms_p95": percentile(inference_ms, 0.95),
        "capture_to_result_ms_p95": percentile(e2e_age_ms, 0.95),
        "capture_failures": producer.failures,
    }
    period_ms = 1000.0 / args.target_fps
    result["pass"] = bool(
        actual["width"] == args.width
        and actual["height"] == args.height
        and camera_fps >= args.target_fps * 0.98
        and processed_fps >= args.target_fps * 0.98
        and result["capture_to_result_ms_p95"] <= period_ms
        and result["skip_ratio"] <= 0.01
        and producer.failures == 0
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
