"""Tests for the reliability gates added in PLAN.md — compute_reliability() and the
generation-time gating it drives in coaching() / photo_feedback().

Why these gates exist: the flight parabola gives ONE equation with TWO unknowns
(g_implied = a_px·k²/scale), so a wrong g proves that k or scale is broken but cannot
say which. We never auto-correct; we only refuse to quote scale-dependent numbers.

compute_reliability() is a pure function, so every gate below is tested without a video.
"""
import math
import re

import pytest

import analyze_jump as A


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def rel(**over):
    """compute_reliability() with an all-good baseline, overridden per test."""
    kw = dict(
        det_pct=100.0,
        window_det_pct=100.0,
        scale_cv=0.10,
        scale_is_manual=False,
        g_implied=9.81,
        slowmo_detected=False,
        slowmo_fit_r2=None,
        events_reliable=True,
    )
    kw.update(over)
    return A.compute_reliability(**kw)


# A complete, plausible metric dict (numbers from the real slj_00002 run) that passes
# every gate. Absolute values are deliberately distinctive so tests can assert on the
# exact rendered substring, not just on a regex.
def metrics(**over):
    m = {
        "tracking_reliable": True,
        "events_reliable": True,
        "absolute_metrics_reliable": True,
        "reliability_reason": "중력 검증 통과",
        "pose_detection_pct": 100.0,
        "countermovement_knee_min_deg": 110.0, "countermovement_reliable": True,
        "takeoff_speed_mps": 7.77, "takeoff_vx_mps": 6.66, "takeoff_vy_mps": 5.55,
        "knee_angle_at_takeoff_deg": 144.0, "hip_angle_at_takeoff_deg": 167.0,
        "ankle_angle_at_takeoff_deg": 115.0,
        "takeoff_angle_deg": 34.8,
        "arm_swing_range_deg": 97.0, "arm_swing_reliable": True,
        "landing_knee_min_deg": 86.0, "knee_angle_at_landing_deg": 145.0,
        "landing_com_vs_foot_m": 0.44, "landing_metric_reliable": True,
        "apex_com_height_m": 0.33, "estimated_distance_foot_m": 2.22,
    }
    m.update(over)
    return m


# Absolute numbers as they get rendered ("7.77 m/s", "0.44m"). If the gate leaks, one of
# these strings shows up verbatim — a sharper check than a generic regex.
ABSOLUTE_SUBSTRINGS = ("7.77", "6.66", "5.55", "0.44", "0.33", "2.22")

SPEED_RE = re.compile(r"\d+\.\d+\s*m/s(?!²)")      # "3.41 m/s" but NOT the "9.81 m/s²" in the notice
LENGTH_RE = re.compile(r"\d+\.\d+\s*m(?![/²\w])")  # "0.10m"
ANGLE_RE = re.compile(r"\d+\s*°")


# --------------------------------------------------------------------------- #
# tracking_reliable
# --------------------------------------------------------------------------- #
def test_low_whole_clip_detection_fails_tracking():
    assert rel(det_pct=68.5)["tracking_reliable"] is False


def test_high_scale_cv_fails_tracking():
    assert rel(scale_cv=0.62)["tracking_reliable"] is False


def test_nan_scale_cv_is_not_a_free_pass_on_auto_scale():
    """auto_scale_px_per_m() returns NaN when it FAILS. NaN must never be read as
    'manual scale, so skip the check' — that was the bug Codex caught in plan v1."""
    for bad in (float("nan"), None, float("inf")):
        assert rel(scale_cv=bad, scale_is_manual=False)["tracking_reliable"] is False


def test_manual_scale_exempts_the_cv_check():
    assert rel(scale_cv=float("nan"), scale_is_manual=True)["tracking_reliable"] is True


def test_broken_flight_window_fails_even_when_whole_clip_looks_fine():
    """95% overall can still hide a shredded take-off→landing window."""
    assert rel(det_pct=95.0, window_det_pct=70.0)["tracking_reliable"] is False


def test_nan_window_detection_fails_closed():
    """Reversed / out-of-range events produce NaN upstream; that must not pass."""
    assert rel(window_det_pct=float("nan"))["tracking_reliable"] is False


def test_non_numeric_inputs_do_not_raise():
    for bad in (None, "95", float("nan"), float("inf"), True):
        out = rel(det_pct=bad)
        assert out["tracking_reliable"] is False


# --------------------------------------------------------------------------- #
# absolute_metrics_reliable
# --------------------------------------------------------------------------- #
def test_gravity_far_from_981_blocks_absolute_metrics():
    """slj_00002: g=11.5 → the scale is ~17% off, so distances must not be quoted."""
    out = rel(g_implied=11.5)
    assert out["absolute_metrics_reliable"] is False
    assert "11.50" in out["reliability_reason"]


def test_gravity_near_981_allows_absolute_metrics():
    assert rel(g_implied=9.81)["absolute_metrics_reliable"] is True


def test_manual_scale_does_not_override_a_failed_gravity_check():
    """A user-asserted scale is still checked: if g is off, something else (fps, events)
    is broken, so the numbers stay hidden."""
    assert rel(g_implied=11.5, scale_is_manual=True)["absolute_metrics_reliable"] is False


def test_slowmo_with_auto_scale_is_unidentifiable():
    """k was DERIVED from g, so g ≡ 9.81 by construction. Trusting it would be circular:
    two unknowns (k, scale), one equation."""
    out = rel(slowmo_detected=True, g_implied=9.81, slowmo_fit_r2=0.99, scale_is_manual=False)
    assert out["absolute_metrics_reliable"] is False
    assert "슬로우모션" in out["reliability_reason"]


def test_slowmo_with_manual_scale_is_identifiable():
    out = rel(slowmo_detected=True, g_implied=9.81, slowmo_fit_r2=0.99, scale_is_manual=True)
    assert out["absolute_metrics_reliable"] is True
    assert "사용자" in out["reliability_reason"]     # the caveat is surfaced, not hidden


def test_slowmo_with_manual_scale_still_needs_a_clean_parabola():
    """Even with an asserted scale, a sloppy fit means k itself is guesswork."""
    for r2 in (0.90, None, float("nan")):
        out = rel(slowmo_detected=True, g_implied=9.81, slowmo_fit_r2=r2, scale_is_manual=True)
        assert out["absolute_metrics_reliable"] is False


def test_non_finite_gravity_blocks_absolute_metrics():
    for bad in (None, float("nan"), float("inf")):
        assert rel(g_implied=bad)["absolute_metrics_reliable"] is False


def test_non_finite_gravity_blocks_the_slowmo_path_too():
    """Regression (found in Codex code review): the slow-mo branch only checked r2, so a
    NaN gravity with a manual scale slipped through — k = sqrt(9.81/g) is meaningless then."""
    for bad in (None, float("nan"), float("inf"), 0.0, -1.0):
        out = rel(slowmo_detected=True, g_implied=bad, slowmo_fit_r2=0.99, scale_is_manual=True)
        assert out["absolute_metrics_reliable"] is False


def test_unreliable_events_block_absolute_metrics():
    assert rel(events_reliable=False)["absolute_metrics_reliable"] is False


def test_unreliable_tracking_blocks_absolute_metrics():
    out = rel(det_pct=50.0)
    assert out["absolute_metrics_reliable"] is False
    assert "추적" in out["reliability_reason"]


# --------------------------------------------------------------------------- #
# boundary values — pin the inclusive/exclusive semantics
# --------------------------------------------------------------------------- #
def test_thresholds_are_inclusive_at_the_boundary():
    out = rel(det_pct=A.POSE_DET_MIN, window_det_pct=A.WINDOW_DET_MIN,
              scale_cv=A.SCALE_CV_MAX, g_implied=A.G_STD + A.G_TOL)
    assert out["tracking_reliable"] is True
    assert out["absolute_metrics_reliable"] is True


def test_just_past_the_boundary_fails():
    assert rel(det_pct=A.POSE_DET_MIN - 0.1)["tracking_reliable"] is False
    assert rel(scale_cv=A.SCALE_CV_MAX + 0.01)["tracking_reliable"] is False
    assert rel(g_implied=A.G_STD + A.G_TOL + 0.01)["absolute_metrics_reliable"] is False
    assert rel(g_implied=A.G_STD - A.G_TOL - 0.01)["absolute_metrics_reliable"] is False


# --------------------------------------------------------------------------- #
# scale_from_gravity — diagnostic only
# --------------------------------------------------------------------------- #
def test_scale_from_gravity_matches_the_formula():
    # slj_00002: limb scale 163.5 px/m with g=11.5 implies ~191.7 px/m.
    assert A.scale_from_gravity(163.5, 11.5) == pytest.approx(163.5 * 11.5 / 9.81)
    assert A.scale_from_gravity(163.5, 11.5) == pytest.approx(191.7, abs=0.1)


def test_scale_from_gravity_returns_none_on_bad_input():
    for scale, g in [(0, 9.81), (-5, 9.81), (None, 9.81), (float("inf"), 9.81),
                     (163.5, 0), (163.5, None), (163.5, float("nan"))]:
        assert A.scale_from_gravity(scale, g) is None


# Phase feedback supersedes the old flat threshold-based coaching rules.
def test_tracking_failure_returns_a_single_reshoot_line():
    cues = A.coaching(metrics(tracking_reliable=False))
    assert len(cues) == 1
    assert "몸을 안정적으로 찾지 못했어요" in cues[0]
    assert not ANGLE_RE.search(cues[0])


def test_old_summary_requires_reanalysis_instead_of_inventing_phase_observations():
    cues = A.coaching(metrics(absolute_metrics_reliable=True))
    assert len(cues) == 1 and "다시 분석" in cues[0]
    assert not any(value in cues[0] for value in ABSOLUTE_SUBSTRINGS)
    assert all(not p["good"] and not p["bad"] for p in A.photo_feedback(metrics()).values())


def test_flat_coaching_is_derived_from_phase_observations_and_cues():
    m = metrics()
    m["phase_feedback"] = {
        "takeoff": {"label": "도약", "observations": ["몸통 25°"], "cues": ["팔 타이밍 비교"], "notes": []},
        "landing": {"label": "착지", "observations": ["최초 접촉 후보"], "cues": [], "notes": ["반대발 가림"]}}
    cues = A.coaching(m)
    assert cues == ["도약: 몸통 25°", "도약: 팔 타이밍 비교", "착지: 최초 접촉 후보", "착지: 반대발 가림"]
    fb = A.photo_feedback(m)
    assert fb["takeoff"] == {"good": ["몸통 25°"], "bad": ["팔 타이밍 비교"]}
    assert fb["mid"] == {"good": [], "bad": []}


def test_tracking_gate_runs_before_precomputed_phase_feedback():
    m = metrics(tracking_reliable=False)
    m["phase_feedback"] = {"takeoff": {"label": "도약", "observations": ["잘못된 평가"], "cues": [], "notes": []}}
    assert "잘못된 평가" not in "\n".join(A.coaching(m))
