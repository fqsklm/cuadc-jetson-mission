from pathlib import Path

import pytest

from cuadc_jetson.mission import read_qgc_wpl


def test_read_qgc_wpl(tmp_path: Path):
    path = tmp_path / "route.waypoints"
    path.write_text(
        "QGC WPL 110\n"
        "1\t0\t3\t22\t20\t0\t0\t0\t30.1\t120.2\t30\t1\n",
        encoding="utf-8",
    )
    items = read_qgc_wpl(path)
    assert len(items) == 1
    assert items[0].command == 22
    assert items[0].point.lat == 30.1


def test_reject_wrong_header(tmp_path: Path):
    path = tmp_path / "route.waypoints"
    path.write_text("QGC WPL 120\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_qgc_wpl(path)
