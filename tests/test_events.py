"""Tests for event detection and the rule-based feedback generators in analyze_jump.py.

detect_events() gets a hand-built CoM/foot trajectory with a known jump window.
coaching()/photo_feedback() are checked for the property that matters most: they
never crash on partial / None / NaN metrics, and they refuse to give event-windowed
advice when the events are flagged unreliable.
"""
import math

import numpy as np
import pytest

import analyze_jump as A


# --------------------------------------------------------------------------- #
# detect_events — a synthetic jump with a known takeoff/apex/landing
# --------------------------------------------------------------------------- #
def test_detect_events_finds_the_airborne_window():
    T, frame_h = 20, 1000
    # Feet on the ground (y=900) except frames 5..10 lifted clear (y=850 -> 50px > 40px thresh).
    norm = np.zeros((T, 33, 3))
    norm[:, A.FOOT_PTS, 1] = 900.0
    norm[5:11, A.FOOT_PTS, 1] = 850.0
    # CoM height (image y, smaller = higher) dips to its minimum at frame 7 (the apex).
    com_y = np.full(T, 600.0)
    com_y[5:11] = [560, 530, 500, 520, 550, 580]
    com = np.column_stack([np.arange(T, dtype=float) * 10.0, com_y])

    takeoff, apex, landing, ground, airborne = A.detect_events(com, norm, frame_h)

    assert takeoff < apex < landing
    assert takeoff == 5
    assert apex == 7
    assert landing == 11           # first ground-contact frame after the airborne run
    assert ground == pytest.approx(900.0)


# --------------------------------------------------------------------------- #
# Feedback generators — metric dicts used below
# --------------------------------------------------------------------------- #
GOOD = {
    "events_reliable": True,
    "countermovement_knee_min_deg": 80.0, "countermovement_reliable": True,
    "takeoff_speed_mps": 3.5, "takeoff_vx_mps": 3.0, "takeoff_vy_mps": 1.8,
    "knee_angle_at_takeoff_deg": 165.0, "hip_angle_at_takeoff_deg": 168.0,
    "ankle_angle_at_takeoff_deg": 120.0, "takeoff_angle_deg": 28.0,
    "arm_swing_range_deg": 110.0, "arm_swing_reliable": True,
    "landing_knee_min_deg": 90.0, "knee_angle_at_landing_deg": 140.0,
    "landing_com_vs_foot_m": 0.2, "landing_metric_reliable": True,
}


def test_coaching_returns_cues_for_a_good_jump():
    cues = A.coaching(GOOD)
    assert isinstance(cues, list) and "다시 분석" in cues[0]
    assert all(isinstance(c, str) for c in cues)


def test_coaching_refuses_advice_when_events_unreliable():
    cues = A.coaching({"events_reliable": False})
    assert len(cues) == 1
    assert "다시 분석" in cues[0]      # honest "can't trust the events" message, no fake cues


@pytest.mark.parametrize("junk", [None, float("nan")])
def test_coaching_never_crashes_on_missing_or_junk(junk):
    m = {k: junk for k in GOOD}
    m["events_reliable"] = True     # events ok, but every measured value is junk
    cues = A.coaching(m)            # must not raise
    assert isinstance(cues, list) and cues


def test_photo_feedback_structure_and_goodbad_lists():
    fb = A.photo_feedback(GOOD)
    assert set(fb) == {"takeoff", "mid", "landing"}
    for phase in fb.values():
        assert set(phase) == {"good", "bad"}
        assert isinstance(phase["good"], list) and isinstance(phase["bad"], list)


@pytest.mark.parametrize("junk", [None, float("nan")])
def test_photo_feedback_never_crashes_on_missing_or_junk(junk):
    m = {k: junk for k in GOOD}
    m["events_reliable"] = True
    fb = A.photo_feedback(m)        # must not raise
    assert set(fb) == {"takeoff", "mid", "landing"}
