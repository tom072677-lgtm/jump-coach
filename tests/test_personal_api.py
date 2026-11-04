import importlib.util
import io
import time

import cv2
import numpy as np
import pytest


@pytest.fixture
def api(tmp_path):
    import web_app
    assert hasattr(web_app, 'create_app'), '개인 이력 API 초기화가 필요합니다'

    def fake_analysis(store, run):
        return {'metrics': {}, 'observations': {}, 'artifacts': {}, 'shots': [],
                'selection': {'start_frame': 0, 'end_frame': 2}}

    app = web_app.create_app(tmp_path / 'data', analyzer=fake_analysis)
    app.config['TESTING'] = True
    return app.test_client(), app


def create(client, name='윤수', key='one', distance='245.5', date='2026-11-04'):
    person = client.post('/api/athletes', json={'name': name}).get_json()['athlete']
    response = client.post('/api/attempts', json={'athlete_id': person['id'], 'performed_on': date,
                           'request_key': key, 'measured_distance_cm': distance, 'validity': 'valid'})
    assert response.status_code == 201
    return response.get_json()['attempt']


def video(tmp_path):
    path = tmp_path / 'jump.avi'
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 30, (32,32))
    for _ in range(3):
        out.write(np.zeros((32,32,3), np.uint8))
    out.release()
    return path.read_bytes()


def test_name_save_manual_record_and_history(api):
    client, app = api
    a = create(client)
    b = create(client, name='학생', key='two', distance='200')
    data = client.get(f"/api/athletes/{a['athlete_id']}/history").get_json()
    assert data['history']['record_dates'][0]['median'] == 245.5
    assert len(data['history']['attempts']) == 1
    assert data['history']['attempts'][0]['id'] != b['id']
    assert not app.config.get('LOGIN_REQUIRED')


def test_upload_failure_keeps_record(api):
    client, _ = api
    a = create(client)
    r = client.put(f"/api/attempts/{a['id']}/source", data={'video': (io.BytesIO(b'bad'), 'bad.mp4'), 'expected_revision': a['revision']})
    assert r.status_code == 400
    assert client.get(f"/api/attempts/{a['id']}").get_json()['attempt']['measured_distance_cm'] == 245.5


def test_saved_analysis_reopens_and_does_not_count_twice(api, tmp_path):
    client, _ = api
    a = create(client)
    r = client.put(f"/api/attempts/{a['id']}/source", data={'video': (io.BytesIO(video(tmp_path)), 'jump.avi'), 'expected_revision': a['revision']})
    assert r.status_code == 200
    a = r.get_json()['attempt']
    assert client.get(f"/api/attempts/{a['id']}/source").status_code == 200
    request = {'request_key': 'analysis', 'expected_analysis_input_revision': 0, 'config': {'time_mode':'normal','camera_setup':'측면 고정'}}
    r = client.post(f"/api/attempts/{a['id']}/analysis", json=request)
    assert r.status_code == 202
    rid = r.get_json()['run']['id']
    for _ in range(100):
        run = client.get(f'/api/runs/{rid}').get_json()['run']
        if run['status'] == 'succeeded':
            break
        time.sleep(.01)
    assert run['status'] == 'succeeded'
    assert client.post(f"/api/attempts/{a['id']}/analysis", json=request).get_json()['run']['id'] == rid
    item = client.get(f"/api/attempts/{a['id']}").get_json()['attempt']
    assert item['selected_run']['id'] == rid
    assert len(client.get(f"/api/athletes/{a['athlete_id']}/history").get_json()['history']['attempts']) == 1


def test_record_edit_and_foul_change_history_without_analysis(api):
    client, _ = api
    a = create(client)
    r = client.patch(f"/api/attempts/{a['id']}", json={'expected_revision': a['revision'], 'measured_distance_cm': '250'})
    assert r.get_json()['attempt']['measured_distance_cm'] == 250
    a = r.get_json()['attempt']
    client.patch(f"/api/attempts/{a['id']}", json={'expected_revision':a['revision'],'validity':'foul'})
    h = client.get(f"/api/athletes/{a['athlete_id']}/history").get_json()['history']
    assert h['record_dates'] == []
    assert len(h['attempts']) == 1


def test_backup_restore_includes_deleted_records(api, tmp_path):
    client, app = api
    a = create(client)
    client.delete(f"/api/attempts/{a['id']}", json={'expected_revision':a['revision']})
    response = client.post('/api/backups')
    assert response.status_code == 200
    assert importlib.util.find_spec('data_transfer')
    from data_transfer import verify_restore
    service = app.extensions['jumpcoach_service']()
    path = service.store.root.parent / 'backups' / response.get_json()['backup']['filename']
    result = verify_restore(path, tmp_path / 'restored')
    assert result['attempts'] == 1


def test_failed_draft_upload_is_not_a_confirmed_attempt(api):
    client,_ = api
    p = client.post('/api/athletes',json={'name':'초안'}).get_json()['athlete']
    a = client.post('/api/attempts',json={'athlete_id':p['id'],'performed_on':'2026-11-04',
        'request_key':'draft','commit':False}).get_json()['attempt']
    assert client.put(f"/api/attempts/{a['id']}/source",data={'video':(io.BytesIO(b'bad'),'bad.mp4'),
        'expected_revision':a['revision']}).status_code == 400
    h = client.get(f"/api/athletes/{p['id']}/history").get_json()['history']
    assert h['counts']['attempts'] == 0
    r = client.patch(f"/api/attempts/{a['id']}",json={'expected_revision':a['revision'],
        'measured_distance_cm':250,'commit':True})
    assert r.status_code == 200
    assert client.get(f"/api/athletes/{p['id']}/history").get_json()['history']['counts']['attempts'] == 1


def test_invalid_correction_leaves_original_current(api, tmp_path):
    client,_ = api
    a = create(client)
    a = client.put(f"/api/attempts/{a['id']}/source", data={'video':(io.BytesIO(video(tmp_path)),'jump.avi'),
        'expected_revision':a['revision']}).get_json()['attempt']
    r = client.post(f"/api/attempts/{a['id']}/correction", data={'video':(io.BytesIO(b'bad'),'bad.mp4'),
        'expected_revision':a['revision'],'request_key':'correction'})
    assert r.status_code == 400
    current = client.get(f"/api/attempts/{a['id']}").get_json()['attempt']
    assert current['source_sha256'] == a['source_sha256']
    assert current['deleted_at'] is None
