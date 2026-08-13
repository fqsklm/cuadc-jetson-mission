#!/usr/bin/env python3
"""Validate a ROS camera_calibration archive using per-view reprojection RMS."""

from __future__ import annotations

import argparse
import io
import math
import tarfile

import cv2
import numpy as np
import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("--size", default="9x6")
    parser.add_argument("--square", type=float, default=0.023)
    parser.add_argument("--max-images", type=int, default=0)
    args = parser.parse_args()
    cols, rows = (int(value) for value in args.size.lower().split("x", 1))

    object_points = np.zeros((cols * rows, 3), np.float32)
    object_points[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * args.square

    with tarfile.open(args.archive, "r:gz") as archive:
        document = yaml.safe_load(archive.extractfile("ost.yaml"))
        k = np.asarray(document["camera_matrix"]["data"], dtype=np.float64).reshape(3, 3)
        d = np.asarray(document["distortion_coefficients"]["data"], dtype=np.float64)
        image_names = sorted(name for name in archive.getnames() if name.endswith(".png"))
        if args.max_images and len(image_names) > args.max_images:
            selected = np.linspace(0, len(image_names) - 1, args.max_images, dtype=int)
            image_names = [image_names[index] for index in selected]
        errors: list[float] = []
        failures: list[str] = []
        for index, name in enumerate(image_names, 1):
            encoded = np.frombuffer(archive.extractfile(name).read(), dtype=np.uint8)
            image = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
            reduced = cv2.resize(image, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
            found, corners = cv2.findChessboardCorners(
                reduced,
                (cols, rows),
                cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE,
            )
            if not found:
                failures.append(name)
                continue
            corners *= 2.0
            corners = cv2.cornerSubPix(
                image,
                corners,
                (11, 11),
                (-1, -1),
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001),
            )
            ok, rvec, tvec = cv2.solvePnP(object_points, corners, k, d)
            if not ok:
                failures.append(name)
                continue
            projected, _ = cv2.projectPoints(object_points, rvec, tvec, k, d)
            residual = corners.reshape(-1, 2) - projected.reshape(-1, 2)
            errors.append(float(math.sqrt(np.mean(np.sum(residual * residual, axis=1)))))
            if index % 10 == 0:
                print(f"processed={index}/{len(image_names)}", flush=True)

    if not errors:
        raise SystemExit("no valid checkerboard samples")
    values = np.asarray(errors)
    print(f"images_total={len(image_names)}")
    print(f"images_valid={len(errors)}")
    print(f"images_failed={len(failures)}")
    print(f"rms_mean_px={values.mean():.6f}")
    print(f"rms_median_px={np.median(values):.6f}")
    print(f"rms_p95_px={np.percentile(values, 95):.6f}")
    print(f"rms_max_px={values.max():.6f}")
    print(f"worst_image={image_names[int(np.argmax(values))] if len(errors) == len(image_names) else 'see per-view rerun'}")
    if failures:
        print("failed_images=" + ",".join(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
