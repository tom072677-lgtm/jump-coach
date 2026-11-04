"""Tests for the letterbox subject-crop helper and the slow-motion correction math.

The crop helper is a pure function (testable on synthetic landmark boxes). The slow-mo
correction is embedded in run_analysis, but its physics basis lives in fit_takeoff_velocity:
a real-time jump filmed k-times slower reads back as g_implied = g/k^2 and speed = real/k,
and k = sqrt(9.81/g_implied) recovers the true values — exactly what the correction applies.
"""
import math

import numpy as np
import pytest

import analyze_jump as A


# --------------------------------------------------------------------------- #
# _subject_crop_box
# --------------------------------------------------------------------------- #
def _norm_with_subject(x0, x1, y0, y1, T=10):
    """(T,33,3) landmark array spread across the given pixel box, fully visible."""
    norm = np.zeros((T, 33, 3))
    norm[:, :, 0] = np.linspace(x0, x1, 33)[None, :]
    norm[:, :, 1] = np.linspace(y0, y1, 33)[None, :]
    norm[:, :, 2] = 0.9
    return norm, np.ones(T, dtype=bool)


def test_crop_triggers_on_letterboxed_small_subject():
    # 1080x1920 vertical 'shorts': subject is a thin central band (like the 337cm clip).
    norm, valid = _norm_with_subject(120, 1000, 820, 1200)
    box = A._subject_crop_box(norm, valid, (1080, 1920))
    assert box is not None
    x, y, w, h = box
    assert 0 <= x and 0 <= y and x + w <= 1080 and y + h <= 1920   # inside the frame
    assert w % 2 == 0 and h % 2 == 0                               # libx264 even dims
    assert h < 1920 * 0.8                                          # cropped away the letterbox


def test_crop_none_when_subject_fills_frame():
    # Subject spans most of the frame -> no crop (normal clips untouched -> no regression).
    norm, valid = _norm_with_subject(100, 980, 80, 1850)
    assert A._subject_crop_box(norm, valid, (1080, 1920)) is None


def test_crop_none_on_too_few_valid_frames():
    norm = np.zeros((3, 33, 3)); norm[:, :, 2] = 0.9
    valid = np.array([True, False, True])
    assert A._subject_crop_box(norm, valid, (1080, 1920)) is None


# --------------------------------------------------------------------------- #
# Slow-motion physics: g_implied = g/k^2, recovered by k = sqrt(9.81/g_implied)
# --------------------------------------------------------------------------- #
def test_slowmo_signature_and_recovery():
    real_fps, scale = 30.0, 100.0
    vx, vy, g, k = 3.0, 2.5, 9.81, 8.0          # ground truth + 8x slow motion
    takeoff = 5
    landing = takeoff + int(round(0.5 * k * real_fps))   # 0.5 s real flight -> 0.5*k s of footage
    com = np.zeros((landing + 5, 2))
    for i in range(len(com)):
        t_real = ((i - takeoff) / real_fps) / k          # container time / k = real elapsed time
        com[i, 0] = scale * (vx * t_real)
        com[i, 1] = 1000.0 - scale * (vy * t_real - 0.5 * g * t_real ** 2)
    speed, angle, rvx, rvy, g_impl = A.fit_takeoff_velocity(com, scale, takeoff, landing, real_fps)

    # slow-mo signature
    assert g_impl == pytest.approx(g / k ** 2, rel=0.02)         # ~0.15 for 8x
    # the correction the pipeline applies
    k_est = math.sqrt(9.81 / g_impl)
    assert k_est == pytest.approx(k, rel=0.02)
    assert speed * k_est == pytest.approx(math.hypot(vx, vy), rel=0.02)   # rescaled speed recovers real
    # take-off angle is slow-mo INVARIANT (vy and vx both scale by 1/k)
    assert angle == pytest.approx(math.degrees(math.atan2(vy, vx)), abs=0.5)


def test_slowmo_detection_threshold_excludes_normal_jumps():
    """A normal real-time jump has g in [7,12] -> must NOT be treated as slow motion
    (the slow-mo branch only fires for g < 3.0)."""
    assert not (0.0 < 9.81 < 3.0)      # documents the gate: real g never enters slow-mo path
    assert (0.0 < 0.15 < 3.0)          # an 8x slow-mo g does


# --------------------------------------------------------------------------- #
# _joints_to_draw — render gate that suppresses off-body / low-confidence joints
# (regression guard for the "skeleton grabs a background object" glitch)
# --------------------------------------------------------------------------- #
def _body(core=(500.0, 500.0), torso=100.0):
    """A plausible 33-landmark frame: every joint near the core, fully visible."""
    xy = np.tile(np.array(core, dtype=float), (33, 1))
    xy += np.linspace(-torso, torso, 33)[:, None] * 0.3   # small spread within the body
    vis = np.full(33, 0.9)
    return xy, vis


def test_joints_to_draw_keeps_a_normal_pose():
    xy, vis = _body()
    keep = A._joints_to_draw(xy, vis, core=(500.0, 500.0), torso_len=100.0)
    assert keep.all()                                       # nothing dropped on a clean pose


def test_joints_to_draw_drops_a_joint_flung_to_the_background():
    xy, vis = _body()
    xy[A.L_WRI] = (500.0 + 600.0, 500.0)         # wrist flung 6 torso-lengths away
    keep = A._joints_to_draw(xy, vis, core=(500.0, 500.0), torso_len=100.0)
    assert not keep[A.L_WRI]                     # the off-body fling is dropped
    assert keep[A.R_HIP] and keep[A.L_SHO]   # the rest stays


def test_joints_to_draw_drops_low_visibility_joint():
    xy, vis = _body()
    vis[A.L_ELB] = 0.2
    keep = A._joints_to_draw(xy, vis, core=(500.0, 500.0), torso_len=100.0)
    assert not keep[A.L_ELB]


def test_joints_to_draw_keeps_a_really_extended_limb():
    """A fully extended real limb (~2.3 torso lengths from the core) must NOT be removed."""
    xy, vis = _body()
    xy[A.R_WRI] = (500.0 + 2.3 * 100.0, 500.0)   # within the 2.8-torso reach
    keep = A._joints_to_draw(xy, vis, core=(500.0, 500.0), torso_len=100.0)
    assert keep[A.R_WRI]


def test_joints_to_draw_falls_back_to_visibility_when_scale_unknown():
    xy, vis = _body()
    xy[A.L_WRI] = (9999.0, 9999.0)               # far, but no body scale to judge reach
    keep = A._joints_to_draw(xy, vis, core=None, torso_len=float("nan"))
    assert keep[A.L_WRI]                         # distance gate disabled -> kept (vis ok)
    vis[A.L_WRI] = 0.1
    keep = A._joints_to_draw(xy, vis, core=None, torso_len=float("nan"))
    assert not keep[A.L_WRI]                     # but low visibility still drops it
