"""추론만 대체하고 실제 API, 업로드 검사, 비교·로그를 실행한다."""
import copy
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest


@pytest.fixture
def web(monkeypatch, tmp_path):
    # import 시 실행되는 24시간 정리는 테스트 임시 폴더만 순회한다.
    source = Path(__file__).resolve().parents[1] / "web_app.py"
    original_resolve = Path.resolve
    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", lambda p, *a, **kw:
                      tmp_path / "web_app.py" if str(p) == str(source) else original_resolve(p, *a, **kw))
        patch.setattr(sys, "excepthook", sys.excepthook)
        spec = importlib.util.spec_from_file_location("web_app_phase_test", source)
        module = importlib.util.module_from_spec(spec)
        patch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
    assert module.OUT == tmp_path / "out"
    module.app.config["TESTING"] = True
    monkeypatch.setattr(module, "to_browser_mp4", lambda src, dst: False)
    return module


@pytest.fixture
def video_bytes(tmp_path):
    path = tmp_path / "input.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (32, 32))
    assert writer.isOpened()
    for _ in range(3):
        writer.write(np.zeros((32, 32, 3), dtype=np.uint8))
    writer.release()
    return path.read_bytes()


def install_analysis(web, monkeypatch):
    moments = {}
    for i, (key, label, phase) in enumerate([
        ("load", "하강 말기", "takeoff"), ("drive", "전방 팔스윙 추진", "takeoff"),
        ("takeoff", "이지 직전", "takeoff"), ("mid", "공중", "mid"),
        ("landing", "최초 착지", "landing"),
    ]):
        moments[key] = {"label": label, "phase": phase, "frame": i + 10,
                        "time_s": round((i + 10) / 30, 3), "available": key != "drive",
                        "reliable": key != "drive", "measurements": [{"label": "무릎", "value": 120, "unit": "°"}],
                        "observations": ["관찰한 자세"], "cues": ["영상으로 확인"],
                        "notes": ["준비 구간 부족"] if key == "drive" else ["모델 추정"]}
    metrics = {"moments": moments, "phase_feedback": {
        key: {"label": label, "observations": ["구간 관찰"], "cues": [], "notes": []}
        for key, label in [("takeoff", "도약"), ("mid", "공중"), ("landing", "최초 착지")]},
        "absolute_metrics_reliable": True, "landing_contact_reliable": True,
        "estimated_distance_foot_m": 2.0, "takeoff_speed_mps": 3.0}

    def analyze(video, prefix, model):
        artifacts = {key: prefix + "." + key + ".jpg" for key in moments if key != "drive"}
        artifacts.update(annotated=prefix + ".mp4", plots=prefix + ".plots.png")
        return copy.deepcopy(metrics), artifacts

    monkeypatch.setattr(web.analyze_jump, "analyze_auto", analyze)


def test_single_api_provides_five_moments_and_missing_reason_without_scores(web, monkeypatch, video_bytes):
    install_analysis(web, monkeypatch)
    resp = web.app.test_client().post("/analyze", data={"video": (io.BytesIO(video_bytes), "jump.avi")})
    assert resp.status_code == 200
    data = resp.get_json()
    assert [shot["key"] for shot in data["shots"]] == ["load", "drive", "takeoff", "mid", "landing"]
    missing = data["shots"][1]
    assert missing["available"] is False
    assert not missing.get("url")
    assert missing["notes"] == ["준비 구간 부족"]
    assert data["shots"][4]["frame"] == 14
    assert data["shots"][4]["time_s"] == 0.467
    assert "form_score" not in data["metrics"]
    record = json.loads(web.LOG_FILE.read_text(encoding="utf-8").splitlines()[0])
    assert "form_score" not in record


def test_compare_exposes_every_clips_photos_and_phase_feedback(web, monkeypatch, video_bytes):
    install_analysis(web, monkeypatch)
    resp = web.app.test_client().post("/compare", data={"videos": [
        (io.BytesIO(video_bytes), '<img src=x onerror=alert(1)>.avi'),
        (io.BytesIO(video_bytes), "second.avi")]})
    assert resp.status_code == 200
    rows = resp.get_json()["report"]["rows"]
    assert len(rows) == 2
    for row in rows:
        assert len(row["shots"]) == 5
        assert row["shots"][4]["label"] == "최초 착지"
        assert row["phase_feedback"]["takeoff"]["observations"] == ["구간 관찰"]
        assert "form_score" not in row
        assert row["plots_url"].startswith("/out/")
    records = [json.loads(line) for line in web.LOG_FILE.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert all("form_score" not in rec for rec in records)


def test_api_rejects_missing_extension_and_damaged_files(web):
    client = web.app.test_client()
    for data in ({}, {"video": (io.BytesIO(b"x"), "bad.txt")},
                 {"video": (io.BytesIO(b"not a video"), "bad.avi")}):
        assert client.post("/analyze", data=data).status_code == 400
    assert client.post("/compare", data={"videos": (io.BytesIO(b"x"), "one.avi")}).status_code == 400


def test_compare_limits_work_to_five_clips(web, monkeypatch, video_bytes):
    install_analysis(web, monkeypatch)
    resp = web.app.test_client().post("/compare", data={"videos": [
        (io.BytesIO(video_bytes), f"jump{i}.avi") for i in range(6)]})
    assert resp.status_code == 200
    assert len(resp.get_json()["report"]["rows"]) == 5


def render_ui(function, data):
    """브라우저의 주변 DOM만 대체하고 실제 화면 생성 함수를 Node에서 실행한다."""
    source = (Path(__file__).resolve().parents[1] / "templates" / "index.html").read_text(encoding="utf-8")
    script = source.split("<script>", 1)[1].split("</script>", 1)[0]
    dom = """const elements = new Map();
global.document = {getElementById(id){
  if (!elements.has(id)) elements.set(id, {innerHTML:'', classList:{add(){},remove(){}},
    addEventListener(){}, scrollIntoView(){}, textContent:''});
  return elements.get(id);
}};
global.window = {};
"""
    run = subprocess.run(["node", "-"], input=dom + script + "\n" + function + "(" + json.dumps(data) + ");\nconsole.log(result.innerHTML);",
                         text=True, encoding="utf-8", capture_output=True, check=True)
    return run.stdout


def test_moment_ui_groups_all_five_escapes_text_and_explains_missing_photo():
    shots = [{"key": key, "label": label, "phase": phase, "available": key != "drive",
              "reliable": True, "frame": 10, "time_s": 0.333,
              "url": "/out/test.jpg" if key != "drive" else None,
              "measurements": [], "comment": "<img src=x onerror=alert(1)>" if key != 'drive' else '',
              "observations": [],
              "cues": [], "notes": ["준비 구간 부족"] if key == "drive" else []}
             for key, label, phase in [("load", "하강 말기", "takeoff"),
               ("drive", "전방 팔스윙 추진", "takeoff"), ("takeoff", "이지 직전", "takeoff"),
               ("mid", "공중", "mid"), ("landing", "최초 착지", "landing")]]
    html = render_ui("showResult", {"metrics": {"absolute_metrics_reliable": False,
                     "reliability_reason": "크기기준 미검증"}, "shots": shots})
    assert "도약 · 세 순간" in html
    assert all(label in html for label in ("하강 말기", "전방 팔스윙 추진", "이지 직전", "최초 착지"))
    assert "다시 촬영하세요" in html and "0.333" in html and "프레임 10" in html
    assert "준비 구간 부족" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert '<img src=x onerror=alert(1)>' not in html
    assert 'src="null"' not in html and 'src="undefined"' not in html


def test_ui_gates_first_contact_length_and_escapes_compare_filename():
    shot = {"key": "landing", "phase": "landing", "available": True,
            "measurements": [{"label": "발–무게중심", "value": 9.87, "unit": "cm"},
                             {"label": "발–골반", "value": 8.76, "unit": "cm"}]}
    verified = render_ui("showResult", {"metrics": {"landing_foot_ahead_of_com_m": 9.87,
        "landing_foot_ahead_of_hip_m": 8.76, "landing_contact_reliable": True,
        "absolute_metrics_reliable": True}, "shots": [shot]})
    assert "9.87" in verified and "8.76" in verified
    for contact, absolute in [(False, True), (True, False)]:
        html = render_ui("showResult", {"metrics": {"landing_foot_ahead_of_com_m": 9.87,
            "landing_foot_ahead_of_hip_m": 8.76, "landing_contact_reliable": contact,
            "absolute_metrics_reliable": absolute}, "shots": [shot]})
        assert "9.87" not in html and "8.76" not in html
    html = render_ui("showCompare", {"rows": [{"ok": True, "label": "<svg onload=alert(1)>",
        "distance": 2.1, "takeoff_speed": 3.2, "phase_feedback": {}, "shots": []}], "warnings": []})
    assert "&lt;svg onload=alert(1)&gt;" in html
    assert "<svg onload=alert(1)>" not in html
    assert "자세점수" not in html and "최고 자세" not in html and "속도(파워)" not in html


def test_phase_details_do_not_repeat_the_same_feedback_above_and_below_the_photo():
    line = '발과 골반의 간격 관찰'
    shot = {'key': 'landing', 'phase': 'landing', 'label': '착지', 'available': True,
            'reliable': True, 'url': '/out/test.jpg', 'comment': line,
            'observations': [line], 'cues': [], 'notes': []}
    html = render_ui('showResult', {'metrics': {'phase_feedback': {
        'landing': {'observations': [line], 'cues': [], 'notes': []}}}, 'shots': [shot]})
    assert html.count(line) == 1


def test_three_core_metrics_are_always_visible_before_phase_photos():
    for metrics in ({}, {'takeoff_speed_mps':3.3,'takeoff_angle_deg':25.2,'flight_time_s':.5,
                        'metric_display': {k:{'displayable':True,'estimated':True} for k in
                          ('takeoff_speed_mps','takeoff_angle_deg','flight_time_s')}}):
        html = render_ui('showResult', {'metrics':metrics,'shots':[]})
        top = html.split('도약 · 세 순간')[0]
        for label in ('도약 속도','도약 각도','체공 시간'):
            assert label in top
        assert '<details' not in top
        assert '계산 불가' in top if not metrics else '3.30' in top


def test_ui_removes_distance_and_repetitive_caveats_in_single_and_compare():
    note = '관절각은 추정값입니다. 몸통–허벅지 포함각은 해부학적 고관절 굽힘각과 다릅니다.'
    shot = {'key': 'landing', 'phase': 'landing', 'available': True, 'reliable': False,
            'label': '착지', 'comment': '발을 앞으로 보내세요.', 'notes': [note]}
    single = render_ui('showResult', {'metrics': {'estimated_distance_foot_m': 9.876,
        'absolute_metrics_reliable': True, 'reliability_reason': '표시하지 않을 검증 안내',
        'detection_warnings': ['표시하지 않을 경고']}, 'shots': [shot]})
    comparison = render_ui('showCompare', {'best_record': '최고거리', 'warnings': [note],
        'rows': [{'ok': True, 'label': '점프', 'distance': 9.876, 'measurement_note': note,
                  'shots': [shot]}]})
    for html in [single, comparison]:
        assert '발을 앞으로 보내세요.' in html and '착지 후보' in html
        assert all(text not in html for text in ['추정 거리', '9.876', '최고거리',
            '자동 추정 신뢰도', '해석 시 주의', note, '표시하지 않을'])
