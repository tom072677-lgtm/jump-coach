import hashlib
import importlib.util
from pathlib import Path

import pytest


def make_store(tmp_path):
    assert importlib.util.find_spec('storage'), '개인 기록 저장소가 필요합니다'
    from storage import Store
    return Store(tmp_path / 'data')


def new_attempt(store, name='윤수', key='one', distance='245.5', **extra):
    person = store.save_athlete(name)
    return store.create_attempt(person['id'], '2026-11-04', key,
                                measured_distance_cm=distance, **extra)


def test_names_and_records_survive_reopening(tmp_path):
    store = make_store(tmp_path)
    first = new_attempt(store)
    assert new_attempt(store, key='two')['athlete_id'] == first['athlete_id']
    other = new_attempt(store, name='학생', key='three')
    reopened = type(store)(store.root)
    assert reopened.get_attempt(first['id'])['measured_distance_mm'] == 2455
    assert len(reopened.list_attempts(first['athlete_id'])) == 2
    assert len(reopened.list_attempts(other['athlete_id'])) == 1


@pytest.mark.parametrize('bad', ['0', '-1', 'NaN', 'Infinity', '245.55', '1e100'])
def test_invalid_records_are_rejected(tmp_path, bad):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        new_attempt(store, distance=bad)


def test_retry_after_edit_does_not_undo_record(tmp_path):
    store = make_store(tmp_path)
    a = new_attempt(store)
    changed = store.update_attempt(a['id'], a['revision'], {'measured_distance_cm': '250'})
    retry = new_attempt(store)
    assert retry['id'] == a['id']
    assert retry['measured_distance_mm'] == 2500
    with pytest.raises(ValueError):
        store.update_attempt(a['id'], a['revision'], {'measured_distance_cm': '240'})
    assert changed['revision'] == a['revision'] + 1


def test_source_is_immutable_and_rerun_is_not_an_attempt(tmp_path):
    store = make_store(tmp_path)
    a = new_attempt(store)
    source = tmp_path / 'input.mp4'
    source.write_bytes(b'original video')
    a = store.attach_source(a['id'], source, a['revision'])
    assert store.attach_source(a['id'], source, 0)['id'] == a['id']
    wrong = tmp_path / 'wrong.mp4'
    wrong.write_bytes(b'another video')
    with pytest.raises(ValueError):
        store.attach_source(a['id'], wrong, a['revision'])
    run = store.create_run(a['id'], 'run1', 0, {'time_mode': 'normal'})
    store.fail_run(run['id'], 'test', '실패')
    assert store.get_attempt(a['id'])['measured_distance_mm'] == 2455
    assert len(store.list_attempts(a['athlete_id'])) == 1
    assert hashlib.sha256(store.source_path(a['id']).read_bytes()).hexdigest() == a['source_sha256']


def test_run_request_retry_precedes_revision_check(tmp_path):
    store = make_store(tmp_path)
    a = new_attempt(store)
    source = tmp_path / 'input.mp4'
    source.write_bytes(b'video')
    a = store.attach_source(a['id'], source, a['revision'])
    run = store.create_run(a['id'], 'run1', 0, {'time_mode': 'normal'})
    again = store.create_run(a['id'], 'run1', 0, {'time_mode': 'normal'})
    assert run['id'] == again['id']
    assert store.get_attempt(a['id'])['analysis_input_revision'] == 1


def test_empty_foul_delete_and_restart_states(tmp_path):
    store = make_store(tmp_path)
    a = new_attempt(store, distance=None, validity='foul')
    assert a['measured_distance_mm'] is None
    a = store.update_attempt(a['id'], a['revision'], {'deleted': True})
    assert store.list_attempts(a['athlete_id']) == []
    restored = store.update_attempt(a['id'], a['revision'], {'deleted': False})
    assert restored['validity'] == 'foul'


def test_changed_input_keeps_previous_result_but_marks_it_stale(tmp_path):
    store = make_store(tmp_path)
    a = new_attempt(store)
    source = tmp_path / 'input.mp4'
    source.write_bytes(b'video')
    a = store.attach_source(a['id'], source, a['revision'])
    run = store.create_run(a['id'], 'r1', 0, {'time_mode': 'normal'})
    store.finish_run(run['id'], {'metrics': {}, 'artifacts': {}, 'selection': {'start': 0, 'end': 1}})
    a = store.get_attempt(a['id'])
    assert a['selected_run']['status'] == 'succeeded'
    second = store.create_run(a['id'], 'r2', a['analysis_input_revision'], {'time_mode': 'slow', 'slowdown': 4})
    store.fail_run(second['id'], 'test', '실패')
    a = store.get_attempt(a['id'])
    assert a['selected_run']['id'] == run['id']
    assert a['analysis_stale'] is True


def test_inflight_runs_become_interrupted_on_restart(tmp_path):
    store = make_store(tmp_path)
    a = new_attempt(store)
    src = tmp_path / 'a.mp4'
    src.write_bytes(b'x')
    store.attach_source(a['id'], src, a['revision'])
    run = store.create_run(a['id'], 'r', 0, {})
    type(store)(store.root).recover_runs()
    assert store.get_run(run['id'])['status'] == 'interrupted'


def test_wrong_source_correction_is_atomic_and_does_not_double_count(tmp_path):
    from history import build_history
    store = make_store(tmp_path)
    a = new_attempt(store)
    src = tmp_path / 'wrong.mp4'
    src.write_bytes(b'wrong source')
    a = store.attach_source(a['id'], src, a['revision'])
    right = tmp_path / 'right.mp4'
    right.write_bytes(b'correct video')
    corrected = store.correct_attempt(a['id'], a['revision'], 'correction1', right)
    retry = store.correct_attempt(a['id'], a['revision'], 'correction1', right)
    assert retry['id'] == corrected['id']
    assert store.source_path(corrected['id']).read_bytes() == b'correct video'
    assert corrected['measured_distance_cm'] == 245.5
    old = store.get_attempt(a['id'])
    assert old['superseded_by_attempt_id'] == corrected['id']
    assert store.source_path(a['id']).read_bytes() == b'wrong source'
    with pytest.raises(ValueError):
        store.update_attempt(a['id'], old['revision'], {'deleted': False})
    h = build_history(store, a['athlete_id'])
    assert h['counts']['attempts'] == 1
    assert len(h['record_points']) == 1


def test_record_points_are_enriched_and_sorted_chronologically(tmp_path):
    from history import build_history
    store = make_store(tmp_path)
    person = store.save_athlete('선수')
    # Three attempts on 11-04 and two on 11-02, created out of chronological order.
    plan = [
        ('2026-11-04', '240'),
        ('2026-11-02', '230'),
        ('2026-11-04', '250'),
        ('2026-11-02', '235'),
        ('2026-11-04', '245'),
    ]
    for i, (day, distance) in enumerate(plan):
        store.create_attempt(person['id'], day, f'rec{i}', measured_distance_cm=distance)
    points = build_history(store, person['id'])['record_points']
    # (a) every record carries an integer attempt_no
    assert all(isinstance(p['attempt_no'], int) for p in points)
    # (b) sorted ascending by (date, attempt_no)
    keys = [(p['date'], p['attempt_no']) for p in points]
    assert keys == sorted(keys)
    # (c) same-day attempts remain separate entries (not collapsed into one)
    assert len(points) == 5
    day_04 = [p for p in points if p['date'] == '2026-11-04']
    assert len(day_04) == 3
    assert len({p['attempt_no'] for p in day_04}) == 3
    # the first entry is the earliest day, not the DESC order returned by list_attempts
    assert points[0]['date'] == '2026-11-02'


def test_failed_same_settings_rerun_preserves_confirmed_result(tmp_path):
    store = make_store(tmp_path)
    a = new_attempt(store)
    src = tmp_path / 'jump.mp4'
    src.write_bytes(b'video')
    a = store.attach_source(a['id'], src, a['revision'])
    first = store.create_run(a['id'], 'r1', 0, {'time_mode':'normal'})
    store.finish_run(first['id'], {'selection': {'start':0,'end':3}})
    a = store.get_attempt(a['id'])
    store.confirm_clip(a['id'], first['id'], a['revision'])
    second = store.create_run(a['id'], 'r2', a['analysis_input_revision'], {'time_mode':'normal'})
    store.fail_run(second['id'], 'test', '실패')
    a = store.get_attempt(a['id'])
    assert a['selected_run']['id'] == first['id']
    assert a['clip_confirmed'] and not a['analysis_stale']
