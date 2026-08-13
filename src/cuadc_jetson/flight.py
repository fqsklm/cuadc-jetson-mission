from __future__ import annotations

import math
import time
from collections import deque
from pathlib import Path

from .mission import MissionItem, read_qgc_wpl, waypoint
from .types import FlightSnapshot, GeoPoint, Pose, TargetLock


class MavlinkFlightLink:
    def __init__(self, config: dict, mission_config: dict, release_config: dict, dry_run: bool):
        self.config = config
        self.mission_config = mission_config
        self.release_config = release_config
        self.dry_run = dry_run
        self.connection = None
        self.snapshot = FlightSnapshot()
        self._attitude = (0.0, 0.0, 0.0)
        self._pose_history: deque[Pose] = deque(maxlen=400)
        self._last_own_heartbeat_s = float("-inf")

    def connect(self) -> None:
        from pymavlink import mavutil

        self.connection = mavutil.mavlink_connection(
            self.config["device"],
            baud=int(self.config.get("baud", 921600)),
            autoreconnect=True,
            source_system=int(self.config.get("source_system", 1)),
            source_component=int(self.config.get("source_component", mavutil.mavlink.MAV_COMP_ID_ONBOARD_COMPUTER)),
        )
        heartbeat = self.connection.wait_heartbeat(timeout=float(self.config.get("connect_timeout_s", 15)))
        if heartbeat is None:
            raise TimeoutError("未收到飞控 HEARTBEAT")
        self._handle(heartbeat)
        self._request_message_intervals()

    def _request_message_intervals(self) -> None:
        from pymavlink import mavutil

        rates = {
            mavutil.mavlink.MAVLINK_MSG_ID_HEARTBEAT: 2,
            mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT: 5,
            mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: 10,
            mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE: 20,
            mavutil.mavlink.MAVLINK_MSG_ID_RC_CHANNELS: 5,
            mavutil.mavlink.MAVLINK_MSG_ID_MISSION_CURRENT: 2,
        }
        for message_id, hz in rates.items():
            self.connection.mav.command_long_send(
                self.connection.target_system,
                self.connection.target_component,
                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                0,
                message_id,
                1_000_000 / hz,
                0, 0, 0, 0, 0,
            )

    def poll(self, timeout_s: float = 0.05) -> FlightSnapshot:
        if self.connection is None:
            raise RuntimeError("飞控尚未连接")
        deadline = time.monotonic() + timeout_s
        now = time.monotonic()
        if now - self._last_own_heartbeat_s >= 1.0:
            from pymavlink import mavutil

            self.connection.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0, 0,
                mavutil.mavlink.MAV_STATE_ACTIVE,
            )
            self._last_own_heartbeat_s = now
        while time.monotonic() < deadline:
            message = self.connection.recv_match(blocking=True, timeout=max(0.0, deadline - time.monotonic()))
            if message is None:
                break
            self._handle(message)
        return self.snapshot

    def _handle(self, message) -> None:
        from pymavlink import mavutil

        now = time.monotonic()
        kind = message.get_type()
        if kind == "HEARTBEAT":
            self.snapshot.connected = True
            self.snapshot.last_heartbeat_s = now
            self.snapshot.armed = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            self.snapshot.mode = mavutil.mode_string_v10(message).upper()
        elif kind == "ATTITUDE":
            self._attitude = (float(message.roll), float(message.pitch), float(message.yaw) % (2 * math.pi))
        elif kind == "GLOBAL_POSITION_INT":
            self.snapshot.pose = Pose(
                now,
                message.lat / 1e7,
                message.lon / 1e7,
                message.relative_alt / 1000.0,
                *self._attitude,
                math.hypot(message.vx, message.vy) / 100.0,
            )
            self._pose_history.append(self.snapshot.pose)
        elif kind == "GPS_RAW_INT":
            self.snapshot.gps_fix_type = int(message.fix_type)
            self.snapshot.satellites = int(message.satellites_visible)
        elif kind == "MISSION_CURRENT":
            self.snapshot.mission_index = int(message.seq)
        elif kind == "MISSION_COUNT":
            self.snapshot.mission_count = int(message.count)
        elif kind == "RC_CHANNELS":
            self.snapshot.rc_channels = {i: int(getattr(message, f"chan{i}_raw")) for i in range(1, 19)}

    def pose_at(self, monotonic_s: float, max_age_s: float = 0.25) -> Pose | None:
        if not self._pose_history:
            return None
        pose = min(self._pose_history, key=lambda item: abs(item.monotonic_s - monotonic_s))
        return pose if abs(pose.monotonic_s - monotonic_s) <= max_age_s else None

    def upload_target_route(self, target: TargetLock) -> None:
        altitude = float(self.mission_config["target_altitude_m"])
        ingress = [GeoPoint(float(p[0]), float(p[1]), float(p[2])) for p in self.mission_config.get("ingress", [])]
        egress = [GeoPoint(float(p[0]), float(p[1]), float(p[2])) for p in self.mission_config.get("egress", [])]
        target_point = GeoPoint(target.point.lat, target.point.lon, altitude)
        items = [waypoint(p) for p in ingress] + [waypoint(target_point)] + [waypoint(p) for p in egress]
        landing_file = self.mission_config.get("landing_mission_file", "")
        if landing_file:
            items.extend(read_qgc_wpl(Path(landing_file)))
        if self.dry_run:
            return
        self._upload(items)

    def upload_survey_route(self) -> None:
        survey_file = self.mission_config.get("survey_mission_file", "")
        if not survey_file:
            if self.dry_run:
                return
            raise ValueError("实飞配置必须提供 mission.survey_mission_file")
        items = read_qgc_wpl(Path(survey_file))
        if not items:
            raise ValueError("侦察航线为空")
        if not self.dry_run:
            self._upload(items)

    def _upload(self, items: list[MissionItem]) -> None:
        from pymavlink import mavutil

        if self.connection is None:
            raise RuntimeError("飞控尚未连接")
        target_system = self.connection.target_system
        target_component = self.connection.target_component
        self.connection.mav.mission_clear_all_send(target_system, target_component)
        clear_ack = self.connection.recv_match(type="MISSION_ACK", blocking=True, timeout=3)
        if clear_ack is None or int(clear_ack.type) != mavutil.mavlink.MAV_MISSION_ACCEPTED:
            raise RuntimeError("飞控未确认清除旧任务，拒绝覆盖航线")
        self.connection.mav.mission_count_send(target_system, target_component, len(items))
        sent: set[int] = set()
        deadline = time.monotonic() + float(self.config.get("mission_upload_timeout_s", 20))
        while time.monotonic() < deadline:
            msg = self.connection.recv_match(type=["MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK"], blocking=True, timeout=1)
            if msg is None:
                continue
            if msg.get_type() == "MISSION_ACK":
                if msg.type != mavutil.mavlink.MAV_MISSION_ACCEPTED or len(sent) != len(items):
                    raise RuntimeError(f"飞控拒绝任务或任务未完整发送: ack={msg.type}, sent={len(sent)}")
                self.connection.mav.mission_set_current_send(target_system, target_component, 0)
                return
            seq = int(msg.seq)
            if not 0 <= seq < len(items):
                raise RuntimeError(f"飞控请求了无效任务序号 {seq}")
            item = items[seq]
            self.connection.mav.mission_item_int_send(
                target_system, target_component, seq, item.frame, item.command,
                item.current, item.autocontinue, *item.params,
                int(round(item.point.lat * 1e7)), int(round(item.point.lon * 1e7)), item.point.alt_rel_m,
            )
            sent.add(seq)
        raise TimeoutError("上传任务超时，未收到 MISSION_ACK")

    def release_training_payload(self) -> None:
        from pymavlink import mavutil

        if self.dry_run or self.connection is None:
            return
        channel = int(self.release_config["servo_channel"])
        open_pwm = int(self.release_config["open_pwm"])
        closed_pwm = int(self.release_config["closed_pwm"])
        try:
            self.connection.mav.command_long_send(
                self.connection.target_system, self.connection.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_SERVO, 0, channel, open_pwm, 0, 0, 0, 0, 0,
            )
            self._wait_command_ack(mavutil.mavlink.MAV_CMD_DO_SET_SERVO)
            time.sleep(float(self.release_config.get("pulse_seconds", 0.5)))
        finally:
            # Spring-return hardware and a physical inhibit are still required;
            # this close attempt only covers ordinary software/ACK failures.
            self.connection.mav.command_long_send(
                self.connection.target_system, self.connection.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_SERVO, 0, channel, closed_pwm, 0, 0, 0, 0, 0,
            )
            self._wait_command_ack(mavutil.mavlink.MAV_CMD_DO_SET_SERVO)

    def _wait_command_ack(self, command: int) -> None:
        from pymavlink import mavutil

        deadline = time.monotonic() + float(self.config.get("command_ack_timeout_s", 2))
        while time.monotonic() < deadline:
            msg = self.connection.recv_match(type="COMMAND_ACK", blocking=True, timeout=0.25)
            if msg is None or int(msg.command) != command:
                continue
            if int(msg.result) not in {
                mavutil.mavlink.MAV_RESULT_ACCEPTED,
                mavutil.mavlink.MAV_RESULT_IN_PROGRESS,
            }:
                raise RuntimeError(f"飞控拒绝命令 {command}: result={msg.result}")
            return
        raise TimeoutError(f"命令 {command} 未收到 COMMAND_ACK")
