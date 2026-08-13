from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .fusion import TargetFusion
from .geometry import distance_m, point_in_polygon
from .types import Detection, FlightSnapshot, MissionState, TargetLock


class MissionActions(Protocol):
    def upload_survey_route(self) -> None: ...
    def upload_target_route(self, target: TargetLock) -> None: ...
    def release_training_payload(self) -> None: ...


@dataclass
class ControllerSettings:
    takeoff_alt_m: float
    heartbeat_timeout_s: float
    target_reach_radius_m: float
    dynamic_upload_enabled: bool
    release_enabled: bool
    release_polygon: list
    auth_rc_channel: int
    auth_pwm_min: int
    forbidden_cooldown_s: float
    survey_end_margin: int = 1


class MissionController:
    """Fail-closed mission state machine.

    It never arms and never changes flight mode. MANUAL/RTL/LOITER or stale
    telemetry immediately suppress mission writes and payload output.
    """

    def __init__(self, settings: ControllerSettings, fusion: TargetFusion, actions: MissionActions):
        self.settings = settings
        self.fusion = fusion
        self.actions = actions
        self.state = MissionState.PREFLIGHT
        self.target: TargetLock | None = None
        self.last_forbidden_seen_s = float("-inf")
        self.route_uploaded = False
        self.survey_uploaded = False
        self.payload_released = False
        self.seen_disarmed = False

    def observe(self, detections: list[Detection], now_s: float, forbidden_labels: set[str]) -> None:
        for detection in detections:
            if detection.label in forbidden_labels:
                self.last_forbidden_seen_s = now_s
            else:
                self.fusion.add(detection)

    def tick(self, snapshot: FlightSnapshot, now_s: float, camera_ready: bool) -> MissionState:
        if not snapshot.connected or now_s - snapshot.last_heartbeat_s > self.settings.heartbeat_timeout_s:
            self.state = MissionState.SAFE_HOLD
            return self.state
        if not snapshot.armed:
            self.seen_disarmed = True
        elif not self.seen_disarmed:
            # A service that starts/restarts in flight must never take over an
            # unknown pre-existing mission.
            self.state = MissionState.PILOT_OVERRIDE
            return self.state
        if self.state == MissionState.SAFE_HOLD and snapshot.armed:
            return self.state
        if not camera_ready or snapshot.gps_fix_type < 3:
            self.state = MissionState.SAFE_HOLD if snapshot.armed else MissionState.PREFLIGHT
            return self.state
        if self.state in {MissionState.PREFLIGHT, MissionState.SAFE_HOLD}:
            self.state = MissionState.WAITING_FOR_FLIGHT

        if self.state == MissionState.WAITING_FOR_FLIGHT and not snapshot.armed and not self.survey_uploaded:
            self.actions.upload_survey_route()
            self.survey_uploaded = True

        airborne = snapshot.armed and snapshot.pose is not None and snapshot.pose.alt_rel_m >= self.settings.takeoff_alt_m
        if self.state == MissionState.WAITING_FOR_FLIGHT and airborne and snapshot.mode == "AUTO":
            self.state = MissionState.SURVEY

        active_states = {
            MissionState.SURVEY,
            MissionState.TARGET_LOCKED,
            MissionState.TRANSIT,
            MissionState.WAITING_FOR_AUTH,
            MissionState.RETURNING,
        }
        if self.state in active_states and snapshot.mode != "AUTO":
            self.state = MissionState.PILOT_OVERRIDE
            return self.state

        if self.state == MissionState.SURVEY:
            survey_complete = (
                snapshot.mission_count > 0
                and snapshot.mission_index >= max(0, snapshot.mission_count - 1 - self.settings.survey_end_margin)
            )
            if survey_complete:
                lock = self.fusion.best()
                if lock is not None:
                    self.target = lock
                    self.state = MissionState.TARGET_LOCKED

        if self.state == MissionState.TARGET_LOCKED and self.target is not None:
            if not self.settings.dynamic_upload_enabled:
                return self.state
            if not point_in_polygon(self.target.point, self.settings.release_polygon):
                self.state = MissionState.SAFE_HOLD
                return self.state
            self.actions.upload_target_route(self.target)
            self.route_uploaded = True
            self.state = MissionState.TRANSIT

        if self.state == MissionState.TRANSIT and self.target and snapshot.pose:
            aircraft = self.target.point.__class__(snapshot.pose.lat, snapshot.pose.lon, snapshot.pose.alt_rel_m)
            if distance_m(aircraft, self.target.point) <= self.settings.target_reach_radius_m:
                self.state = MissionState.WAITING_FOR_AUTH

        if self.state == MissionState.WAITING_FOR_AUTH:
            if self.target is None or snapshot.pose is None:
                self.state = MissionState.RETURNING
                return self.state
            aircraft = self.target.point.__class__(snapshot.pose.lat, snapshot.pose.lon, snapshot.pose.alt_rel_m)
            if distance_m(aircraft, self.target.point) > self.settings.target_reach_radius_m:
                # The single release window has passed. Late authorization must
                # never cause an action during egress or landing.
                self.state = MissionState.RETURNING
                return self.state
            authorized = snapshot.rc_channels.get(self.settings.auth_rc_channel, 0) >= self.settings.auth_pwm_min
            scene_clear = now_s - self.last_forbidden_seen_s >= self.settings.forbidden_cooldown_s
            telemetry_fresh = now_s - snapshot.last_heartbeat_s <= self.settings.heartbeat_timeout_s
            if self.settings.release_enabled and authorized and scene_clear and telemetry_fresh:
                self.actions.release_training_payload()
                self.payload_released = True
                self.state = MissionState.RETURNING

        if self.state in {MissionState.RETURNING, MissionState.PILOT_OVERRIDE, MissionState.WAITING_FOR_AUTH}:
            if not snapshot.armed and snapshot.pose and snapshot.pose.alt_rel_m < 1.0:
                self.state = MissionState.COMPLETE
        return self.state
