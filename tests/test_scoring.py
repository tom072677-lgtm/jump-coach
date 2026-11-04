"""폐지한 자세 채점 대신 비교에 공개되는 수치의 신뢰도 경계를 검증한다."""
import pytest

import compare


@pytest.mark.parametrize("value", [None, "3.2", True, float("nan"), float("inf")])
def test_non_numeric_distance_does_not_become_best_record(value):
    rep = compare.compare([{"label": "A", "metrics": {
        "estimated_distance_foot_m": value, "absolute_metrics_reliable": True}}])
    assert "distance" not in rep["rows"][0]
    assert "best_record" not in rep


def test_missing_reliability_does_not_authorize_old_unverified_distance():
    rep = compare.compare([{"label": "이전 분석", "metrics": {
        "estimated_distance_foot_m": 3.5, "takeoff_speed_mps": 4.0}}])
    assert "distance" not in rep["rows"][0]
    assert rep["rows"][0]["takeoff_speed"] is None


def test_unverified_slow_motion_hides_corrected_flight_time():
    rep = compare.compare([{"label": "느린 영상", "metrics": {
        "absolute_metrics_reliable": False, "slowmo_detected": True, "flight_time_s": 0.6}}])
    assert rep["rows"][0]["flight_time_s"] is None


def test_real_time_flight_time_survives_scale_failure():
    rep = compare.compare([{"label": "실시간 영상", "metrics": {
        "absolute_metrics_reliable": False, "slowmo_detected": False, "flight_time_s": 0.6}}])
    assert rep["rows"][0]["flight_time_s"] == 0.6
