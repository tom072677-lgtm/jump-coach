"""개인 내 실제 차이, 비교 대상의 소유권·상태, 관측값과 원인 구분."""
from copy import deepcopy
import importlib.util

import pytest


def module():
    assert importlib.util.find_spec('personal_feedback'), '개인별 시도 비교가 필요합니다'
    import personal_feedback
    return personal_feedback


def attempt(key, record, speed, flight, knee_delta, reach):
    moments = {}
    for phase, knee, frame in [('load', 67, 10), ('drive', 94, 14),
                               ('takeoff', 161, 17), ('mid', 88, 21), ('landing', 101, 29)]:
        moments[phase] = {'available': True, 'reliable': phase != 'landing',
            'observed': phase == 'landing', 'frame': frame, 'measurements': [
                {'label': '무릎 포함각', 'value': knee, 'unit': '°'},
                {'label': '몸통과 지면의 각도', 'value': 8, 'unit': '°'}]}
    moments['landing']['measurements'] += [
        {'label': '다리 길이 대비 발–골반 간격', 'value': reach, 'unit': '%'},
        {'label': '다리 길이 대비 발–무게중심 간격', 'value': 23, 'unit': '%'}]
    values = {'takeoff_speed_mps': speed, 'takeoff_angle_deg': 25., 'flight_time_s': flight,
              'knee_angle_change_apex_to_contact_deg': knee_delta}
    data = {'metrics': {'fps': 24, 'primary_side': 'R', 'tracking_reliable': True,
              'landing_event': {'landing_observed': True},
              'raw_measurements': {'landing_joints_visible': True, 'apex_joints_visible': True,
                                   'knee_angle_change_apex_to_contact_deg': knee_delta},
              'moments': moments},
            'observations': {k: {'value': v, 'displayable': True, 'eligible': False}
                             for k, v in values.items()},
            'context': {'model_sha256': 'model', 'metric_schema_version': 'v1',
                        'pipeline_version': {'analyze_jump.py': 'v1'},
                        'config': {'camera_setup': '측면 고정', 'time_mode': 'normal'},
                        'timebase': {'uniform': True}}}
    return {'id': key, 'athlete_id': 'person', 'state': 'committed', 'deleted_at': None,
            'validity': 'valid', 'analysis_stale': False, 'analysis_included': True,
            'clip_confirmed': False, 'source_sha256': key, 'measured_distance_cm': record,
            'measurement_context': {'setup': '학원'}, 'performed_on': '2026-11-04',
            'attempt_no': 1, 'selected_run': {'id': key, 'status': 'succeeded', 'result': data}}


def pair():
    return attempt('low', 270, 2.92, 11/24, 13., 63.), attempt('high', 302, 3.30, .5, 29., 60.4)


def test_same_person_gets_signed_actual_differences_without_claiming_larger_reach():
    low, high = pair()
    report = module().build_personal_feedback(low, [low, high])
    assert report['reference']['id'] == 'high'
    rows = {x['key']: x for x in report['rows']}
    assert rows['takeoff_speed_mps']['difference'] == pytest.approx(-.38)
    assert rows['landing_hip_leg_pct']['difference'] == pytest.approx(2.6)
    assert rows['flight_time_s']['small'] is True  # 24fps 한 프레임 차이
    assert rows['knee_extension_deg']['difference'] == -16
    assert '13.0' in str(report['highlights']) and '29.0' in str(report['highlights'])
    assert '주원인' not in str(report) and '근력 부족' not in str(report)
    assert not low['clip_confirmed'] and not high['clip_confirmed']


def test_different_record_reference_precedes_another_equal_best_record():
    low, high = pair()
    equal = attempt('same-best', 302, 3.6, .5, 29., 63.3)
    report = module().build_personal_feedback(high, [equal, low, high])
    assert report['reference']['id'] == 'low'
    assert report['record_difference_cm'] == 32


@pytest.mark.parametrize('field,value', [
    ('athlete_id','other'), ('deleted_at','deleted'), ('analysis_stale',True),
    ('analysis_included',False), ('validity','foul'), ('state','draft'),
    ('source_sha256','low')])
def test_excludes_other_people_deleted_stale_unselected_and_duplicate_sources(field, value):
    low, high = pair()
    high[field] = value
    assert module().build_personal_feedback(low, [low, high]) is None


@pytest.mark.parametrize('field,value', [('camera_setup','정면'), ('camera_setup','')])
def test_camera_conditions_must_match(field, value):
    low, high = pair()
    high['selected_run']['result']['context']['config'][field] = value
    assert module().build_personal_feedback(low,[high]) is None


def test_analysis_version_or_missing_metadata_does_not_silently_mix():
    low, high = pair()
    high['selected_run']['result']['context']['pipeline_version'] = {'analyze_jump.py':'v2'}
    assert module().build_personal_feedback(low,[high]) is None
    high['selected_run']['result']['context'] = {}
    assert module().build_personal_feedback(low,[high]) is None


def test_missing_values_and_hidden_joint_do_not_become_zero_or_comparison():
    low, high = pair()
    data = high['selected_run']['result']
    data['observations']['takeoff_speed_mps']['displayable'] = False
    data['metrics']['raw_measurements']['landing_joints_visible'] = False
    data['metrics']['moments']['landing']['observed'] = False
    report = module().build_personal_feedback(low,[high])
    keys = {r['key'] for r in report['rows']}
    assert 'takeoff_speed_mps' not in keys and 'knee_extension_deg' not in keys
    assert 'landing_hip_leg_pct' not in keys


def test_unknown_time_does_not_compare_drive_duration_or_turn_frames_into_seconds():
    low, high = pair()
    high['selected_run']['result']['context']['config']['time_mode'] = 'unknown'
    high['selected_run']['result']['observations']['flight_time_s']['displayable'] = False
    report = module().build_personal_feedback(low,[high])
    assert not any(r['key'] in ('drive_lead_s','push_duration_s','flight_time_s') for r in report['rows'])


def test_inputs_are_unchanged_by_personal_feedback():
    items = pair()
    before = deepcopy(items)
    module().build_personal_feedback(items[0],items)
    assert items == before


def test_sqlite_integer_include_flag_is_supported():
    low, high = pair()
    low['analysis_included'] = high['analysis_included'] = 1
    assert module().build_personal_feedback(low,[high]) is not None


def test_highest_record_without_comparable_observations_does_not_hide_good_reference():
    low, high = pair()
    bad = attempt('bad',320,3.5,.5,30,65)
    bad['selected_run']['result']['observations'] = {}
    bad['selected_run']['result']['metrics']['tracking_reliable'] = False
    report = module().build_personal_feedback(low,[high,bad])
    assert report['reference']['id'] == 'high'


def test_identical_measurements_never_produce_a_directional_difference():
    low, high = pair()
    high['selected_run']['result'] = deepcopy(low['selected_run']['result'])
    report = module().build_personal_feedback(low,[high])
    assert all(r['difference'] == 0 and r['small'] for r in report['rows'])


def test_explicit_slow_motion_is_converted_before_comparing_phase_durations():
    low, high = pair()
    d=high['selected_run']['result']
    d['context']['config'].update(time_mode='slow',slowdown=4)
    for moment in d['metrics']['moments'].values():
        moment['frame'] *= 4
    report=module().build_personal_feedback(low,[high])
    assert all(r['difference'] == pytest.approx(0) for r in report['rows']
               if r['key'] in ('drive_lead_s','push_duration_s'))


def test_record_edits_do_not_change_observed_motion_differences():
    low, high = pair()
    first=module().build_personal_feedback(low,[high])
    high['measured_distance_cm'] = 320
    second=module().build_personal_feedback(low,[high])
    assert first['rows'] == second['rows']


def _takeoff_rows(speed_diff, angle_diff, angle_small):
    """highlights()가 쓰는 도약 행 세트를 직접 구성한다."""
    return [
        {'key': 'takeoff_speed_mps', 'label': '도약 속도', 'phase': 'takeoff', 'unit': 'm/s',
         'before': 3.47, 'after': 3.47 + speed_diff, 'difference': speed_diff,
         'small': abs(speed_diff) <= .15 + 1e-6},
        {'key': 'takeoff_angle_deg', 'label': '도약 각도', 'phase': 'takeoff', 'unit': '°',
         'before': 25., 'after': 25. + angle_diff, 'difference': angle_diff, 'small': angle_small},
        {'key': 'flight_time_s', 'label': '체공 시간', 'phase': 'takeoff', 'unit': 's',
         'before': .5, 'after': .567, 'difference': .067, 'small': False},
    ]


def test_slower_speed_but_bigger_angle_and_higher_record_tells_to_raise_angle_and_speed():
    rows = _takeoff_rows(speed_diff=-.18, angle_diff=3.6, angle_small=False)
    result = module().highlights(rows, record_difference=14)
    takeoff = next(x for x in result if x['phase'] == 'takeoff')
    assert takeoff['action'] == ('도약 속도는 줄었지만 도약 각도를 크게 하여 체공시간을 확보하였습니다. '
                                 '도약 각도를 30도 수준으로 올리면서 도약 속도를 늘려보세요.')


def test_slower_speed_higher_record_but_flat_angle_keeps_existing_action():
    rows = _takeoff_rows(speed_diff=-.18, angle_diff=0.1, angle_small=True)
    result = module().highlights(rows, record_difference=14)
    takeoff = next(x for x in result if x['phase'] == 'takeoff')
    assert takeoff['action'] == '이번 밀어내기 동작을 다시 재현해 속도와 기록이 함께 유지되는지 확인하세요.'


def test_faster_speed_and_higher_record_keeps_existing_reproduce_action():
    rows = _takeoff_rows(speed_diff=.2, angle_diff=3.6, angle_small=False)
    result = module().highlights(rows, record_difference=14)
    takeoff = next(x for x in result if x['phase'] == 'takeoff')
    assert takeoff['action'] == '이번 밀어내기 동작을 다시 재현해 속도와 기록이 함께 유지되는지 확인하세요.'


def test_slower_speed_with_lower_record_keeps_existing_push_action():
    rows = _takeoff_rows(speed_diff=-.18, angle_diff=3.6, angle_small=False)
    result = module().highlights(rows, record_difference=-14)
    takeoff = next(x for x in result if x['phase'] == 'takeoff')
    assert takeoff['action'] == ('하강에서 발이 떨어질 때까지 밀어내는 동작을 이어가며, '
                                 '다음 시도의 속도와 기록이 함께 바뀌는지 확인하세요.')


def test_more_speed_with_a_lower_record_is_not_automatically_praised():
    low, high = pair()
    low['selected_run']['result']['observations']['takeoff_speed_mps']['value'] = 4
    report=module().build_personal_feedback(low,[high])
    assert '기록은 낮았습니다' in report['highlights'][0]['action']
    assert '재현' not in report['highlights'][0]['action']


def test_api_comparison_refreshes_after_reference_deleted_or_excluded(tmp_path):
    import io
    import cv2
    import numpy as np
    import web_app
    app=web_app.create_app(tmp_path/'data',analyzer=lambda *_: {})
    client=app.test_client()
    person=client.post('/api/athletes',json={'name':'비교 검증'}).json['athlete']
    service=app.extensions['jumpcoach_service']()
    saved=[]
    for index,example in enumerate(pair()):
        a=client.post('/api/attempts',json={'athlete_id':person['id'],'performed_on':'2026-11-04',
            'request_key':str(index),'measured_distance_cm':example['measured_distance_cm']}).json['attempt']
        path=tmp_path/f'{index}.avi'
        writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),24,(32,32))
        for _ in range(3):writer.write(np.full((32,32,3),index*100,np.uint8))
        writer.release()
        a=client.put(f"/api/attempts/{a['id']}/source",data={'video':(io.BytesIO(path.read_bytes()),path.name),
            'expected_revision':a['revision']}).json['attempt']
        run=service.store.create_run(a['id'],str(index),a['analysis_input_revision'],
                                     {'camera_setup':'측면 고정','time_mode':'normal'})
        service.store.start_run(run['id'])
        data=deepcopy(example['selected_run']['result'])
        data['selection']={'start_frame':17,'end_frame':29}
        service.store.finish_run(run['id'],data)
        saved.append(service.store.get_attempt(a['id']))
    low,high=saved
    get=lambda:client.get(f"/api/attempts/{low['id']}").json['attempt']['personal_feedback']
    assert get()['reference']['id'] == high['id']
    r=client.post(f"/api/athletes/{person['id']}/comparison",json={'attempt_ids':[a['id'] for a in saved]})
    assert all(a['personal_feedback'] for a in r.json['attempts'])
    high=client.patch(f"/api/attempts/{high['id']}",json={'expected_revision':high['revision'],
                      'analysis_included':False}).json['attempt']
    assert get() is None
    high=client.patch(f"/api/attempts/{high['id']}",json={'expected_revision':high['revision'],
                      'analysis_included':True}).json['attempt']
    assert get() is not None
    assert client.delete(f"/api/attempts/{high['id']}",json={'expected_revision':high['revision']}).status_code == 200
    assert get() is None
