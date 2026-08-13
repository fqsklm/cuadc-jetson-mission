from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import distance_m
from .types import Detection, GeoPoint, TargetLock


@dataclass
class _Cluster:
    label: str
    points: list[GeoPoint] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)

    @property
    def center(self) -> GeoPoint:
        return GeoPoint(
            sum(p.lat for p in self.points) / len(self.points),
            sum(p.lon for p in self.points) / len(self.points),
        )


class TargetFusion:
    def __init__(
        self,
        radius_m: float,
        min_observations: int,
        min_confidence: float,
        selection: str = "confidence",
        label_priorities: dict[str, float] | None = None,
    ):
        self.radius_m = radius_m
        self.min_observations = min_observations
        self.min_confidence = min_confidence
        self.selection = selection
        self.label_priorities = label_priorities or {}
        self._clusters: list[_Cluster] = []

    def add(self, detection: Detection) -> None:
        if detection.point is None or detection.confidence < self.min_confidence:
            return
        for cluster in self._clusters:
            if cluster.label == detection.label and distance_m(cluster.center, detection.point) <= self.radius_m:
                cluster.points.append(detection.point)
                cluster.confidences.append(detection.confidence)
                return
        self._clusters.append(_Cluster(detection.label, [detection.point], [detection.confidence]))

    def best(self) -> TargetLock | None:
        valid = [c for c in self._clusters if len(c.points) >= self.min_observations]
        if not valid:
            return None
        def score(cluster: _Cluster) -> tuple[float, float, int]:
            mean_confidence = sum(cluster.confidences) / len(cluster.confidences)
            if self.selection == "max_numeric":
                try:
                    value = float(cluster.label)
                except ValueError:
                    value = float("-inf")
            elif self.selection == "priority":
                value = float(self.label_priorities.get(cluster.label, float("-inf")))
            else:
                value = mean_confidence
            return value, mean_confidence, len(cluster.points)

        selected = max(valid, key=score)
        return TargetLock(
            selected.label,
            selected.center,
            len(selected.points),
            sum(selected.confidences) / len(selected.confidences),
        )
