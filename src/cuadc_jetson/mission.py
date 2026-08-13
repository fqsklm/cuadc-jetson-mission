from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .types import GeoPoint


@dataclass(frozen=True)
class MissionItem:
    frame: int
    command: int
    current: int
    autocontinue: int
    params: tuple[float, float, float, float]
    point: GeoPoint


def read_qgc_wpl(path: str | Path) -> list[MissionItem]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "QGC WPL 110":
        raise ValueError("只支持 QGC WPL 110 航点文件")
    result: list[MissionItem] = []
    for line_number, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 12:
            raise ValueError(f"航点文件第 {line_number} 行字段数不是 12")
        result.append(
            MissionItem(
                frame=int(fields[2]),
                command=int(fields[3]),
                current=int(fields[1]),
                autocontinue=int(fields[11]),
                params=tuple(float(x) for x in fields[4:8]),
                point=GeoPoint(float(fields[8]), float(fields[9]), float(fields[10])),
            )
        )
    return result


def waypoint(point: GeoPoint, command: int = 16, frame: int = 3) -> MissionItem:
    return MissionItem(frame, command, 0, 1, (0.0, 5.0, 0.0, float("nan")), point)

