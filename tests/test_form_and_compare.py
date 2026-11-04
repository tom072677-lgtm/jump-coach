"""점수 없이 측정값과 구간 관찰을 비교하는 계약."""
import json

import compare


def measured(**over):
    m = {
        "absolute_metrics_reliable": True, "events_reliable": True,
        "landing_contact_reliable": True,
        "estimated_distance_foot_m": 2.1, "takeoff_speed_mps": 3.2,
        "takeoff_angle_deg": 28.0, "flight_time_s": 0.5,
        "landing_foot_ahead_of_com_m": 0.35, "landing_foot_ahead_of_hip_m": 0.29,
        "phase_feedback": {"takeoff": {"label": "도약", "observations": ["팔이 전방으로 이동"], "cues": [], "notes": []}},
        "moments": {"load": {"available": True, "frame": 10}},
    }
    m.update(over)
    return m


def test_compare_keeps_observations_and_measurements_without_ranking_posture():
    rep = compare.compare([{"label": "첫 영상", "metrics": measured()}])
    row = rep["rows"][0]
    assert "distance" not in row
    assert row["takeoff_speed"] == 3.2
    assert row["phase_feedback"]["takeoff"]["observations"] == ["팔이 전방으로 이동"]
    assert row["moments"]["load"]["frame"] == 10
    for removed in ("form_score", "breakdown", "strength", "weakness"):
        assert removed not in row
    for removed in ("best_form", "diverge", "common_problems", "power_note"):
        assert removed not in rep


def test_unverified_longest_distance_cannot_win_or_leak_as_a_measurement():
    rep = compare.compare([
        {"label": "검증 실패", "metrics": measured(estimated_distance_foot_m=9.9,
         takeoff_speed_mps=8.8, absolute_metrics_reliable=False, reliability_reason="크기기준 미검증")},
        {"label": "검증됨", "metrics": measured()},
    ])
    assert "best_record" not in rep
    assert "distance" not in rep["rows"][0]
    assert rep["rows"][0]["takeoff_speed"] is None
    assert "크기기준 미검증" in rep["rows"][0]["measurement_note"]


def test_all_unverified_distances_have_no_best_record():
    rep = compare.compare([{"label": "A", "metrics": measured(absolute_metrics_reliable=False)}])
    assert "best_record" not in rep
    assert rep["n_valid"] == 1


def test_first_contact_gap_needs_contact_and_absolute_reliability():
    for over in ({"landing_contact_reliable": False}, {"absolute_metrics_reliable": False},
                 {"landing_contact_reliable": None}):
        row = compare.compare([{"label": "A", "metrics": measured(**over)}])["rows"][0]
        assert row["landing_foot_ahead_of_com_m"] is None
        assert row["landing_foot_ahead_of_hip_m"] is None
    row = compare.compare([{"label": "A", "metrics": measured()}])["rows"][0]
    assert row["landing_foot_ahead_of_com_m"] == 0.35
    assert row["landing_foot_ahead_of_hip_m"] == 0.29


def test_compare_keeps_failed_clip_and_does_not_count_it_as_valid():
    rep = compare.compare([{"label": "A", "metrics": measured()},
                           {"label": "B", "metrics": None, "reason": "추적 실패"}])
    assert rep["n_valid"] == 1
    assert rep["rows"][1] == {"label": "B", "ok": False, "reason": "추적 실패"}
    assert compare.compare([{"label": "B", "metrics": None}])["message"]


def test_compare_report_is_strict_json_and_does_not_mutate_source():
    m = measured(estimated_distance_foot_m=float("nan"), takeoff_speed_mps=float("inf"))
    rep = compare.compare([{"label": "A", "metrics": m}])
    json.dumps(rep, allow_nan=False)
    assert "distance" not in rep["rows"][0]
    assert "form_score" not in m
