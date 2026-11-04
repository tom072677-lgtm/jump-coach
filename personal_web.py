"""가벼운 이름 선택과 영구 기록 API."""
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

import cv2
from flask import Blueprint, jsonify, request, send_file

from analysis_service import AnalysisService, video_info
from data_transfer import create_backup
from history import build_history
from personal_feedback import build_personal_feedback, single_coaching_summary
from storage import Conflict


def register_personal_api(app, data_dir, model, analyzer=None):
    bp = Blueprint('personal', __name__)
    service_lock = threading.Lock()
    instance = []

    def service():
        with service_lock:
            if not instance:
                instance.append(AnalysisService(data_dir, model, analyzer=analyzer))
            return instance[0]

    app.extensions['jumpcoach_service'] = service

    @bp.errorhandler(Conflict)
    def conflict(error):
        return jsonify(ok=False, error=str(error)), 409

    @bp.errorhandler(ValueError)
    def invalid(error):
        return jsonify(ok=False, error=str(error)), 400

    @bp.errorhandler(KeyError)
    def missing(error):
        return jsonify(ok=False, error=str(error).strip("'")), 404

    def body():
        result = request.get_json(silent=True)
        if not isinstance(result, dict):
            raise ValueError('입력 내용을 확인하세요.')
        return result

    def present(item):
        # File paths are served only through manifest-backed endpoints.
        item = dict(item)
        if item.get('source_path'):
            item['source_url'] = f"/api/attempts/{item['id']}/source"
        run = item.get('selected_run') or {}
        data = run.get('result') or {}
        if data:
            data = {**data, 'coaching_summary': [] if item.get('analysis_stale') else single_coaching_summary(data)}
            if data.get('artifacts', {}).get('plots'):
                data['plots_url'] = f"/api/runs/{run['id']}/files/plots"
            item['selected_run'] = {**run, 'result': data}
        return item

    @bp.route('/api/athletes', methods=['GET','POST'])
    def people():
        store = service().store
        if request.method == 'POST':
            return jsonify(ok=True, athlete=store.save_athlete(body().get('name')))
        return jsonify(ok=True, athletes=store.list_athletes())

    @bp.post('/api/attempts')
    def create_attempt():
        data = body()
        result = service().store.create_attempt(data['athlete_id'], data['performed_on'], data['request_key'],
            measured_distance_cm=data.get('measured_distance_cm'), validity=data.get('validity','valid'),
            commit=data.get('commit', True), measurement_setup=data.get('measurement_setup','학원 측정'),
            capture_setup=data.get('capture_setup','측면 고정'), record_unknown=data.get('record_unknown',False))
        return jsonify(ok=True, attempt=present(result)), 201

    @bp.route('/api/attempts/<aid>', methods=['GET','PATCH','DELETE'])
    def attempt(aid):
        store = service().store
        if request.method == 'GET':
            item = present(store.get_attempt(aid))
            item['personal_feedback'] = build_personal_feedback(item, store.list_attempts(item['athlete_id']))
            return jsonify(ok=True, attempt=item)
        data = body()
        if request.method == 'DELETE':
            data['deleted'] = True
        return jsonify(ok=True, attempt=present(store.update_attempt(aid, data.get('expected_revision'), data)))

    @bp.post('/api/attempts/<aid>/restore')
    def restore(aid):
        return jsonify(ok=True, attempt=present(service().store.update_attempt(aid,body().get('expected_revision'),{'deleted':False})))

    @bp.post('/api/attempts/<aid>/correction')
    def correction(aid):
        store = service().store
        with staged_video(store) as (path, name, info):
            item = store.correct_attempt(aid,int(request.form.get('expected_revision',-1)),
                                         request.form.get('request_key'),path,name)
        return jsonify(ok=True, attempt=present(item))

    @contextmanager
    def staged_video(store):
        upload = request.files.get('video')
        if upload is None or not upload.filename:
            raise ValueError('영상을 선택하세요.')
        ext = Path(upload.filename).suffix.lower()
        if ext not in ('.mp4','.mov','.avi','.m4v','.webm'):
            raise ValueError('mp4·mov·avi·m4v·webm 영상을 선택하세요.')
        with store.upload():
            staging = store.root / 'staging'
            staging.mkdir(exist_ok=True)
            path = staging / (uuid.uuid4().hex + ext)
            try:
                upload.save(path)
                info = video_info(path)
                yield path, upload.filename, info
            finally:
                path.unlink(missing_ok=True)

    @bp.route('/api/attempts/<aid>/source', methods=['GET','PUT'])
    def source(aid):
        store = service().store
        if request.method == 'GET':
            return send_file(store.source_path(aid), conditional=True)
        with staged_video(store) as (path, name, info):
            item = store.attach_source(aid,path,int(request.form.get('expected_revision',-1)),name)
        return jsonify(ok=True, attempt=present(item), video_info=info)

    @bp.get('/api/attempts/<aid>/frame')
    def source_frame(aid):
        path = service().store.source_path(aid)
        info = video_info(path)
        index = int(request.args.get('index', 0))
        if not 0 <= index < info['frames']:
            raise ValueError('프레임 번호를 확인하세요.')
        cap = cv2.VideoCapture(str(path))
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES,index)
            ok, frame = cap.read()
        finally:
            cap.release()
        if not ok:
            raise ValueError('프레임을 읽지 못했습니다.')
        ok, image = cv2.imencode('.jpg',frame)
        if not ok:
            raise ValueError('프레임을 표시하지 못했습니다.')
        return app.response_class(image.tobytes(), mimetype='image/jpeg')

    @bp.get('/api/attempts/<aid>/video-info')
    def source_info(aid):
        return jsonify(ok=True, video_info=video_info(service().store.source_path(aid)))

    @bp.post('/api/attempts/<aid>/analysis')
    def analyze(aid):
        data = body()
        config = data.get('config',{})
        if not isinstance(config,dict):
            raise ValueError('분석 설정을 확인하세요.')
        run = service().enqueue(aid,data['request_key'],data.get('expected_analysis_input_revision'),config)
        return jsonify(ok=True, run=run), 202

    @bp.get('/api/runs/<rid>')
    def run(rid):
        return jsonify(ok=True, run=service().store.get_run(rid))

    @bp.post('/api/attempts/<aid>/clip-confirmation')
    def confirm(aid):
        data = body()
        return jsonify(ok=True, attempt=present(service().store.confirm_clip(aid,data['run_id'],data.get('expected_revision'))))

    @bp.get('/api/runs/<rid>/files/<key>')
    def artifact(rid,key):
        store = service().store
        run = store.get_run(rid)
        relative = (run.get('result') or {}).get('artifacts',{}).get(key)
        if not relative:
            raise KeyError('결과 파일이 없습니다.')
        return send_file(store.safe_path(relative), conditional=True)

    @bp.get('/api/athletes/<person>/history')
    def history(person):
        if request.args.get('deleted') == '1':
            return jsonify(ok=True, attempts=[present(x) for x in service().store.list_attempts(person,deleted=True)])
        h = build_history(service().store, person)
        h['attempts'] = [present(a) for a in h['attempts']]
        return jsonify(ok=True, history=h)

    @bp.post('/api/athletes/<person>/comparison')
    def comparison(person):
        ids = body().get('attempt_ids',[])
        if not isinstance(ids,list) or not 2 <= len(ids) <= 5 or len(set(ids)) != len(ids):
            raise ValueError('서로 다른 시도를 2~5개 선택하세요.')
        items = [service().store.get_attempt(i) for i in ids]
        if any(a['athlete_id'] != person or a['deleted_at'] for a in items):
            raise ValueError('같은 사람의 저장된 시도를 선택하세요.')
        return jsonify(ok=True, attempts=[{**present(a), 'personal_feedback': build_personal_feedback(a, items)} for a in items])

    @bp.post('/api/athletes/<person>/session')
    def session(person):
        data = body()
        result = service().store.save_session(person,data['performed_on'],data.get('focus',''),data.get('practice_note',''))
        return jsonify(ok=True, session=result)

    @bp.post('/api/backups')
    def backup():
        return jsonify(ok=True, backup=create_backup(service().store))

    @bp.get('/api/backups/<name>')
    def download(name):
        if not name.startswith('JumpCoach-') or not name.endswith('.zip') or Path(name).name != name:
            raise ValueError('백업 파일을 확인하세요.')
        return send_file(service().store.root.parent/'backups'/name,as_attachment=True,download_name=name)

    app.register_blueprint(bp)
