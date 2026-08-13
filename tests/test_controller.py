from cuadc_jetson.controller import ControllerSettings, MissionController
from cuadc_jetson.fusion import TargetFusion
from cuadc_jetson.types import Detection, FlightSnapshot, GeoPoint, MissionState, Pose


class Actions:
    def __init__(self):
        self.uploads = 0
        self.releases = 0
        self.surveys = 0

    def upload_survey_route(self):
        self.surveys += 1

    def upload_target_route(self, _target):
        self.uploads += 1

    def release_training_payload(self):
        self.releases += 1


def snapshot(now=10.0, mode="AUTO", armed=True, alt=20.0):
    return FlightSnapshot(True, armed, mode, 3, 12, mission_index=8, mission_count=10, rc_channels={8: 1000}, pose=Pose(now, 30.0, 120.0, alt, 0, 0, 0), last_heartbeat_s=now)


def controller(release=True):
    actions = Actions()
    polygon = [GeoPoint(29.9, 119.9), GeoPoint(29.9, 120.1), GeoPoint(30.1, 120.1), GeoPoint(30.1, 119.9)]
    ctl = MissionController(ControllerSettings(5, 2, 8, True, release, polygon, 8, 1800, 5), TargetFusion(8, 2, 0.5), actions)
    return ctl, actions


def test_full_flow_requires_rc_authorization():
    ctl, actions = controller()
    ctl.tick(snapshot(armed=False, alt=0), 9.9, True)
    snap = snapshot()
    assert ctl.tick(snap, 10, True) == MissionState.SURVEY
    assert actions.surveys == 1
    detections = [Detection("42", 0.9, 960, 540, GeoPoint(30.0, 120.0))] * 2
    ctl.observe(detections, 10.1, {"person"})
    assert ctl.tick(snap, 10.1, True) == MissionState.WAITING_FOR_AUTH
    assert actions.uploads == 1
    assert actions.releases == 0
    snap.rc_channels[8] = 1900
    assert ctl.tick(snap, 10.2, True) == MissionState.RETURNING
    assert actions.releases == 1


def test_pilot_override_blocks_release():
    ctl, actions = controller()
    ctl.tick(snapshot(armed=False, alt=0), 9.9, True)
    snap = snapshot()
    ctl.tick(snap, 10, True)
    ctl.observe([Detection("42", 0.9, 1, 1, GeoPoint(30, 120))] * 2, 10.1, {"person"})
    ctl.tick(snap, 10.1, True)
    snap.mode = "RTL"
    snap.rc_channels[8] = 1900
    assert ctl.tick(snap, 10.2, True) == MissionState.PILOT_OVERRIDE
    assert actions.releases == 0


def test_recent_person_blocks_release():
    ctl, actions = controller()
    ctl.tick(snapshot(armed=False, alt=0), 9.9, True)
    snap = snapshot()
    ctl.tick(snap, 10, True)
    ctl.observe([Detection("42", 0.9, 1, 1, GeoPoint(30, 120))] * 2, 10.1, {"person"})
    ctl.tick(snap, 10.1, True)
    ctl.observe([Detection("person", 0.9, 1, 1)], 10.2, {"person"})
    snap.rc_channels[8] = 1900
    assert ctl.tick(snap, 10.3, True) == MissionState.WAITING_FOR_AUTH
    assert actions.releases == 0


def test_survey_is_uploaded_once_while_disarmed():
    ctl, actions = controller()
    snap = snapshot(armed=False, alt=0)
    assert ctl.tick(snap, 10, True) == MissionState.WAITING_FOR_FLIGHT
    assert ctl.tick(snap, 10.1, True) == MissionState.WAITING_FOR_FLIGHT
    assert actions.surveys == 1


def test_inflight_restart_is_locked_out():
    ctl, actions = controller()
    assert ctl.tick(snapshot(), 10, True) == MissionState.PILOT_OVERRIDE
    assert actions.uploads == actions.releases == actions.surveys == 0


def test_camera_failure_in_flight_latches_safe_hold():
    ctl, _actions = controller()
    ctl.tick(snapshot(armed=False, alt=0), 9.9, True)
    assert ctl.tick(snapshot(), 10, True) == MissionState.SURVEY
    assert ctl.tick(snapshot(), 10.1, False) == MissionState.SAFE_HOLD
    assert ctl.tick(snapshot(), 10.2, True) == MissionState.SAFE_HOLD


def test_target_lock_does_not_upload_when_dynamic_route_disabled():
    actions = Actions()
    polygon = [GeoPoint(29.9, 119.9), GeoPoint(29.9, 120.1), GeoPoint(30.1, 120.1)]
    settings = ControllerSettings(5, 2, 8, False, False, polygon, 8, 1800, 5)
    ctl = MissionController(settings, TargetFusion(8, 2, 0.5), actions)
    ctl.tick(snapshot(armed=False, alt=0), 9.9, True)
    snap = snapshot()
    ctl.tick(snap, 10, True)
    ctl.observe([Detection("42", 0.9, 1, 1, GeoPoint(30, 120))] * 2, 10.1, {"person"})
    assert ctl.tick(snap, 10.1, True) == MissionState.TARGET_LOCKED
    assert actions.uploads == 0


def test_selection_waits_until_survey_end():
    ctl, actions = controller()
    ctl.tick(snapshot(armed=False, alt=0), 9.9, True)
    snap = snapshot()
    snap.mission_index = 3
    ctl.tick(snap, 10, True)
    ctl.observe([Detection("42", 0.9, 1, 1, GeoPoint(30, 120))] * 2, 10.1, {"person"})
    assert ctl.tick(snap, 10.1, True) == MissionState.SURVEY
    assert actions.uploads == 0
    snap.mission_index = 8
    assert ctl.tick(snap, 10.2, True) == MissionState.WAITING_FOR_AUTH
    assert actions.uploads == 1


def test_late_authorization_after_leaving_target_never_releases():
    ctl, actions = controller()
    ctl.tick(snapshot(armed=False, alt=0), 9.9, True)
    snap = snapshot()
    ctl.tick(snap, 10, True)
    ctl.observe([Detection("42", 0.9, 1, 1, GeoPoint(30, 120))] * 2, 10.1, {"person"})
    assert ctl.tick(snap, 10.1, True) == MissionState.WAITING_FOR_AUTH
    snap.pose = Pose(10.2, 30.001, 120.0, 20, 0, 0, 0)
    snap.rc_channels[8] = 1900
    assert ctl.tick(snap, 10.2, True) == MissionState.RETURNING
    assert actions.releases == 0
