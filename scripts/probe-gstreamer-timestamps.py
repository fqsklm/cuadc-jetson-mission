#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int, default=2592)
    parser.add_argument("--height", type=int, default=1944)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--frames", type=int, default=300)
    args = parser.parse_args()

    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    pipeline = Gst.parse_launch(
        f"v4l2src device={args.device} io-mode=2 ! "
        f"image/jpeg,width={args.width},height={args.height},framerate={args.fps}/1 ! "
        "nvv4l2decoder mjpeg=1 ! nvvidconv ! "
        "video/x-raw,format=BGRx ! appsink name=sink max-buffers=2 drop=true sync=false"
    )
    sink = pipeline.get_by_name("sink")
    pipeline.set_state(Gst.State.PLAYING)
    if pipeline.get_state(5 * Gst.SECOND).state != Gst.State.PLAYING:
        raise RuntimeError("pipeline did not enter PLAYING")

    offsets_ns: list[int] = []
    ages_ms: list[float] = []
    intervals_ms: list[float] = []
    previous_capture_ns = None
    started_ns = time.monotonic_ns()
    try:
        for _ in range(args.frames):
            sample = sink.emit("try-pull-sample", 2 * Gst.SECOND)
            if sample is None:
                raise TimeoutError("appsink sample timeout")
            received_ns = time.monotonic_ns()
            buffer = sample.get_buffer()
            if buffer.pts == Gst.CLOCK_TIME_NONE:
                raise RuntimeError("GStreamer buffer has no PTS")
            capture_ns = int(pipeline.get_base_time() + buffer.pts)
            before = time.monotonic_ns()
            gst_now = int(pipeline.get_clock().get_time())
            after = time.monotonic_ns()
            offsets_ns.append(gst_now - (before + after) // 2)
            ages_ms.append((received_ns - capture_ns) / 1e6)
            if previous_capture_ns is not None:
                intervals_ms.append((capture_ns - previous_capture_ns) / 1e6)
            previous_capture_ns = capture_ns
    finally:
        pipeline.set_state(Gst.State.NULL)

    elapsed_s = (time.monotonic_ns() - started_ns) / 1e9
    output = {
        "frames": args.frames,
        "elapsed_s": elapsed_s,
        "fps": args.frames / elapsed_s,
        "gst_clock_minus_monotonic_ns_median": statistics.median(offsets_ns),
        "gst_clock_minus_monotonic_ns_max_abs": max(abs(value) for value in offsets_ns),
        "capture_to_app_ms_mean": statistics.fmean(ages_ms),
        "capture_to_app_ms_p95": sorted(ages_ms)[int(0.95 * (len(ages_ms) - 1))],
        "frame_interval_ms_mean": statistics.fmean(intervals_ms),
        "frame_interval_ms_min": min(intervals_ms),
        "frame_interval_ms_max": max(intervals_ms),
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
