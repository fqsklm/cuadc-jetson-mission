import json

import pytest

from cuadc_jetson.config import ConfigError, load_config


def example_config():
    return {
        "dry_run": True,
        "camera": {"device": "/dev/video0", "width": 1920, "height": 1080},
        "vision": {"target_model": "target.engine", "people_model": "", "selection": "max_numeric", "fx": 1000, "fy": 1000, "cx": 960, "cy": 540},
        "flight": {"firmware": "ardupilot", "device": "/dev/cuadc-fc", "baud": 921600},
        "mission": {"dynamic_upload_enabled": False, "survey_mission_file": "", "landing_mission_file": "", "ingress": [], "egress": []},
        "safety": {"release_polygon": []},
        "release": {"enabled": False},
        "storage": {},
    }


def write(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_safe_template_is_valid(tmp_path):
    assert load_config(write(tmp_path, example_config())).dry_run


@pytest.mark.parametrize("missing", ["people_model", "survey_mission_file", "landing_mission_file"])
def test_release_refuses_missing_safety_artifacts(tmp_path, missing):
    data = example_config()
    data["dry_run"] = False
    data["release"] = {
        "enabled": True,
        "require_rc_authorization": True,
        "servo_channel": 9,
    }
    data["mission"]["dynamic_upload_enabled"] = True
    data["vision"]["people_model"] = "people.engine"
    survey = tmp_path / "survey.waypoints"
    landing = tmp_path / "landing.waypoints"
    survey.write_text("QGC WPL 110\n1\t0\t3\t22\t0\t0\t0\t0\t30\t120\t30\t1\n", encoding="utf-8")
    landing.write_text("QGC WPL 110\n1\t0\t3\t21\t0\t0\t0\t0\t30\t120\t0\t1\n", encoding="utf-8")
    data["mission"]["survey_mission_file"] = str(survey)
    data["mission"]["landing_mission_file"] = str(landing)
    data["mission"]["ingress"] = [[30.0, 120.0, 20.0]]
    data["mission"]["egress"] = [[30.0, 120.001, 20.0]]
    data["safety"]["release_polygon"] = [[30, 120], [30, 121], [31, 121]]
    if missing == "people_model":
        data["vision"][missing] = ""
    else:
        data["mission"][missing] = ""
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))


def test_release_refuses_dry_run_contradiction(tmp_path):
    data = example_config()
    data["release"] = {"enabled": True}
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))


def test_dynamic_upload_refuses_missing_route_files(tmp_path):
    data = example_config()
    data["dry_run"] = False
    data["mission"]["dynamic_upload_enabled"] = True
    data["safety"]["release_polygon"] = [[30, 120], [30, 121], [31, 121]]
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))


def test_time_sync_rejects_negative_threshold(tmp_path):
    data = example_config()
    data["time_sync"] = {"max_offset_ms": -1}
    with pytest.raises(ConfigError, match="max_offset_ms"):
        load_config(write(tmp_path, data))
