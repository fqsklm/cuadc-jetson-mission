from cuadc_jetson.fusion import TargetFusion
from cuadc_jetson.types import Detection, GeoPoint


def detection(label, confidence):
    return Detection(label, confidence, 0, 0, GeoPoint(30.0, 120.0))


def test_numeric_selection_uses_value_then_confidence():
    fusion = TargetFusion(8, 2, 0.5, "max_numeric")
    for item in [detection("12", 0.99), detection("12", 0.98), detection("83", 0.7), detection("83", 0.71)]:
        fusion.add(item)
    assert fusion.best().label == "83"


def test_priority_selection():
    fusion = TargetFusion(8, 1, 0.5, "priority", {"triangle": 10, "circle": 20})
    fusion.add(detection("triangle", 0.99))
    fusion.add(detection("circle", 0.7))
    assert fusion.best().label == "circle"
