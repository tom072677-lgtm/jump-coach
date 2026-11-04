"""여러 영상의 측정값과 구간 관찰 비교. 자세 채점·순위는 생성하지 않는다."""
import json
import math
import sys


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def compare(clips):
    """clips: {'label', 'metrics': summary dict 또는 None, 'reason'?: str}."""
    rows = []
    for clip in clips:
        m = clip.get("metrics")
        if not m:
            rows.append({"label": clip["label"], "ok": False,
                         "reason": clip.get("reason", "분석 실패")})
            continue
        absolute = m.get("absolute_metrics_reliable") is True
        contact = absolute and m.get("landing_contact_reliable") is True

        def value(key, allowed=True):
            v = m.get(key)
            return v if allowed and _finite(v) else None

        rows.append({
            "label": clip["label"], "ok": True,
            "takeoff_speed": value("takeoff_speed_mps", absolute),
            "takeoff_angle_deg": value("takeoff_angle_deg", m.get("events_reliable") is not False),
            "flight_time_s": value("flight_time_s", absolute or not m.get("slowmo_detected")),
            "landing_foot_ahead_of_com_m": value("landing_foot_ahead_of_com_m", contact),
            "landing_foot_ahead_of_hip_m": value("landing_foot_ahead_of_hip_m", contact),
            "absolute_metrics_reliable": absolute,
            "landing_contact_reliable": m.get("landing_contact_reliable") is True,
            "measurement_note": "" if absolute else m.get("reliability_reason", "크기기준 미검증: 거리·속도 비교 보류"),
            "phase_feedback": m.get("phase_feedback", {}),
            "moments": m.get("moments", {}),
        })

    valid = [r for r in rows if r["ok"]]
    report = {"rows": rows, "n_valid": len(valid),
              "warnings": []}
    if not valid:
        report["message"] = "비교할 수 있는 유효한 분석이 없습니다."
    if any(not r["absolute_metrics_reliable"] for r in valid):
        report["warnings"].append("크기기준을 검증하지 못한 영상은 거리·속도 비교에서 제외했습니다.")
    return report


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    clips = []
    for path in sys.argv[1:]:
        try:
            with open(path, encoding="utf-8") as source:
                clips.append({"label": path.replace("\\", "/").split("/")[-1].replace(".summary.json", ""),
                              "metrics": json.load(source)})
        except Exception as exc:
            clips.append({"label": path, "metrics": None, "reason": str(exc)})
    print(json.dumps(compare(clips), ensure_ascii=False, indent=2))
