#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from cuadc_jetson.time_sync import TimeSyncMonitor


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Jetson UTC synchronization quality")
    parser.add_argument("--max-offset-ms", type=float, default=10.0)
    parser.add_argument("--max-root-dispersion-ms", type=float, default=20.0)
    parser.add_argument("--max-stratum", type=int, default=4)
    parser.add_argument("--require-qualified", action="store_true")
    args = parser.parse_args()
    status = TimeSyncMonitor(
        {
            "max_offset_ms": args.max_offset_ms,
            "max_root_dispersion_ms": args.max_root_dispersion_ms,
            "max_stratum": args.max_stratum,
        }
    ).status(force=True)
    print(json.dumps(status.__dict__ | {"estimated_utc_error_s": status.estimated_utc_error_s}, indent=2))
    if not status.synchronized:
        return 2
    if args.require_qualified and not status.qualified:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
