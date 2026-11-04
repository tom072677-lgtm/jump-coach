import importlib.util

import pytest

from test_contact_events import jump, refine


def contract():
    assert importlib.util.find_spec('metric_contract'), '누적 지표 계약이 필요합니다'
    import metric_contract
    return metric_contract


def sample():
    return {'tracking_reliable': True, 'primary_side': 'L',
            'landing_event': {'takeoff_reliable': True, 'reliable': True},
            'raw_measurements': {'flight_time_video_s': .6, 'takeoff_speed_video_mps': 3.,
                'takeoff_angle_deg': 30., 'landing_foot_ahead_of_hip_m': .4,
                'flight_com_visible': True,
                'landing_foot_ahead_of_com_m': .3, 'landing_knee_angle_deg': 150.,
                'knee_angle_change_apex_to_contact_deg': 30., 'trajectory_r2': .99},
            'moments': {}}


def context(**extra):
    return {'timebase': {'uniform': True}, 'config': {'time_mode': 'normal', 'camera_setup': '고정'},
            'pipeline_version': 'v1', 'model_sha256': 'test', **extra}


def test_absolute_metrics_need_independent_calibration():
    module = contract()
    result = module.metric_observations(sample(), context())
    assert result['flight_time_s']['eligible']
    assert result['takeoff_angle_deg']['eligible']
    assert not result['takeoff_speed_mps']['eligible']
    assert not result['landing_foot_ahead_of_hip_m']['eligible']


def test_landing_cannot_certify_takeoff_or_time():
    module = contract()
    m = sample()
    m['landing_event'].pop('takeoff_reliable')
    result = module.metric_observations(m, context())
    assert not result['flight_time_s']['eligible']
    assert not result['takeoff_angle_deg']['eligible']


def test_variable_timing_is_not_real_time_and_known_slowdown_is_applied():
    module = contract()
    assert not module.metric_observations(sample(), context(timebase={'uniform': False}))['flight_time_s']['eligible']
    c = context()
    c['config'] = {'time_mode': 'slow', 'slowdown': 4, 'camera_setup': '고정'}
    assert module.metric_observations(sample(), c)['flight_time_s']['value'] == pytest.approx(.15)


def test_measured_record_is_not_a_calibration_input():
    module = contract()
    c = context()
    c['measured_distance_mm'] = 2400
    first = module.metric_observations(sample(), c)
    c['measured_distance_mm'] = 2900
    assert module.metric_observations(sample(), c) == first


def test_takeoff_boundary_quality_is_separate_from_landing():
    result = refine(jump())
    assert 'takeoff_reliable' in result
    assert result['takeoff_reliable'] is True
    assert result['reliable'] is False
    data = jump()
    data[1][6:10, [30,32]] = 0
    assert refine(data)['takeoff_reliable'] is False


def test_calibration_has_independent_finite_points():
    module = contract()
    calibration = {'points': [[0, 0], [200, 0]], 'length_cm': 100, 'frame': 0}
    assert module.calibration_scale(calibration, [1920,1080]) == 200
    with pytest.raises(ValueError):
        module.calibration_scale({'points': [[0,0],[0,0]], 'length_cm': 100, 'frame': 0}, [1920,1080])


def test_good_parabola_does_not_replace_visible_com_inputs():
    m = sample()
    m['raw_measurements']['flight_com_visible'] = False
    values = contract().metric_observations(m,context(independent_scale=True))
    assert not values['takeoff_speed_mps']['eligible']
    assert not values['takeoff_angle_deg']['eligible']


def test_compatibility_version_separates_groups_but_text_edits_do_not():
    first = contract().metric_observations(sample(),context())
    second = contract().metric_observations(sample(),context(pipeline_version='v2'))
    assert first['flight_time_s']['comparison_group'] == second['flight_time_s']['comparison_group']
    changed = contract().metric_observations(sample(),context(metric_schema_version='personal-v2'))
    assert first['flight_time_s']['comparison_group'] != changed['flight_time_s']['comparison_group']


def test_visible_side_estimates_are_shown_without_claiming_both_feet_verified():
    m = sample()
    m['landing_event'] = {'takeoff_observed': True, 'landing_observed': True,
                          'takeoff_reliable': False, 'reliable': False}
    m['raw_measurements'].update(flight_com_visible=False, flight_primary_visible=True)
    values = contract().metric_observations(m, context())
    for name in ('flight_time_s','takeoff_angle_deg'):
        assert values[name]['displayable']
        assert not values[name]['eligible']
    assert not values['takeoff_speed_mps']['displayable']


def test_unknown_boundary_or_bad_fit_still_hides_estimates():
    m = sample()
    m['landing_event'] = {'takeoff_observed': False, 'landing_observed': True}
    values = contract().metric_observations(m, context())
    assert not values['flight_time_s']['displayable']
    m['landing_event']['takeoff_observed'] = True
    m['raw_measurements']['trajectory_r2'] = .4
    assert not contract().metric_observations(m, context())['takeoff_angle_deg']['displayable']


def test_camera_note_is_for_comparison_not_single_clip_measurement():
    c = context()
    c['config']['camera_setup'] = ''
    value = contract().metric_observations(sample(), c)['flight_time_s']
    assert value['displayable'] and not value['eligible']


def test_impossible_flight_hides_all_derived_estimates():
    m = sample()
    m['raw_measurements']['flight_time_video_s'] = 7
    values = contract().metric_observations(m, context(independent_scale=True))
    assert all(not values[k]['displayable'] for k in
               ('flight_time_s','takeoff_speed_mps','takeoff_angle_deg'))
    assert all(not values[k]['eligible'] for k in
               ('flight_time_s','takeoff_speed_mps','takeoff_angle_deg'))
