import math

from cuadc_jetson.geometry import distance_m, pixel_to_ground, point_in_polygon, undistort_normalized_pixel
from cuadc_jetson.types import GeoPoint, Pose


def test_distance_and_polygon():
    origin = GeoPoint(30.0, 120.0)
    near = GeoPoint(30.00001, 120.0)
    assert 1.0 < distance_m(origin, near) < 1.2
    polygon = [GeoPoint(29.9, 119.9), GeoPoint(29.9, 120.1), GeoPoint(30.1, 120.1), GeoPoint(30.1, 119.9)]
    assert point_in_polygon(origin, polygon)
    assert not point_in_polygon(GeoPoint(31.0, 120.0), polygon)


def test_downward_camera_center_projects_under_aircraft():
    pose = Pose(1.0, 30.0, 120.0, 20.0, 0.0, 0.0, 0.0)
    point = pixel_to_ground(pose, 960, 540, 1000, 1000, 960, 540, [0, -90, 0])
    assert point is not None
    assert distance_m(GeoPoint(pose.lat, pose.lon), point) < 0.01


def test_plumb_bob_undistortion_moves_radially_inward_for_positive_k1():
    x, y = undistort_normalized_pixel(1100, 500, 1000, 1000, 500, 500, "plumb_bob", [0.2, 0, 0, 0, 0])
    assert 0 < x < 0.6
    assert abs(y) < 1e-12


def test_equidistant_center_is_stable():
    x, y = undistort_normalized_pixel(500, 500, 1000, 1000, 500, 500, "equidistant", [0.1, 0, 0, 0])
    assert x == 0
    assert y == 0
