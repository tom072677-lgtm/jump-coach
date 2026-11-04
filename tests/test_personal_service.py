from analysis_service import presentation_shots


def test_photo_metrics_obey_same_eligibility_as_history():
    moments = {'takeoff':{'measurements':[
        {'label':'추정 도약각','unit':'°','value':30},
        {'label':'무릎 포함각','unit':'°','value':160},
        {'label':'추정 이륙 속도','unit':'m/s','value':3}]}}
    observations = {k:{'eligible':False} for k in ('takeoff_speed_mps','takeoff_angle_deg',
                    'landing_foot_ahead_of_hip_m','landing_foot_ahead_of_com_m')}
    shots = presentation_shots({'moments':moments},{},'r',observations)
    assert shots[2]['measurements'] == [{'label':'무릎 포함각','unit':'°','value':160}]
    observations['takeoff_angle_deg'] = {'eligible':True,'value':31.12,'label':'도약 각도','unit':'°'}
    shots = presentation_shots({'moments':moments},{},'r',observations)
    assert shots[2]['measurements'][-1]['value'] == 31.12
    assert moments['takeoff']['measurements'][0]['value'] == 30


def test_photo_can_show_observed_estimate_without_approving_comparison():
    observations = {k:{'eligible':False,'displayable':False} for k in
        ('takeoff_speed_mps','takeoff_angle_deg','landing_foot_ahead_of_hip_m','landing_foot_ahead_of_com_m')}
    observations['takeoff_angle_deg'].update(displayable=True,value=25.4,label='도약 각도',unit='°')
    shots = presentation_shots({'moments':{}},{},'r',observations)
    assert shots[2]['measurements'] == [{'label':'추정 도약 각도','value':25.4,'unit':'°'}]
    assert not observations['takeoff_angle_deg']['eligible']


def test_worker_rejects_source_changed_after_upload(tmp_path):
    from analysis_service import AnalysisService
    called = []
    service = AnalysisService(tmp_path/'data',tmp_path/'unused-model',analyzer=lambda *args:called.append(1))
    store = service.store
    p = store.save_athlete('검증')
    a = store.create_attempt(p['id'],'2026-11-04','a',240)
    src = tmp_path/'original.mp4'
    src.write_bytes(b'original')
    store.attach_source(a['id'],src,a['revision'])
    run = store.create_run(a['id'],'r',0,{})
    store.source_path(a['id']).write_bytes(b'changed')
    service.process(run['id'])
    service.pool.shutdown()
    assert not called
    assert store.get_run(run['id'])['status'] == 'failed'
    assert '원본' in store.get_run(run['id'])['error_message']


def test_playback_contains_only_jump_window_in_original_frame_order(tmp_path):
    import cv2
    import numpy as np
    import pytest
    import analysis_service

    source = tmp_path/'annotated.mp4'
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'mp4v'), 24, (64,48))
    assert writer.isOpened()
    for frame in range(240):
        writer.write(np.full((48,64,3), frame, dtype=np.uint8))
    writer.release()
    output = tmp_path/'playback.mp4'
    metrics = {'fps':24, 'frames':240, 'pre_takeoff_frame':120, 'landing_frame':136}
    assert hasattr(analysis_service, 'create_playback'), '개인 분석 재생 영상은 점프 구간만 잘라야 합니다'
    window = analysis_service.create_playback(source, output, metrics, 24)
    cap = cv2.VideoCapture(str(output))
    fps = cap.get(cv2.CAP_PROP_FPS)
    decoded = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        decoded.append(float(frame.mean()))
    cap.release()
    assert len(decoded) == 34
    assert fps == pytest.approx(24)
    assert 1 <= len(decoded)/fps <= 2
    assert decoded[0] == pytest.approx(103, abs=6)
    assert decoded[-1] == pytest.approx(136, abs=6)
    assert window == {'start_frame':103, 'end_frame_exclusive':137,
                      'start_s':103/24, 'end_s':137/24, 'fps':24}
    assert metrics['pre_takeoff_frame'] == 120
    assert metrics['landing_frame'] == 136


def test_playback_encoding_failure_does_not_return_full_video(tmp_path, monkeypatch):
    import subprocess
    import pytest
    import analysis_service

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr=b'encoder unavailable')

    monkeypatch.setattr(analysis_service.subprocess, 'run', fail)
    assert hasattr(analysis_service, 'create_playback'), '전체 영상으로 조용히 되돌아가면 안 됩니다'
    with pytest.raises(ValueError, match='분석 영상'):
        analysis_service.create_playback(tmp_path/'full.mp4', tmp_path/'playback.mp4',
            {'fps':24, 'frames':240, 'pre_takeoff_frame':120, 'landing_frame':136}, 24)
