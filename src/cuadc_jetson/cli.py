from __future__ import annotations

import argparse
import logging
import sys

from .app import run
from .config import ConfigError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="CUADC Jetson-only mission supervisor")
    parser.add_argument("--config", required=True, help="JSON 配置文件")
    parser.add_argument("--validate", action="store_true", help="只校验配置，不连接硬件")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(args.config)
        if args.validate:
            print("配置校验通过")
            return 0
        return run(config)
    except (ConfigError, KeyError, ValueError) as exc:
        logging.error("启动被拒绝: %s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

