#!/usr/bin/env python3
"""
web_app.py — JumpCoach 로컬 웹 UI

브라우저에서 영상을 올리고 '분석' 버튼을 누르면 analyze_jump.run_analysis 를
호출해 결과(주석 영상·그래프·지표·코칭)를 보여준다. 로컬 전용(127.0.0.1).
"""
import json
import socket
import subprocess
import sys
import threading
import traceback
import uuid
import webbrowser
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# pythonw(콘솔 없는 실행)에서는 sys.stdout/stderr 가 None이라 Flask/Werkzeug 로깅이
# 예외로 죽는다. devnull로 대체해 콘솔 없이도 안전하게 동작하게 한다.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 콘솔 없는 실행(pythonw)에서 부팅 중 미처리 예외를 파일로 남겨 진단 가능하게.
_BOOT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startup.log")
def _boot_excepthook(t, v, tb):
    try:
        with open(_BOOT_LOG, "w", encoding="utf-8") as f:
            f.write("".join(traceback.format_exception(t, v, tb)))
    except Exception:
        pass
sys.excepthook = _boot_excepthook

import cv2
from flask import Flask, request, jsonify, send_from_directory, render_template
from personal_feedback import single_coaching_summary

import analyze_jump  # run_analysis(video, out_prefix, ...) -> (metrics, artifacts)
import compare as compare_mod  # compare(clips) -> report

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
OUT = BASE / "out"
MODEL = str(BASE / "models" / "pose_landmarker_heavy.task")
LOG_FILE = BASE / "analysis_log.jsonl"   # permanent: accumulates real jumps for later calibration


_LOG_LOCK = threading.Lock()   # serialize JSONL appends (Flask threaded=True: /analyze can overlap)


def log_analysis(uid, kind, metrics):
    """Best-effort: append one JSON line per analysis so real-jump data accumulates for
    later calibration (the honest source of accuracy is measured outcomes, not fixed
    literature values). Never breaks the analysis; logs the real exception if it fails."""
    try:
        rec = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "uid": uid,
            "kind": kind,
            "takeoff_speed_mps": metrics.get("takeoff_speed_mps"),
            "takeoff_angle_deg": metrics.get("takeoff_angle_deg"),
            "gravity_check": metrics.get("flight_gravity_check_mps2"),
            "pose_detection_pct": metrics.get("pose_detection_pct"),
            # Keep the RAW values above, but record whether they were trustworthy — the
            # thresholds in analyze_jump.py are heuristics awaiting force-plate calibration,
            # and that recalibration needs to be able to filter this log by trust level.
            "window_detection_pct": metrics.get("window_detection_pct"),
            "tracking_reliable": metrics.get("tracking_reliable"),
            "absolute_metrics_reliable": metrics.get("absolute_metrics_reliable"),
        }
        with _LOG_LOCK, open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        traceback.print_exc()


ALLOWED_EXT = {".mp4", ".mov", ".avi", ".m4v", ".webm"}
MAX_MB = 300

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024


def to_browser_mp4(src: Path, dst: Path) -> bool:
    """OpenCV mp4v는 브라우저 재생 호환이 낮으므로 ffmpeg로 H.264(yuv420p) 변환.
    ffmpeg가 없거나 실패하면 False(원본으로 폴백)."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(dst)],
            check=True, capture_output=True,
        )
        return dst.exists() and dst.stat().st_size > 0
    except Exception:
        return False


@app.route("/")
def index():
    return render_template("index.html", max_mb=MAX_MB)


def moment_shots(metrics, artifacts):
    """검출하지 못한 순간도 이유와 함께 반환하고 사진이 있을 때만 URL을 단다."""
    shots = []
    for key, label, phase in (("load", "하강 말기", "takeoff"),
                              ("drive", "전방 팔스윙 추진", "takeoff"),
                              ("takeoff", "이지 직전", "takeoff"),
                              ("mid", "공중", "mid"), ("landing", "최초 착지", "landing")):
        moment = metrics.get("moments", {}).get(key)
        shot = dict(moment) if moment else {
            "label": label, "phase": phase, "frame": None, "time_s": None,
            "available": False, "reliable": False, "measurements": [],
            "observations": [], "cues": [], "notes": ["이 순간을 확인할 분석 데이터가 없습니다."]}
        shot["key"] = key
        if shot.get("available") and artifacts.get(key):
            shot["url"] = f"/out/{Path(artifacts[key]).name}"
        shots.append(shot)
    return shots


@app.post("/analyze")
def analyze():
    UPLOADS.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    f = request.files.get("video")
    if f is None or f.filename == "":
        return jsonify(ok=False, error="영상 파일을 선택하세요."), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return jsonify(ok=False, error=f"지원하지 않는 형식입니다({ext}). mp4·mov·avi·m4v·webm 만 가능합니다."), 400

    uid = uuid.uuid4().hex[:12]          # 요청마다 고유 → 파일명 충돌/덮어쓰기 방지
    src = UPLOADS / f"{uid}{ext}"
    f.save(str(src))

    # 실제로 열리는 영상인지 검증(손상·미지원 코덱 차단)
    cap = cv2.VideoCapture(str(src))
    ok = cap.isOpened()
    nframes = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if not ok or nframes < 1:
        src.unlink(missing_ok=True)
        return jsonify(ok=False, error="영상을 열 수 없습니다. 파일이 손상되었거나 지원하지 않는 코덱입니다."), 400

    try:
        metrics, artifacts = analyze_jump.analyze_auto(str(src), str(OUT / uid), model=MODEL)
    except SystemExit as e:                # 파이프라인의 의도적 중단(예: 자세 검출 실패)
        return jsonify(ok=False, error=str(e)), 400
    except Exception:
        traceback.print_exc()
        return jsonify(ok=False, error="분석 중 오류가 발생했습니다. 측면에서 전신이 보이는 점프 영상인지 확인해 주세요."), 500

    # 브라우저 재생용 H.264 변환(실패 시 원본 폴백)
    annotated = Path(artifacts["annotated"])
    web_mp4 = OUT / f"{uid}.web.mp4"
    video_name = web_mp4.name if to_browser_mp4(annotated, web_mp4) else annotated.name

    log_analysis(uid, "single", metrics)

    return jsonify(
        ok=True,
        metrics=metrics,
        video_url=f"/out/{video_name}",
        plots_url=f"/out/{Path(artifacts['plots']).name}",
        shots=moment_shots(metrics, artifacts),
        coaching_summary=single_coaching_summary({'metrics': metrics}),
    )


@app.errorhandler(413)
def too_large(_e):
    return jsonify(ok=False, error=f"파일이 너무 큽니다(최대 {MAX_MB}MB)."), 413


@app.post("/compare")
def compare_route():
    UPLOADS.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    files = [f for f in request.files.getlist("videos") if f and f.filename]
    if len(files) < 2:
        return jsonify(ok=False, error="비교하려면 영상을 2개 이상 올리세요."), 400
    files = files[:5]

    # Stage 1 (sequential, fast): save + validate each upload.
    prepared = []  # (idx, label, src|None, uid|None, reason|None)
    for i, f in enumerate(files):
        label = f"{i + 1}. {Path(f.filename).stem}"[:40]
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            prepared.append((i, label, None, None, f"지원하지 않는 형식({ext})")); continue
        uid = uuid.uuid4().hex[:12]
        src = UPLOADS / f"{uid}{ext}"
        f.save(str(src))
        cap = cv2.VideoCapture(str(src)); okv = cap.isOpened()
        nframes = cap.get(cv2.CAP_PROP_FRAME_COUNT); cap.release()
        if not okv or nframes < 1:
            src.unlink(missing_ok=True)
            prepared.append((i, label, None, None, "영상을 열 수 없음")); continue
        prepared.append((i, label, src, uid, None))

    # Stage 2 (parallel): analyze each valid clip in its own thread. MediaPipe
    # releases the GIL during inference, so threads overlap (measured ~2.3x on 2 clips,
    # 8 cores). render_plots uses a standalone Figure so it's thread-safe.
    def run_one(item):
        idx, label, src, uid, reason = item
        if src is None:
            return (idx, {"label": label, "metrics": None, "reason": reason})
        try:
            metrics, artifacts = analyze_jump.analyze_auto(str(src), str(OUT / uid), model=MODEL)
        except SystemExit as exc:
            return (idx, {"label": label, "metrics": None, "reason": str(exc)})
        except Exception:
            traceback.print_exc()
            return (idx, {"label": label, "metrics": None, "reason": "분석 중 오류"})
        annotated = Path(artifacts["annotated"]); web_mp4 = OUT / f"{uid}.web.mp4"
        vname = web_mp4.name if to_browser_mp4(annotated, web_mp4) else annotated.name
        return (idx, {"label": label, "metrics": metrics, "uid": uid,
                      "video_url": f"/out/{vname}", "plots_url": f"/out/{Path(artifacts['plots']).name}",
                      "shots": moment_shots(metrics, artifacts),
                      "coaching_summary": single_coaching_summary({'metrics': metrics})})

    nworkers = min(4, max(1, sum(1 for p in prepared if p[2] is not None)))
    with ThreadPoolExecutor(max_workers=nworkers) as ex:
        out = list(ex.map(run_one, prepared))
    clips = [c for _, c in sorted(out, key=lambda x: x[0])]

    report = compare_mod.compare(clips)
    for row, clip in zip(report["rows"], clips):
        for key in ("video_url", "plots_url", "shots", "coaching_summary"):
            row[key] = clip.get(key)

    # log each analyzed clip (sequential, after the parallel pool — no append race).
    for c in clips:
        if c.get("metrics"):
            log_analysis(c.get("uid"), "compare", c["metrics"])

    return jsonify(ok=True, report=report)


@app.post("/shutdown")
def shutdown():
    """Let the user turn the background analyzer off from the web page."""
    def _stop():
        import time
        time.sleep(0.4)
        os._exit(0)
    threading.Thread(target=_stop, daemon=True).start()
    return jsonify(ok=True)


@app.route("/out/<path:name>")
def out_file(name):
    # send_from_directory: OUT 폴더 밖 경로 접근 차단(경로 탈출 방지)
    return send_from_directory(OUT, name)


from personal_web import register_personal_api

DATA_DIR = Path(os.environ.get('JUMPCOACH_DATA_DIR') or (Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'JumpCoach' / 'data'))
register_personal_api(app, DATA_DIR, MODEL)


def create_app(data_dir, analyzer=None):
    isolated = Flask(__name__)
    isolated.config['MAX_CONTENT_LENGTH'] = MAX_MB * 1024 * 1024
    isolated.add_url_rule('/', 'index', index)
    isolated.register_error_handler(413, too_large)
    register_personal_api(isolated, Path(data_dir), MODEL, analyzer=analyzer)
    return isolated


PORT = int(os.environ.get('JUMPCOACH_PORT', '5000'))
URL = f"http://127.0.0.1:{PORT}/"


def _port_open(port=5000):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    # 이미 서버가 떠 있으면(=아이콘 두 번째 클릭) 브라우저만 열고 종료 → 중복 서버 방지
    if _port_open(PORT):
        webbrowser.open(URL)
        sys.exit(0)
    # 서버 부팅 직후 기본 브라우저로 사용 화면 자동 오픈
    if os.environ.get('JUMPCOACH_NO_BROWSER') != '1':
        threading.Timer(1.5, lambda: webbrowser.open(URL)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
