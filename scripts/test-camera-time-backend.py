#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import time

from cuadc_jetson.camera import UsbCamera


def percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[min(len(values) - 1, int(fraction * (len(values) - 1)))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int, default=2592)
    parser.add_argument("--height", type=int, default=1944)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--warmup-frames", type=int, default=30)
    parser.add_argument("--decode-hz", type=float, default=0.0)
    args = parser.parse_args()
    camera = UsbCamera(
        {
            "device": args.device,
            "backend": "gstreamer",
            "allow_opencv_fallback": False,
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "timestamp_queue_frames": max(120, args.frames + args.warmup_frames),
        }
    )
    frames = []
    last_sequence = 0
    last_decoded_sequence = 0
    last_decode_monotonic_s = float("-inf")
    decode_ms: list[float] = []
    decode_match_failures = 0
    deadline = time.monotonic() + max(30.0, args.frames / args.fps * 3)
    try:
        camera.start()
        while len(frames) < args.frames + args.warmup_frames and time.monotonic() < deadline:
            frame = camera.latest()
            if frame and frame.sequence != last_sequence:
                frames.append(frame)
                last_sequence = frame.sequence
            decode_frame = camera.next_after(last_decoded_sequence, time.monotonic() - 0.080)
            if (
                decode_frame
                and args.decode_hz > 0
                and decode_frame.monotonic_s - last_decode_monotonic_s >= 1.0 / args.decode_hz
            ):
                last_decoded_sequence = decode_frame.sequence
                try:
                    before_decode = time.monotonic()
                    camera.decoded_image(decode_frame)
                    decode_ms.append((time.monotonic() - before_decode) * 1000)
                    last_decode_monotonic_s = decode_frame.monotonic_s
                except RuntimeError:
                    decode_match_failures += 1
            else:
                time.sleep(0.0005)
    finally:
        camera.close()
    if len(frames) < args.frames + args.warmup_frames:
        raise TimeoutError(f"received only {len(frames)} frames")
    frames = frames[args.warmup_frames:]
    intervals_ms = [(b.monotonic_s - a.monotonic_s) * 1000 for a, b in zip(frames, frames[1:])]
    elapsed_s = frames[-1].monotonic_s - frames[0].monotonic_s
    ages_ms = [(time.time_ns() - frame.unix_ns) / 1e6 for frame in frames]
    uncertainties_ms = [frame.timestamp_uncertainty_ns / 1e6 for frame in frames]
    output = {
        "frames": len(frames),
        "timestamp_source": sorted({frame.timestamp_source for frame in frames}),
        "capture_fps": (frames[-1].sequence - frames[0].sequence) / elapsed_s,
        "consumer_observed_fps": (len(frames) - 1) / elapsed_s,
        "consumer_skipped_frames": frames[-1].sequence - frames[0].sequence - len(frames) + 1,
        "frame_interval_ms_mean": statistics.fmean(intervals_ms),
        "frame_interval_ms_p95": percentile(intervals_ms, 0.95),
        "timestamp_uncertainty_ms_p95": percentile(uncertainties_ms, 0.95),
        "oldest_frame_age_at_report_ms": max(ages_ms),
        "newest_frame_age_at_report_ms": min(ages_ms),
        "sequence_first": frames[0].sequence,
        "sequence_last": frames[-1].sequence,
        "decoded_frames": len(decode_ms),
        "decode_ms_mean": statistics.fmean(decode_ms) if decode_ms else None,
        "decode_ms_p95": percentile(decode_ms, 0.95) if decode_ms else None,
        "decode_match_failures": decode_match_failures,
    }
    output["pass"] = bool(
        output["capture_fps"] >= args.fps * 0.98
        and output["timestamp_source"] == ["v4l2-soe-gstreamer-pts"]
        and output["timestamp_uncertainty_ms_p95"] <= 2.0
        and decode_match_failures == 0
    )
    print(json.dumps(output, indent=2))
    return 0 if output["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
