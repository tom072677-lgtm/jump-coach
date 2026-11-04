"""Unit tests for the pure kinematics/physics functions in analyze_jump.py.

These need no video and no pose model — they feed hand-built arrays with a KNOWN
answer (a right angle, a textbook projectile parabola, a flat-line CoM) and check
the function recovers it. The projectile test is the important one: it validates
the core distance/speed/angle engine and the implied-gravity sanity check.
"""
import math

import numpy as np
import pytest

import analyze_jump as A


# --------------------------------------------------------------------------- #
# joint_angle — 3D angle at the middle joint, known geometry
# --------------------------------------------------------------------------- #
def _world_three_points(a_xyz, b_xyz, c_xyz):
    """Build a (1, 33, 3) world array with landmarks 0/1/2 at the given points."""
    w = np.zeros((1, 33, 3))
    w[0, 0], w[0, 1], w[0, 2] = a_xyz, b_xyz, c_xyz
    return w


@pytest.mark.parametrize("a,c,expected", [
    ((1, 0, 0), (0, 1, 0), 90.0),     # perpendicular
    ((1, 0, 0), (-1, 0, 0), 180.0),   # straight line through the joint
    ((1, 0, 0), (1, 1, 0), 45.0),     # 45 degrees
    ((1, 0, 0), (1, 0, 0), 0.0),      # collapsed onto itself
])
def test_joint_angle_known_geometry(a, c, expected):
    # abs=1e-2: the scorer adds a 1e-9 epsilon to the denominator, which shifts cos by
    # ~1e-9 and therefore the angle by ~0.003 deg when cos is near +-1 (the 0 deg / 180 deg cases).
    w = _world_three_points(a, (0, 0, 0), c)
    assert A.joint_angle(w, 0, 1, 2)[0] == pytest.approx(expected, abs=1e-2)


# --------------------------------------------------------------------------- #
# fit_takeoff_velocity — recover a textbook projectile (THE core physics test)
# --------------------------------------------------------------------------- #
def test_fit_takeoff_velocity_recovers_known_projectile():
    fps, scale = 30.0, 100.0            # 100 px per metre
    vx, vy, g = 2.0, 2.5, 9.81          # ground truth (m/s, m/s, m/s^2)
    takeoff, landing, base_y = 5, 15, 1000.0
    com = np.zeros((20, 2))
    for i in range(20):
        t = (i - takeoff) / fps
        com[i, 0] = scale * (vx * t)                       # x(t) = vx*t
        com[i, 1] = base_y - scale * (vy * t - 0.5 * g * t ** 2)  # image y grows downward
    speed, angle, rvx, rvy, rg = A.fit_takeoff_velocity(com, scale, takeoff, landing, fps)
    assert rvx == pytest.approx(vx, abs=1e-6)
    assert rvy == pytest.approx(vy, abs=1e-6)
    assert rg == pytest.approx(g, abs=1e-6)                # implied-gravity sanity check
    assert speed == pytest.approx(math.hypot(vx, vy), abs=1e-6)
    assert angle == pytest.approx(math.degrees(math.atan2(vy, vx)), abs=1e-6)


def test_fit_takeoff_velocity_too_few_frames_returns_nans():
    com = np.zeros((10, 2))
    out = A.fit_takeoff_velocity(com, 100.0, 5, 6, 30.0)   # only 2 frames in window
    assert all(math.isnan(v) for v in out)


# --------------------------------------------------------------------------- #
# center_of_mass — all landmarks at one point => CoM at that point
# --------------------------------------------------------------------------- #
def test_center_of_mass_degenerate_point():
    norm = np.full((1, 33, 3), 0.0)
    norm[..., 0] = 100.0     # all x = 100
    norm[..., 1] = 200.0     # all y = 200
    com = A.center_of_mass(norm)
    assert com[0, 0] == pytest.approx(100.0)
    assert com[0, 1] == pytest.approx(200.0)


# --------------------------------------------------------------------------- #
# med_at — robust median of a window (ignores NaN)
# --------------------------------------------------------------------------- #
def test_med_at_window_median():
    arr = np.array([1, 2, 3, 4, 5, 6, 7], dtype=float)
    assert A.med_at(arr, 3, k=2) == 4.0                    # median of [2,3,4,5,6]


def test_med_at_ignores_nan():
    arr = np.array([1.0, np.nan, 3.0, np.nan, 5.0])
    assert A.med_at(arr, 2, k=2) == 3.0                    # nanmedian of [1,3,5]


# --------------------------------------------------------------------------- #
# _vis_ok — every required landmark must clear the threshold (no masking)
# --------------------------------------------------------------------------- #
def test_vis_ok_all_visible_vs_one_low():
    raw_vis = np.full((10, 33), 0.9)
    assert A._vis_ok(raw_vis, [11, 12, 13], 0, 10, 0.6) is True
    raw_vis[:, 12] = 0.1                                   # one landmark consistently lost
    assert A._vis_ok(raw_vis, [11, 12, 13], 0, 10, 0.6) is False


def test_vis_ok_empty_window_is_false():
    raw_vis = np.full((10, 33), 0.9)
    assert A._vis_ok(raw_vis, [11], 5, 5, 0.6) is False


# --------------------------------------------------------------------------- #
# primary_side — picks the more visible side
# --------------------------------------------------------------------------- #
def test_primary_side_prefers_more_visible():
    norm = np.zeros((4, 33, 3))
    # left joints (hip/knee/ankle/sho/elb) more visible
    for idx in (A.L_HIP, A.L_KNE, A.L_ANK, A.L_SHO, A.L_ELB):
        norm[:, idx, 2] = 0.9
    for idx in (A.R_HIP, A.R_KNE, A.R_ANK, A.R_SHO, A.R_ELB):
        norm[:, idx, 2] = 0.2
    assert A.primary_side(norm) == "L"


# --------------------------------------------------------------------------- #
# interpolate_gaps — linearly fills a NaN gap along time
# --------------------------------------------------------------------------- #
def test_interpolate_gaps_linear_fill():
    arr = np.zeros((5, 1, 1))
    arr[:, 0, 0] = [0.0, np.nan, np.nan, np.nan, 4.0]
    valid = np.array([True, False, False, False, True])
    out = A.interpolate_gaps(arr, valid)
    assert out[:, 0, 0] == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0])


# --------------------------------------------------------------------------- #
# phase_of — frame-to-phase label boundaries
# --------------------------------------------------------------------------- #
def test_phase_of_boundaries():
    assert A.phase_of(3, 5, 10)[0] == "PREP/LOAD"
    assert A.phase_of(5, 5, 10)[0] == "FLIGHT"
    assert A.phase_of(10, 5, 10)[0] == "LANDING"  # first contact belongs to landing
    assert A.phase_of(11, 5, 10)[0] == "LANDING"


# --------------------------------------------------------------------------- #
# butter_lp — degrades gracefully on a too-short clip (returns raw, no crash)
# --------------------------------------------------------------------------- #
def test_butter_lp_short_signal_returns_raw():
    sig = np.array([1.0, 5.0, 2.0])      # far shorter than filter needs
    out = A.butter_lp(sig, fps=30.0)
    assert np.array_equal(out, sig)


def test_butter_lp_smooths_a_long_constant():
    sig = np.full(100, 7.0)
    out = A.butter_lp(sig, fps=30.0)
    assert out == pytest.approx(np.full(100, 7.0), abs=1e-6)
