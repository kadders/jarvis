"""Motion ingest and API view tests."""

from models.motion import motion_sample_from_payload, motion_view_payload


def test_motion_sample_from_payload():
    row = motion_sample_from_payload(
        {
            "session_id": "s1",
            "t_s": 2.5,
            "x": 1.0,
            "y": 2.0,
            "z": -1.0,
            "a": 30.0,
            "source": "tap_rrf_poll",
            "feed_mm_min": 900,
        }
    )
    assert row is not None
    assert row["t_s"] == 2.5
    assert row["a"] == 30.0


def test_motion_view_payload_downsample():
    samples = [{"t_s": float(i), "x": i, "y": 0.0, "z": 0.0, "source": "tap_rrf_poll"} for i in range(100)]
    view = motion_view_payload("s1", samples, max_points=10)
    assert view["n_samples"] == 100
    assert view["n_points"] <= 10
    assert view["bbox"]["x_max"] >= 90.0
