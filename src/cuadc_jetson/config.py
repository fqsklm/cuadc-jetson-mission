from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import GeoPoint
from .mission import read_qgc_wpl


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]
    path: Path

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name)
        if not isinstance(value, dict):
            raise ConfigError(f"配置段 {name!r} 缺失或不是对象")
        return value

    @property
    def dry_run(self) -> bool:
        return bool(self.raw.get("dry_run", True))

    @property
    def release_enabled(self) -> bool:
        return bool(self.section("release").get("enabled", False))

    @property
    def release_polygon(self) -> list[GeoPoint]:
        return [GeoPoint(float(p[0]), float(p[1])) for p in self.section("safety").get("release_polygon", [])]


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置 JSON 无效: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("配置根节点必须是对象")
    config = AppConfig(raw=raw, path=config_path)
    validate(config)
    return config


def _require(section: dict[str, Any], key: str, expected: type) -> Any:
    value = section.get(key)
    if not isinstance(value, expected):
        raise ConfigError(f"配置项 {key!r} 必须是 {expected.__name__}")
    return value


def validate(config: AppConfig) -> None:
    for name in ("camera", "vision", "flight", "mission", "safety", "release", "storage"):
        config.section(name)

    camera = config.section("camera")
    _require(camera, "device", str)
    if int(camera.get("width", 0)) <= 0 or int(camera.get("height", 0)) <= 0:
        raise ConfigError("camera.width/height 必须为正数")
    if camera.get("backend", "gstreamer") not in {"gstreamer", "opencv"}:
        raise ConfigError("camera.backend 必须为 gstreamer 或 opencv")

    time_sync = config.raw.get("time_sync", {})
    if not isinstance(time_sync, dict):
        raise ConfigError("time_sync 必须是对象")
    for key in ("max_offset_ms", "max_root_dispersion_ms", "check_interval_s", "pose_alignment_holdback_ms"):
        if key in time_sync and (not isinstance(time_sync[key], (int, float)) or float(time_sync[key]) < 0):
            raise ConfigError(f"time_sync.{key} 必须是非负数")
    if int(time_sync.get("max_stratum", 4)) < 1:
        raise ConfigError("time_sync.max_stratum 必须为正整数")

    vision = config.section("vision")
    target_model = _require(vision, "target_model", str)
    if Path(target_model).suffix.lower() != ".pt":
        raise ConfigError("vision.target_model 必须是 .pt 文件；当前项目已停用 engine/ONNX")
    people_model = vision.get("people_model", "")
    if not isinstance(people_model, str):
        raise ConfigError("vision.people_model 必须是字符串")
    if people_model and Path(people_model).suffix.lower() != ".pt":
        raise ConfigError("vision.people_model 必须是 .pt 文件；当前项目已停用 engine/ONNX")
    for key in ("fx", "fy", "cx", "cy"):
        value = vision.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ConfigError(f"vision.{key} 必须是有限数值")
    if float(vision["fx"]) <= 0 or float(vision["fy"]) <= 0:
        raise ConfigError("vision.fx/fy 必须为正数")
    distortion_model = vision.get("distortion_model", "plumb_bob")
    if distortion_model not in {"plumb_bob", "rational_polynomial", "equidistant"}:
        raise ConfigError("vision.distortion_model 必须是 plumb_bob、rational_polynomial 或 equidistant")
    coefficients = vision.get("distortion_coefficients", [])
    if not isinstance(coefficients, list) or not all(
        isinstance(value, (int, float)) and math.isfinite(float(value)) for value in coefficients
    ):
        raise ConfigError("vision.distortion_coefficients 必须是有限数值数组")
    if vision.get("selection", "confidence") not in {"confidence", "max_numeric", "priority"}:
        raise ConfigError("vision.selection 必须为 confidence、max_numeric 或 priority")
    if vision.get("selection") == "priority" and not vision.get("label_priorities"):
        raise ConfigError("priority 选择策略必须配置 label_priorities")

    flight = config.section("flight")
    _require(flight, "device", str)
    if int(flight.get("baud", 0)) <= 0:
        raise ConfigError("flight.baud 必须为正数")
    if flight.get("firmware") != "ardupilot":
        raise ConfigError("当前版本只支持经过设计的 ardupilot 路径；PX4 适配尚未实现")

    safety = config.section("safety")
    polygon = safety.get("release_polygon", [])
    if polygon and (not isinstance(polygon, list) or len(polygon) < 3):
        raise ConfigError("safety.release_polygon 必须为空或至少包含 3 个点")

    release = config.section("release")
    mission = config.section("mission")
    dynamic_upload = bool(mission.get("dynamic_upload_enabled", False))
    if dynamic_upload:
        if config.dry_run:
            raise ConfigError("mission.dynamic_upload_enabled=true 时 dry_run 必须为 false")
        if len(config.release_polygon) < 3:
            raise ConfigError("启用动态航线前必须配置 release_polygon")
        for key, label in (("survey_mission_file", "侦察航线"), ("landing_mission_file", "降落航线")):
            path_value = mission.get(key)
            if not isinstance(path_value, str) or not path_value:
                raise ConfigError(f"启用动态航线前必须配置{label}")
            path = Path(path_value).expanduser()
            if not path.is_absolute():
                path = (config.path.parent / path).resolve()
            if not path.is_file():
                raise ConfigError(f"{label}文件不存在: {path}")
            mission[key] = str(path)
        try:
            survey_items = read_qgc_wpl(mission["survey_mission_file"])
            landing_items = read_qgc_wpl(mission["landing_mission_file"])
        except (OSError, ValueError) as exc:
            raise ConfigError(f"航线文件无效: {exc}") from exc
        if not survey_items or survey_items[0].command != 22:
            raise ConfigError("侦察航线第一项必须是 MAV_CMD_NAV_TAKEOFF (22)")
        if not landing_items or landing_items[-1].command != 21:
            raise ConfigError("降落航线最后一项必须是 MAV_CMD_NAV_LAND (21)")
        for key in ("ingress", "egress"):
            points = mission.get(key)
            if not isinstance(points, list) or not points:
                raise ConfigError(f"启用动态航线前 mission.{key} 至少需要一个安全航点")
            for point in points:
                if not isinstance(point, list) or len(point) != 3:
                    raise ConfigError(f"mission.{key} 中每个航点必须为 [lat, lon, alt_rel_m]")
    if config.release_enabled:
        if config.dry_run:
            raise ConfigError("release.enabled=true 时 dry_run 必须为 false")
        if len(config.release_polygon) < 3:
            raise ConfigError("启用释放前必须配置 release_polygon")
        if not release.get("require_rc_authorization", True):
            raise ConfigError("释放功能必须保留 RC 人工授权")
        if int(release.get("servo_channel", 0)) < 1:
            raise ConfigError("release.servo_channel 必须为有效输出通道")
        if not config.section("vision").get("people_model"):
            raise ConfigError("启用释放前必须配置独立的人员检测模型")
        if not dynamic_upload:
            raise ConfigError("启用释放前必须先启用动态航线上传")
