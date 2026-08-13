from cuadc_jetson.time_sync import BootTimeMapper, ClockPair, monotonic_to_unix_ns, parse_chrony_tracking
from cuadc_jetson.time_sync import TimeSyncMonitor, TimeSyncStatus
from cuadc_jetson.flight import MavlinkFlightLink
from cuadc_jetson.types import Pose


class _SystemTimeMessage:
    time_unix_usec = 1_700_000_000_000_000
    time_boot_ms = 12_000

    @staticmethod
    def get_type():
        return "SYSTEM_TIME"


def test_monotonic_to_unix_mapping():
    pair = ClockPair(1_000_000_000, 1_700_000_000_000_000_000, 50)
    assert monotonic_to_unix_ns(1_250_000_000, pair) == 1_700_000_000_250_000_000


def test_chrony_zero_offset_is_valid_and_qualified():
    status = parse_chrony_tracking(
        """Reference ID    : 01020304
Stratum         : 2
Last offset     : +0.000000000 seconds
RMS offset      : 0.000004000 seconds
Root dispersion : 0.000300000 seconds
Leap status     : Normal
"""
    )
    assert status.synchronized
    assert status.qualified
    assert status.estimated_utc_error_s == 0.0003


def test_chrony_large_dispersion_is_not_qualified():
    status = parse_chrony_tracking(
        """Stratum         : 3
Last offset     : -0.001 seconds
RMS offset      : 0.002 seconds
Root dispersion : 0.050 seconds
Leap status     : Normal
"""
    )
    assert status.synchronized
    assert not status.qualified


def test_boot_mapper_uses_least_latency_sample_and_resets_after_reboot():
    mapper = BootTimeMapper()
    mapped1, delay1 = mapper.observe(1_000, 101.030)
    mapped2, delay2 = mapper.observe(2_000, 102.010)
    mapped3, delay3 = mapper.observe(3_000, 103.040)
    assert round(mapped1, 3) == 101.030
    assert round(mapped2, 3) == 102.010
    assert round(mapped3, 3) == 103.010
    assert delay1 == delay2 == 0
    assert round(delay3, 3) == 0.030

    mapped_after_reboot, _ = mapper.observe(100, 200.020)
    assert round(mapped_after_reboot, 3) == 200.020


def test_pose_at_interpolates_position_and_wraps_yaw():
    link = MavlinkFlightLink({}, {}, {}, True)
    link._pose_history.extend(
        [
            Pose(10.0, 30.0, 120.0, 20.0, 0.0, 0.0, 6.265732, 10.0, 1000, 0.002),
            Pose(10.1, 30.0001, 120.0002, 22.0, 0.2, -0.2, 0.017453, 12.0, 1100, 0.003),
        ]
    )
    pose = link.pose_at(10.05, 0.1)
    assert pose is not None
    assert abs(pose.lat - 30.00005) < 1e-9
    assert abs(pose.lon - 120.0001) < 1e-9
    assert abs(pose.alt_rel_m - 21.0) < 1e-9
    assert pose.yaw_rad < 0.01 or pose.yaw_rad > 2 * 3.1415 - 0.01
    assert pose.source_time_boot_ms == 1050
    assert abs(pose.interpolation_span_s - 0.1) < 1e-9


def test_pose_at_rejects_stale_telemetry():
    link = MavlinkFlightLink({}, {}, {}, True)
    link._pose_history.append(Pose(10.0, 30.0, 120.0, 20.0, 0.0, 0.0, 0.0))
    assert link.pose_at(10.3, 0.25) is None


def test_cached_time_status_does_not_refresh(monkeypatch):
    monitor = TimeSyncMonitor()
    expected = TimeSyncStatus(1.0, "test", True, True)
    monitor._status = expected
    monkeypatch.setattr(monitor, "status", lambda force=False: (_ for _ in ()).throw(AssertionError("refreshed")))
    assert monitor.cached_status() is expected


def test_system_time_crosscheck_uses_boot_timestamp_not_receive_timestamp(monkeypatch):
    link = MavlinkFlightLink({}, {}, {}, True)
    link._boot_time_mapper.observe(11_000, 99.000)
    monkeypatch.setattr("cuadc_jetson.flight.time.monotonic", lambda: 100.050)
    monkeypatch.setattr(
        "cuadc_jetson.flight.sample_clock_pair",
        lambda: ClockPair(100_000_000_000, 1_700_000_000_000_000_000, 500),
    )
    link._handle(_SystemTimeMessage())
    assert abs(link.snapshot.autopilot_utc_offset_s or 0) < 1e-9
    assert link.snapshot.last_system_time_s == 100.0
    assert abs((link.snapshot.autopilot_utc_uncertainty_s or 0) - 0.0500005) < 1e-9
