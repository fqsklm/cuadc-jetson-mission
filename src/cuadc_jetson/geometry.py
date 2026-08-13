from __future__ import annotations

import math
from collections.abc import Sequence

from .types import GeoPoint, Pose

EARTH_RADIUS_M = 6_378_137.0


def undistort_normalized_pixel(
    pixel_x: float,
    pixel_y: float,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    distortion_model: str = "plumb_bob",
    distortion_coefficients: Sequence[float] = (),
) -> tuple[float, float]:
    """Convert a raw-image pixel to an undistorted normalized camera ray.

    Supports the ROS CameraInfo models produced by camera_calibration:
    plumb_bob/rational_polynomial and equidistant (fisheye).
    """
    xd = (pixel_x - cx) / fx
    yd = (pixel_y - cy) / fy
    d = [float(value) for value in distortion_coefficients]
    if not d or not any(abs(value) > 1e-15 for value in d):
        return xd, yd

    if distortion_model == "equidistant":
        rd = math.hypot(xd, yd)
        if rd < 1e-15:
            return xd, yd
        k = (d + [0.0] * 4)[:4]
        theta = rd
        for _ in range(10):
            theta2 = theta * theta
            polynomial = 1.0 + k[0] * theta2 + k[1] * theta2**2 + k[2] * theta2**3 + k[3] * theta2**4
            derivative = 1.0 + 3.0 * k[0] * theta2 + 5.0 * k[1] * theta2**2 + 7.0 * k[2] * theta2**3 + 9.0 * k[3] * theta2**4
            if abs(derivative) < 1e-12:
                break
            theta -= (theta * polynomial - rd) / derivative
        scale = math.tan(theta) / rd
        return xd * scale, yd * scale

    if distortion_model not in {"plumb_bob", "rational_polynomial"}:
        raise ValueError(f"unsupported ROS distortion model: {distortion_model}")
    k1, k2, p1, p2, k3, k4, k5, k6 = (d + [0.0] * 8)[:8]
    x, y = xd, yd
    for _ in range(10):
        r2 = x * x + y * y
        numerator = 1.0 + k1 * r2 + k2 * r2**2 + k3 * r2**3
        denominator = 1.0 + k4 * r2 + k5 * r2**2 + k6 * r2**3
        radial = numerator / denominator if abs(denominator) > 1e-12 else numerator
        if abs(radial) < 1e-12:
            break
        delta_x = 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
        delta_y = p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
        x = (xd - delta_x) / radial
        y = (yd - delta_y) / radial
    return x, y


def distance_m(a: GeoPoint, b: GeoPoint) -> float:
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def offset_point(origin: GeoPoint, north_m: float, east_m: float) -> GeoPoint:
    lat = origin.lat + math.degrees(north_m / EARTH_RADIUS_M)
    lon = origin.lon + math.degrees(east_m / (EARTH_RADIUS_M * math.cos(math.radians(origin.lat))))
    return GeoPoint(lat, lon, origin.alt_rel_m)


def point_in_polygon(point: GeoPoint, polygon: Sequence[GeoPoint]) -> bool:
    if len(polygon) < 3:
        return False
    inside = False
    j = len(polygon) - 1
    for i, current in enumerate(polygon):
        previous = polygon[j]
        if ((current.lon > point.lon) != (previous.lon > point.lon)) and (
            point.lat
            < (previous.lat - current.lat) * (point.lon - current.lon)
            / ((previous.lon - current.lon) or 1e-15)
            + current.lat
        ):
            inside = not inside
        j = i
    return inside


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _matvec(a: list[list[float]], v: list[float]) -> list[float]:
    return [sum(a[i][k] * v[k] for k in range(3)) for i in range(3)]


def pixel_to_ground(
    pose: Pose,
    pixel_x: float,
    pixel_y: float,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    camera_to_body_rpy_deg: Sequence[float],
    distortion_model: str = "plumb_bob",
    distortion_coefficients: Sequence[float] = (),
) -> GeoPoint | None:
    """Project a pixel ray onto the flat ground plane below the aircraft.

    Body axes are forward/right/down (NED convention). Camera optical axes are
    right/down/forward. The configurable camera rotation is applied after that
    fixed optical-to-body mapping. Calibration and flight validation are still
    mandatory; this function deliberately refuses rays above the horizon.
    """
    ray_x, ray_y = undistort_normalized_pixel(
        pixel_x, pixel_y, fx, fy, cx, cy, distortion_model, distortion_coefficients
    )
    ray_camera = [ray_x, ray_y, 1.0]
    optical_to_body = [[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    cr, cp, cyaw = (math.radians(float(x)) for x in camera_to_body_rpy_deg)

    def rotation(roll: float, pitch: float, yaw: float) -> list[list[float]]:
        sr, cr_ = math.sin(roll), math.cos(roll)
        sp, cp_ = math.sin(pitch), math.cos(pitch)
        sy, cy_ = math.sin(yaw), math.cos(yaw)
        return [
            [cp_ * cy_, sr * sp * cy_ - cr_ * sy, cr_ * sp * cy_ + sr * sy],
            [cp_ * sy, sr * sp * sy + cr_ * cy_, cr_ * sp * sy - sr * cy_],
            [-sp, sr * cp_, cr_ * cp_],
        ]

    body_ray = _matvec(_matmul(rotation(cr, cp, cyaw), optical_to_body), ray_camera)
    ned_ray = _matvec(rotation(pose.roll_rad, pose.pitch_rad, pose.yaw_rad), body_ray)
    if ned_ray[2] <= 0.05 or pose.alt_rel_m <= 0:
        return None
    scale = pose.alt_rel_m / ned_ray[2]
    return offset_point(GeoPoint(pose.lat, pose.lon), ned_ray[0] * scale, ned_ray[1] * scale)
