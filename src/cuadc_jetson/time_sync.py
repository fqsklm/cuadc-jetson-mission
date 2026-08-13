from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ClockPair:
    monotonic_ns: int
    unix_ns: int
    sampling_uncertainty_ns: int


def sample_clock_pair() -> ClockPair:
    """Sample CLOCK_REALTIME between two CLOCK_MONOTONIC reads.

    The midpoint removes most Python scheduling bias and the half-span is a
    measured upper bound on the sampling uncertainty (excluding NTP error).
    """
    before = time.monotonic_ns()
    unix_ns = time.time_ns()
    after = time.monotonic_ns()
    return ClockPair((before + after) // 2, unix_ns, max(1, (after - before + 1) // 2))


def monotonic_to_unix_ns(monotonic_ns: int, pair: ClockPair) -> int:
    return pair.unix_ns + monotonic_ns - pair.monotonic_ns


@dataclass(frozen=True)
class TimeSyncStatus:
    checked_monotonic_s: float
    source: str
    synchronized: bool
    qualified: bool
    last_offset_s: float | None = None
    rms_offset_s: float | None = None
    root_dispersion_s: float | None = None
    stratum: int | None = None
    leap_status: str = "unknown"
    error: str | None = None

    @property
    def estimated_utc_error_s(self) -> float | None:
        values = [abs(value) for value in (self.last_offset_s, self.root_dispersion_s) if value is not None]
        return sum(values) if values else None


def _tracking_value(output: str, label: str) -> str | None:
    match = re.search(rf"^{re.escape(label)}\s*:\s*(.+?)\s*$", output, re.MULTILINE | re.IGNORECASE)
    return match.group(1) if match else None


def parse_chrony_tracking(
    output: str,
    *,
    max_offset_s: float = 0.010,
    max_root_dispersion_s: float = 0.020,
    max_stratum: int = 4,
    checked_monotonic_s: float = 0.0,
) -> TimeSyncStatus:
    def seconds(label: str) -> float | None:
        value = _tracking_value(output, label)
        if value is None:
            return None
        match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
        return float(match.group(0)) if match else None

    stratum_text = _tracking_value(output, "Stratum")
    stratum = int(stratum_text) if stratum_text and stratum_text.strip().isdigit() else None
    leap = (_tracking_value(output, "Leap status") or "unknown").strip()
    last = seconds("Last offset")
    rms = seconds("RMS offset")
    dispersion = seconds("Root dispersion")
    synchronized = leap.lower() == "normal" and stratum is not None and stratum > 0
    qualified = bool(
        synchronized
        and last is not None
        and abs(last) <= max_offset_s
        and dispersion is not None
        and dispersion <= max_root_dispersion_s
        and stratum <= max_stratum
    )
    return TimeSyncStatus(
        checked_monotonic_s,
        "chrony",
        synchronized,
        qualified,
        last,
        rms,
        dispersion,
        stratum,
        leap,
    )


class TimeSyncMonitor:
    def __init__(self, config: dict | None = None):
        config = config or {}
        self.max_offset_s = float(config.get("max_offset_ms", 10.0)) / 1000.0
        self.max_root_dispersion_s = float(config.get("max_root_dispersion_ms", 20.0)) / 1000.0
        self.max_stratum = int(config.get("max_stratum", 4))
        self.check_interval_s = float(config.get("check_interval_s", 10.0))
        self._status: TimeSyncStatus | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="time-sync-monitor", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.check_interval_s):
            self.status(force=True)

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def cached_status(self) -> TimeSyncStatus:
        if self._status is None:
            return self.status(force=True)
        return self._status

    def status(self, force: bool = False) -> TimeSyncStatus:
        now = time.monotonic()
        if not force and self._status and now - self._status.checked_monotonic_s < self.check_interval_s:
            return self._status
        try:
            result = subprocess.run(["chronyc", "tracking"], capture_output=True, text=True, timeout=2, check=True)
            self._status = parse_chrony_tracking(
                result.stdout,
                max_offset_s=self.max_offset_s,
                max_root_dispersion_s=self.max_root_dispersion_s,
                max_stratum=self.max_stratum,
                checked_monotonic_s=now,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as chrony_error:
            self._status = self._timedatectl_status(now, chrony_error)
        return self._status

    @staticmethod
    def _timedatectl_status(now: float, chrony_error: Exception) -> TimeSyncStatus:
        try:
            result = subprocess.run(
                ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            synchronized = result.stdout.strip().lower() == "yes"
            return TimeSyncStatus(
                now,
                "systemd-timesyncd",
                synchronized,
                False,
                leap_status="normal" if synchronized else "not synchronized",
                error=f"chrony metrics unavailable: {chrony_error}",
            )
        except (FileNotFoundError, subprocess.SubprocessError) as fallback_error:
            return TimeSyncStatus(now, "none", False, False, error=str(fallback_error))


class BootTimeMapper:
    """Map autopilot boot milliseconds to Jetson monotonic time.

    The minimum observed receive-minus-boot offset is used as the least-latency
    estimate. Newer, more delayed packets cannot move historical capture time.
    """

    def __init__(self, window: int = 400):
        self._candidates: deque[float] = deque(maxlen=window)
        self._last_boot_s: float | None = None

    def observe(self, boot_ms: int | float, received_monotonic_s: float) -> tuple[float, float]:
        boot_s = float(boot_ms) / 1000.0
        if self._last_boot_s is not None and boot_s + 1.0 < self._last_boot_s:
            self._candidates.clear()
        self._last_boot_s = boot_s
        candidate = received_monotonic_s - boot_s
        self._candidates.append(candidate)
        offset = min(self._candidates)
        mapped = boot_s + offset
        observed_delay = max(0.0, candidate - offset)
        return mapped, observed_delay
