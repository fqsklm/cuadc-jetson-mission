from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MissionState(str, Enum):
    PREFLIGHT = "preflight"
    WAITING_FOR_FLIGHT = "waiting_for_flight"
    SURVEY = "survey"
    TARGET_LOCKED = "target_locked"
    TRANSIT = "transit"
    WAITING_FOR_AUTH = "waiting_for_auth"
    RETURNING = "returning"
    COMPLETE = "complete"
    PILOT_OVERRIDE = "pilot_override"
    SAFE_HOLD = "safe_hold"


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    alt_rel_m: float = 0.0


@dataclass(frozen=True)
class Pose:
    monotonic_s: float
    lat: float
    lon: float
    alt_rel_m: float
    roll_rad: float
    pitch_rad: float
    yaw_rad: float
    groundspeed_mps: float = 0.0


@dataclass
class FlightSnapshot:
    connected: bool = False
    armed: bool = False
    mode: str = "UNKNOWN"
    gps_fix_type: int = 0
    satellites: int = 0
    mission_index: int = 0
    mission_count: int = 0
    rc_channels: dict[int, int] = field(default_factory=dict)
    pose: Pose | None = None
    last_heartbeat_s: float = 0.0


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    center_x: float
    center_y: float
    point: GeoPoint | None = None


@dataclass(frozen=True)
class TargetLock:
    label: str
    point: GeoPoint
    observations: int
    mean_confidence: float

