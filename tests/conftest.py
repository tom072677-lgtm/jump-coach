"""Shared pytest fixtures + import path for the JumpCoach test suite.

compare.py lives in the repo root and only imports json/math/sys, so we add the
root to sys.path and import it directly. The two real-jump metric dicts below are
FROZEN copies of out/637629f8821b.summary.json (3.02 m) and
out/b6f435a9cacc.summary.json (1.69 m) — embedded here (not read from out/, which
the web app auto-cleans after 24 h) so the regression numbers stay stable.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Real jump A — distance 1.69 m, better knee extension but a too-vertical 41.2° take-off.
# Frozen from out/b6f435a9cacc.summary.json. form_score = 90.8 after the angle band was
# tightened to [20,34] (was 97.3 under the old [20,40] band, which wrongly rewarded 41°).
JUMP_A = {
    "takeoff_angle_deg": 41.2,
    "countermovement_knee_min_deg": 78.5,
    "knee_angle_at_takeoff_deg": 150.7,
    "hip_angle_at_takeoff_deg": 167.1,
    "ankle_angle_at_takeoff_deg": 101.7,
    "arm_swing_range_deg": 104.5,
    "landing_com_vs_foot_m": 0.296,
    "landing_metric_reliable": True,
    "countermovement_reliable": True,
    "arm_swing_reliable": True,
    "events_reliable": True,
    "estimated_distance_foot_m": 1.69,
    "takeoff_speed_mps": 3.22,
    "pose_detection_pct": 98.1,
    "scale_uncertainty_cv": 0.209,
    "flight_gravity_check_mps2": 8.83,
}

# Real jump B — distance 3.02 m, weaker knee extension but a clean 30.1° take-off.
# Frozen from out/637629f8821b.summary.json. form_score = 93.1 (unchanged by the angle
# band tightening, since 30.1° is inside both the old and new band).
JUMP_B = {
    "takeoff_angle_deg": 30.1,
    "countermovement_knee_min_deg": 45.5,
    "knee_angle_at_takeoff_deg": 143.5,
    "hip_angle_at_takeoff_deg": 168.4,
    "ankle_angle_at_takeoff_deg": 126.9,
    "arm_swing_range_deg": 99.3,
    "landing_com_vs_foot_m": 0.004,
    "landing_metric_reliable": True,
    "countermovement_reliable": True,
    "arm_swing_reliable": True,
    "events_reliable": True,
    "estimated_distance_foot_m": 3.02,
    "takeoff_speed_mps": 4.01,
    "pose_detection_pct": 100.0,
    "scale_uncertainty_cv": 0.124,
    "flight_gravity_check_mps2": 9.53,
}

# A clearly poor jump (fabricated, NOT real data) — every submetric outside its
# band — used to prove the scorer discriminates across the quality range.
JUMP_BAD = {
    "takeoff_angle_deg": 60.0,             # too steep (jumps up, loses horizontal)
    "countermovement_knee_min_deg": 140.0,  # barely bends (shallow countermovement)
    "knee_angle_at_takeoff_deg": 120.0,    # poor extension
    "hip_angle_at_takeoff_deg": 130.0,
    "arm_swing_range_deg": 30.0,           # almost no arm swing
    "landing_com_vs_foot_m": -0.30,        # sat back hard
    "landing_metric_reliable": True,
    "countermovement_reliable": True,
    "arm_swing_reliable": True,
    "events_reliable": True,
    "estimated_distance_foot_m": 1.10,
    "takeoff_speed_mps": 2.5,
    "pose_detection_pct": 95.0,
    "scale_uncertainty_cv": 0.15,
    "flight_gravity_check_mps2": 9.7,
}


@pytest.fixture
def jump_a():
    return dict(JUMP_A)


@pytest.fixture
def jump_b():
    return dict(JUMP_B)


@pytest.fixture
def jump_bad():
    return dict(JUMP_BAD)
