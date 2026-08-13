#!/usr/bin/env python3
"""Import a ROS camera_calibration YAML file into CUADC mission config."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
import shutil
import tempfile
from pathlib import Path


def matrix_data(document: dict, key: str) -> list[float]:
    value = document.get(key)
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        raise ValueError(f"ROS 标定文件缺少 {key}.data")
    result = [float(item) for item in value["data"]]
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{key}.data 包含非有限数值")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="把 ROS ost.yaml 导入 CUADC 配置")
    parser.add_argument("--calibration", required=True, type=Path, help="ROS ost.yaml 路径")
    parser.add_argument("--config", required=True, type=Path, help="要更新的 CUADC config.json")
    parser.add_argument("--force-resolution", action="store_true", help="允许标定与任务分辨率不一致（不推荐）")
    args = parser.parse_args()

    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("缺少 PyYAML；在 Ubuntu/Jetson 上安装：sudo apt install python3-yaml") from exc

    calibration_path = args.calibration.expanduser().resolve()
    config_path = args.config.expanduser().resolve()
    document = yaml.safe_load(calibration_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(config, dict):
        raise SystemExit("标定 YAML 和任务 JSON 的根节点都必须是对象")

    width = int(document.get("image_width", 0))
    height = int(document.get("image_height", 0))
    camera = config.get("camera")
    vision = config.get("vision")
    if width <= 0 or height <= 0 or not isinstance(camera, dict) or not isinstance(vision, dict):
        raise SystemExit("标定分辨率无效，或任务配置缺少 camera/vision")
    configured_size = (int(camera.get("width", 0)), int(camera.get("height", 0)))
    calibrated_size = (width, height)
    if configured_size != calibrated_size and not args.force_resolution:
        raise SystemExit(
            f"拒绝导入：标定分辨率 {width}x{height} 与任务分辨率 "
            f"{configured_size[0]}x{configured_size[1]} 不同"
        )

    k = matrix_data(document, "camera_matrix")
    d = matrix_data(document, "distortion_coefficients")
    if len(k) != 9 or k[0] <= 0 or k[4] <= 0:
        raise SystemExit("camera_matrix 必须是有效的 3x3 内参矩阵")
    distortion_model = str(document.get("distortion_model", "plumb_bob"))
    if distortion_model not in {"plumb_bob", "rational_polynomial", "equidistant"}:
        raise SystemExit(f"暂不支持 ROS 畸变模型：{distortion_model}")

    vision.update(
        {
            "fx": k[0],
            "fy": k[4],
            "cx": k[2],
            "cy": k[5],
            "distortion_model": distortion_model,
            "distortion_coefficients": d,
        }
    )

    original_stat = config_path.stat()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = config_path.with_suffix(config_path.suffix + f".before-camera-calibration-{timestamp}.bak")
    shutil.copy2(config_path, backup_path)
    encoded = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=config_path.parent, delete=False) as temporary:
        temporary.write(encoded)
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, original_stat.st_mode)
    if hasattr(os, "chown"):
        os.chown(temporary_path, original_stat.st_uid, original_stat.st_gid)
    temporary_path.replace(config_path)

    print(f"已导入 {width}x{height} 标定：fx={k[0]:.6f}, fy={k[4]:.6f}, cx={k[2]:.6f}, cy={k[5]:.6f}")
    print(f"畸变模型：{distortion_model}，系数：{d}")
    print(f"原配置备份：{backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
