import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "import-ros-calibration.py"
PROJECT_CONFIG = Path(__file__).parents[1] / "config" / "config.example.json"
PROJECT_CALIBRATION = Path(__file__).parents[1] / "config" / "cuadc_hdr_camera-2592x1944.yaml"


def load_importer():
    spec = importlib.util.spec_from_file_location("import_ros_calibration", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_calibration(path: Path, width=1920, height=1080):
    path.write_text(
        f"""image_width: {width}
image_height: {height}
camera_matrix:
  rows: 3
  cols: 3
  data: [1001.0, 0.0, 959.5, 0.0, 999.0, 539.5, 0.0, 0.0, 1.0]
distortion_model: plumb_bob
distortion_coefficients:
  rows: 1
  cols: 5
  data: [0.1, -0.02, 0.001, -0.001, 0.0]
""",
        encoding="utf-8",
    )


def test_importer_updates_intrinsics_and_keeps_backup(tmp_path, monkeypatch):
    calibration = tmp_path / "ost.yaml"
    config_path = tmp_path / "config.json"
    write_calibration(calibration)
    config_path.write_text(
        json.dumps({"camera": {"width": 1920, "height": 1080}, "vision": {"fx": 1}}),
        encoding="utf-8",
    )
    importer = load_importer()
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--calibration", str(calibration), "--config", str(config_path)],
    )

    assert importer.main() == 0
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["vision"]["fx"] == 1001.0
    assert config["vision"]["fy"] == 999.0
    assert config["vision"]["distortion_model"] == "plumb_bob"
    assert config["vision"]["distortion_coefficients"] == [0.1, -0.02, 0.001, -0.001, 0.0]
    assert len(list(tmp_path.glob("config.json.before-camera-calibration-*.bak"))) == 1


def test_importer_rejects_resolution_mismatch(tmp_path, monkeypatch):
    calibration = tmp_path / "ost.yaml"
    config_path = tmp_path / "config.json"
    write_calibration(calibration, width=1280, height=720)
    config_path.write_text(
        json.dumps({"camera": {"width": 1920, "height": 1080}, "vision": {}}),
        encoding="utf-8",
    )
    importer = load_importer()
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--calibration", str(calibration), "--config", str(config_path)],
    )

    with pytest.raises(SystemExit, match="拒绝导入"):
        importer.main()


def test_project_config_contains_current_hdr_camera_calibration():
    config = json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))
    importer = load_importer()
    import yaml

    calibration = yaml.safe_load(PROJECT_CALIBRATION.read_text(encoding="utf-8"))
    k = importer.matrix_data(calibration, "camera_matrix")
    d = importer.matrix_data(calibration, "distortion_coefficients")

    assert (config["camera"]["width"], config["camera"]["height"]) == (2592, 1944)
    assert config["vision"]["fx"] == k[0]
    assert config["vision"]["fy"] == k[4]
    assert config["vision"]["cx"] == k[2]
    assert config["vision"]["cy"] == k[5]
    assert config["vision"]["distortion_model"] == calibration["distortion_model"]
    assert config["vision"]["distortion_coefficients"] == d
