from __future__ import annotations

import json
import logging
import signal
import time
from dataclasses import replace
from pathlib import Path

from .camera import UsbCamera
from .config import AppConfig
from .controller import ControllerSettings, MissionController
from .flight import MavlinkFlightLink
from .fusion import TargetFusion
from .geometry import pixel_to_ground
from .time_sync import TimeSyncMonitor
from .types import Detection, MissionState
from .vision import YoloDetector

LOG = logging.getLogger(__name__)


def run(config: AppConfig) -> int:
    camera_cfg = config.section("camera")
    vision_cfg = config.section("vision")
    flight_cfg = config.section("flight")
    mission_cfg = config.section("mission")
    safety_cfg = config.section("safety")
    release_cfg = config.section("release")
    storage_cfg = config.section("storage")
    time_sync_cfg = config.raw.get("time_sync", {})

    flight = MavlinkFlightLink(flight_cfg, mission_cfg, release_cfg, config.dry_run)
    camera = UsbCamera(camera_cfg)
    time_sync = TimeSyncMonitor(time_sync_cfg)
    detector = YoloDetector(vision_cfg)
    fusion = TargetFusion(
        float(vision_cfg.get("fusion_radius_m", 8)),
        int(vision_cfg.get("min_observations", 6)),
        float(vision_cfg.get("confidence", 0.5)),
        str(vision_cfg.get("selection", "confidence")),
        {str(k): float(v) for k, v in vision_cfg.get("label_priorities", {}).items()},
    )
    controller = MissionController(
        ControllerSettings(
            float(safety_cfg.get("takeoff_alt_m", 5)),
            float(safety_cfg.get("heartbeat_timeout_s", 2)),
            float(safety_cfg.get("target_reach_radius_m", 8)),
            bool(mission_cfg.get("dynamic_upload_enabled", False)),
            config.release_enabled,
            config.release_polygon,
            int(release_cfg.get("auth_rc_channel", 8)),
            int(release_cfg.get("auth_pwm_min", 1800)),
            float(safety_cfg.get("forbidden_cooldown_s", 5)),
            int(mission_cfg.get("survey_end_margin", 1)),
        ),
        fusion,
        flight,
    )
    stop = False

    def request_stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    output_dir = Path(storage_cfg.get("directory", "/var/lib/cuadc-mission/photos"))
    state_file = Path(storage_cfg.get("state_file", "/var/lib/cuadc-mission/state.json"))
    last_frame_sequence = 0
    last_save_s = float("-inf")
    last_detect_s = float("-inf")
    last_state = None
    forbidden_labels = set(safety_cfg.get("forbidden_labels", ["person"]))
    alignment_holdback_s = float(time_sync_cfg.get("pose_alignment_holdback_ms", 80.0)) / 1000.0
    sync_status = time_sync.status(force=True)
    if not sync_status.qualified:
        LOG.warning("UTC time is not precision-qualified: %s", sync_status)
        if bool(time_sync_cfg.get("require_qualified_utc", False)):
            raise RuntimeError("UTC授时未达到配置要求，拒绝启动任务")
    time_sync.start()

    try:
        camera.start()
        flight.connect()
        while not stop:
            now = time.monotonic()
            snapshot = flight.poll(0.05)
            latest_frame = camera.latest()
            frame = camera.next_after(last_frame_sequence, now - alignment_holdback_s)
            pose = flight.pose_at(frame.monotonic_s, float(safety_cfg.get("pose_max_age_s", 0.25))) if frame else None
            if frame and pose is None and now - frame.monotonic_s > float(safety_cfg.get("pose_max_age_s", 0.25)):
                LOG.warning("dropping frame %s: no time-aligned flight pose", frame.sequence)
                last_frame_sequence = frame.sequence
            if frame and frame.sequence != last_frame_sequence and pose:
                last_frame_sequence = frame.sequence
                if controller.state == MissionState.SURVEY and frame.monotonic_s - last_save_s >= float(camera_cfg.get("save_interval_s", 0.5)):
                    sync_status = time_sync.cached_status()
                    camera.save(
                        frame,
                        output_dir,
                        {
                            "time_sync": {
                                "source": sync_status.source,
                                "synchronized": sync_status.synchronized,
                                "qualified": sync_status.qualified,
                                "last_offset_ns": None if sync_status.last_offset_s is None else round(sync_status.last_offset_s * 1e9),
                                "rms_offset_ns": None if sync_status.rms_offset_s is None else round(sync_status.rms_offset_s * 1e9),
                                "root_dispersion_ns": None if sync_status.root_dispersion_s is None else round(sync_status.root_dispersion_s * 1e9),
                                "estimated_utc_error_ns": None if sync_status.estimated_utc_error_s is None else round(sync_status.estimated_utc_error_s * 1e9),
                                "stratum": sync_status.stratum,
                                "leap_status": sync_status.leap_status,
                            },
                            "pose": {
                                "monotonic_ns": round(pose.monotonic_s * 1e9),
                                "source_time_boot_ms": pose.source_time_boot_ms,
                                "timestamp_uncertainty_ns": round(pose.timestamp_uncertainty_s * 1e9),
                                "interpolation_span_ns": round(pose.interpolation_span_s * 1e9),
                                "lat": pose.lat,
                                "lon": pose.lon,
                                "alt_rel_m": pose.alt_rel_m,
                                "roll_rad": pose.roll_rad,
                                "pitch_rad": pose.pitch_rad,
                                "yaw_rad": pose.yaw_rad,
                                "groundspeed_mps": pose.groundspeed_mps,
                            },
                            "capture_pose_alignment_ns": round((pose.monotonic_s - frame.monotonic_s) * 1e9),
                            "autopilot_time": {
                                "unix_usec": snapshot.autopilot_unix_usec,
                                "jetson_minus_autopilot_offset_ns": None if snapshot.autopilot_utc_offset_s is None else round(-snapshot.autopilot_utc_offset_s * 1e9),
                                "sampling_uncertainty_ns": None if snapshot.autopilot_utc_uncertainty_s is None else round(snapshot.autopilot_utc_uncertainty_s * 1e9),
                                "age_ns": None if snapshot.last_system_time_s <= 0 else round((frame.monotonic_s - snapshot.last_system_time_s) * 1e9),
                            },
                        },
                    )
                    last_save_s = frame.monotonic_s
                detect_states = {MissionState.SURVEY, MissionState.TRANSIT, MissionState.WAITING_FOR_AUTH}
                if controller.state in detect_states and frame.monotonic_s - last_detect_s >= 1.0 / float(vision_cfg.get("detect_hz", 10)):
                    detect_image = camera.decoded_image(frame)
                    scale_x = float(camera_cfg["width"]) / detect_image.shape[1]
                    scale_y = float(camera_cfg["height"]) / detect_image.shape[0]
                    raw_detections = [
                        replace(
                            detection,
                            center_x=detection.center_x * scale_x,
                            center_y=detection.center_y * scale_y,
                        )
                        for detection in detector.detect(detect_image)
                    ]
                    detections: list[Detection] = []
                    for detection in raw_detections:
                        point = None
                        if controller.state == MissionState.SURVEY and detection.label not in forbidden_labels:
                            point = pixel_to_ground(
                                pose, detection.center_x, detection.center_y,
                                float(vision_cfg["fx"]), float(vision_cfg["fy"]),
                                float(vision_cfg["cx"]), float(vision_cfg["cy"]),
                                vision_cfg.get("camera_to_body_rpy_deg", [0, -90, 0]),
                                str(vision_cfg.get("distortion_model", "plumb_bob")),
                                vision_cfg.get("distortion_coefficients", []),
                            )
                        detections.append(replace(detection, point=point))
                    controller.observe(detections, frame.monotonic_s, forbidden_labels)
                    last_detect_s = frame.monotonic_s
            frame_fresh = latest_frame is not None and now - latest_frame.monotonic_s <= float(safety_cfg.get("camera_timeout_s", 1.0))
            state = controller.tick(snapshot, now, camera.ready and frame_fresh)
            if state != last_state:
                LOG.info("mission state: %s", state.value)
                state_file.parent.mkdir(parents=True, exist_ok=True)
                state_file.write_text(json.dumps({
                    "state": state.value,
                    "updated_unix": time.time(),
                    "target": controller.target.__dict__ if controller.target else None,
                    "payload_released": controller.payload_released,
                }, default=lambda value: value.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
                last_state = state
            if state == MissionState.COMPLETE:
                return 0
            time.sleep(0.01)
        return 0
    finally:
        camera.close()
        time_sync.close()
