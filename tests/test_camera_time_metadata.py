import json

import pytest

from cuadc_jetson.camera import CameraFrame, DecodedPreview, UsbCamera


class FakeCv2:
    IMWRITE_JPEG_QUALITY = 1

    @staticmethod
    def imwrite(path, _image, _options):
        from pathlib import Path

        Path(path).write_bytes(b"jpeg")
        return True


def test_save_uses_capture_utc_and_writes_time_sidecar(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "cv2", FakeCv2)
    camera = UsbCamera({"jpeg_quality": 90})
    frame = CameraFrame(
        7,
        12.5,
        1_700_000_000_123_456_789,
        object(),
        12_480_000_000,
        12_520_000_000,
        1_100_000,
        "v4l2-soe-gstreamer-pts",
    )

    path = camera.save(frame, tmp_path, {"pose": {"lat": 30.0}, "capture_pose_alignment_ns": 0})
    assert path.name == "photo_1700000000123456789_00000007.jpg"
    metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["capture_unix_ns"] == frame.unix_ns
    assert metadata["sequence"] == 7
    assert metadata["camera_timestamp_source"] == "v4l2-soe-gstreamer-pts"
    assert metadata["camera_timestamp_uncertainty_ns"] == 1_100_000
    assert metadata["pose"]["lat"] == 30.0
    assert not list(tmp_path.glob("*.tmp"))


def test_save_preserves_raw_mjpeg_without_reencoding(tmp_path):
    camera = UsbCamera({})
    jpeg = b"\xff\xd8raw-jpeg\xff\xd9"
    frame = CameraFrame(1, 1.0, 2, None, 1, 2, 3, "v4l2-soe-gstreamer-pts", jpeg)
    path = camera.save(frame, tmp_path)
    assert path.read_bytes() == jpeg


def test_camera_rejects_unknown_backend():
    camera = UsbCamera({"backend": "unknown"})
    with pytest.raises(ValueError, match="unsupported camera backend"):
        camera.start()


def test_decoded_image_reports_direct_frame_source():
    image = object()
    camera = UsbCamera({})
    frame = CameraFrame(1, 1.0, 2, image, 1, 2, 3)

    decoded, source = camera.decoded_image_with_source(frame)

    assert decoded is image
    assert source == "frame"
    assert camera.decoded_image(frame) is image


def test_decoded_image_reports_gstreamer_preview_source():
    image = object()
    camera = UsbCamera({"preview_wait_ms": 0})
    camera._previews.append(DecodedPreview(1_000_000_000, image))
    frame = CameraFrame(1, 1.0, 2, None, 1, 2, 3, encoded_jpeg=b"unused")

    decoded, source = camera.decoded_image_with_source(frame)

    assert decoded is image
    assert source == "gstreamer-preview"
