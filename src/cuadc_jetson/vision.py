from __future__ import annotations

from .types import Detection


class YoloDetector:
    def __init__(self, config: dict):
        from ultralytics import YOLO

        self.config = config
        self.target_model = YOLO(config["target_model"], task=config.get("task", "obb"))
        people_path = config.get("people_model")
        self.people_model = YOLO(people_path, task="detect") if people_path else None

    @staticmethod
    def _extract(results) -> list[Detection]:
        detections: list[Detection] = []
        for result in results:
            names = result.names
            boxes = result.obb if getattr(result, "obb", None) is not None else result.boxes
            if boxes is None:
                continue
            centers = boxes.xywhr[:, :2] if hasattr(boxes, "xywhr") else boxes.xywh[:, :2]
            for center, confidence, class_id in zip(centers, boxes.conf, boxes.cls):
                detections.append(Detection(
                    str(names[int(class_id.item())]), float(confidence.item()),
                    float(center[0].item()), float(center[1].item()),
                ))
        return detections

    def detect(self, image) -> list[Detection]:
        kwargs = {
            "conf": float(self.config.get("confidence", 0.5)),
            "imgsz": int(self.config.get("input_size", 1024)),
            "verbose": False,
        }
        detections = self._extract(self.target_model.predict(image, **kwargs))
        if self.people_model is not None:
            people = self._extract(self.people_model.predict(
                image,
                conf=float(self.config.get("people_confidence", 0.4)),
                imgsz=int(self.config.get("people_input_size", 640)),
                classes=[0],
                verbose=False,
            ))
            detections.extend(people)
        return detections

