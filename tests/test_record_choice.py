import io

import pytest

from test_personal_api import api, create, video


@pytest.mark.parametrize('validity', ['valid', 'foul'])
def test_analysis_requires_record_or_explicit_unknown(api, tmp_path, validity):
    client, app = api
    a = create(client, distance=None)
    a = client.patch(f"/api/attempts/{a['id']}", json={
        'expected_revision': a['revision'], 'validity': validity}).get_json()['attempt']
    a = client.put(f"/api/attempts/{a['id']}/source", data={
        'video': (io.BytesIO(video(tmp_path)), 'jump.avi'),
        'expected_revision': a['revision']}).get_json()['attempt']
    request = {'request_key': 'run', 'expected_analysis_input_revision': 0, 'config': {}}
    response = client.post(f"/api/attempts/{a['id']}/analysis", json=request)
    assert response.status_code == 400
    assert '알 수 없습니다' in response.get_json()['error']
    assert client.get(f"/api/attempts/{a['id']}").get_json()['attempt']['latest_run'] is None
    a = client.patch(f"/api/attempts/{a['id']}", json={
        'expected_revision': a['revision'], 'record_unknown': True}).get_json()['attempt']
    assert a['record_unknown'] and a['measured_distance_cm'] is None
    assert a['validity'] == validity
    assert client.post(f"/api/attempts/{a['id']}/analysis", json=request).status_code == 202
    app.extensions['jumpcoach_service']().pool.shutdown()
    h = client.get(f"/api/athletes/{a['athlete_id']}/history").get_json()['history']
    assert h['record_points'] == []


@pytest.mark.parametrize('distance,unknown', [('240', True), (None, 'true'), (None, 1)])
def test_record_choice_cannot_be_ambiguous(api, distance, unknown):
    client, _ = api
    p = client.post('/api/athletes', json={'name': '검증'}).get_json()['athlete']
    response = client.post('/api/attempts', json={
        'athlete_id': p['id'], 'performed_on': '2026-11-04', 'request_key': 'a',
        'measured_distance_cm': distance, 'record_unknown': unknown})
    assert response.status_code == 400
    assert client.get(f"/api/athletes/{p['id']}/history").get_json()['history']['attempts'] == []


def test_unknown_survives_correction_backup_and_can_be_replaced_by_distance(tmp_path):
    from data_transfer import create_backup, verify_restore
    from storage import Store
    store = Store(tmp_path / 'data')
    p = store.save_athlete('검증')
    a = store.create_attempt(p['id'], '2026-11-04', 'a', record_unknown=True)
    src = tmp_path / 'jump.mp4'
    src.write_bytes(b'video')
    a = store.attach_source(a['id'], src, a['revision'])
    corrected = store.correct_attempt(a['id'], a['revision'], 'correct', src)
    assert corrected['record_unknown']
    backup = create_backup(store)
    restored_path = tmp_path / 'restored'
    verify_restore(store.root.parent / 'backups' / backup['filename'], restored_path)
    restored = Store(restored_path)
    assert restored.get_attempt(corrected['id'])['record_unknown']
    edited = restored.update_attempt(corrected['id'], corrected['revision'], {'measured_distance_cm': '245.5'})
    assert not edited['record_unknown']
    assert edited['measured_distance_cm'] == 245.5
    with pytest.raises(ValueError):
        restored.update_attempt(edited['id'], edited['revision'], {'record_unknown': True})


def test_delete_excludes_records_metrics_and_comparison_even_if_analysis_finishes_late(api, tmp_path):
    from history import build_history
    client, app = api
    a, other = create(client), create(client, key='other')
    store = app.extensions['jumpcoach_service']().store
    src = tmp_path / 'jump.mp4'
    src.write_bytes(b'video')
    a = store.attach_source(a['id'], src, a['revision'])
    run = store.create_run(a['id'], 'run', 0, {})
    store.start_run(run['id'])
    a = client.delete(f"/api/attempts/{a['id']}", json={'expected_revision': a['revision']}).get_json()['attempt']
    store.finish_run(run['id'], {'selection': {'start': 0, 'end': 2}, 'observations': {
        'flight_time_s': {'comparison_group': 'g', 'value': .5, 'eligible': True}}})
    with pytest.raises(ValueError):
        store.create_run(a['id'], 'run', 0, {})
    with pytest.raises(ValueError):
        store.create_run(a['id'], 'new-run', 0, {})
    h = build_history(store, a['athlete_id'])
    assert [x['id'] for x in h['attempts']] == [other['id']]
    assert h['counts']['attempts'] == 1 and len(h['record_points']) == 1
    assert h['metrics']['flight_time_s']['points'] == []
    assert client.post(f"/api/athletes/{a['athlete_id']}/comparison", json={
        'attempt_ids': [a['id'], other['id']]}).status_code == 400
    assert store.source_path(a['id']).read_bytes() == b'video'
    restored = client.post(f"/api/attempts/{a['id']}/restore", json={
        'expected_revision': a['revision']}).get_json()['attempt']
    store.confirm_clip(a['id'], run['id'], restored['revision'])
    h = build_history(store, a['athlete_id'])
    assert h['counts']['attempts'] == 2 and len(h['record_points']) == 2
    assert len(h['metrics']['flight_time_s']['points']) == 1
