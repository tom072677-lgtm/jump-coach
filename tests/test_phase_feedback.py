"""Frame-specific phase feedback, first-contact geometry and trust boundaries."""
import importlib
import importlib.util
import json

import numpy as np
import pytest


def module():
    assert importlib.util.find_spec('phase_feedback') is not None, '구간별 분석 모듈이 필요합니다'
    return importlib.import_module('phase_feedback')


def sample(direction=1):
    n = 32
    pose = np.zeros((n, 33, 3), float)
    pose[:, :, 2] = 1
    pose[:, :, 0] = 300
    pose[:, :, 1] = 500
    pose[:, [11, 12], 1] = 300
    pose[:, [25, 26], 1] = 650
    pose[:, [27, 28, 29, 30, 31, 32], 1] = 800
    pose[:, [15, 16], 0] = 250
    pose[:, [15, 16], 1] = 600
    pose[11:, [15, 16], 0] = 450
    pose[11:, [15, 16], 1] = 290
    com = np.column_stack([60. + np.arange(n) * 10, np.full(n, 450.)])
    com[8, 1] = 550
    com[18, 1] = 250
    com[25:, 0] = 900  # later follow-through must not replace the first-contact CoM
    if direction < 0:
        pose[:, :, 0] = 1000 - pose[:, :, 0]
        com[:, 0] = 1000 - com[:, 0]
    frames = {'load': 8, 'drive': 11, 'takeoff': 14, 'mid': 18, 'landing': 24}
    contact = {'landing_frame': 24, 'contact_point_px': [500 if direction > 0 else 500, 800],
               'contact_landmark': 29, 'contact_side': 'L', 'contact_kind': 'heel',
               'reliable': True, 'method': 'image_roi', 'uncertainty_frames': 1,
               'note': '영상 최초 접촉 추정'}
    m = {'fps': 60, 'frames': n, 'primary_side': 'L', 'scale_px_per_m': 200,
         'tracking_reliable': True, 'absolute_metrics_reliable': True,
         'events_reliable': True, 'takeoff_frame': 15, 'apex_frame': 18,
         'landing_frame': 24, 'jump_direction': direction}
    angles = [np.full(n, v) for v in [150., 160., 110., 90.]]
    return m, pose, pose[:, :, 2].copy(), np.ones(n, bool), com, angles, frames, contact


def build(direction=1, **updates):
    m, pose, vis, valid, com, angles, frames, contact = sample(direction)
    m.update(updates)
    return module().build_phase_analysis(m, pose, vis, valid, com, *angles, frames, contact)


def test_first_contact_reach_uses_that_frame_and_has_forward_positive_sign():
    for direction in [1, -1]:
        out = build(direction)
        assert out['landing_metrics']['landing_foot_ahead_of_com_px'] == pytest.approx(200)
        assert out['landing_metrics']['landing_foot_ahead_of_hip_m'] == pytest.approx(1.)
        assert out['moments']['landing']['frame'] == 24
        text = json.dumps(out, ensure_ascii=False)
        assert '거리 손실이 없' not in text
        assert '뒤로 넘어지지' not in text


def test_landing_leg_ratio_is_scale_and_direction_invariant():
    for direction in (1, -1):
        m, pose, vis, valid, com, angles, frames, contact = sample(direction)
        for scale in (1, 2):
            c = dict(contact, contact_point_px=np.array(contact['contact_point_px']) * scale)
            out = module().build_phase_analysis(m, pose * [scale, scale, 1], vis, valid,
                                                com * scale, *angles, frames, c)
            assert out['landing_metrics']['landing_foot_ahead_of_hip_leg_pct'] == pytest.approx(200/300*100, abs=.1)
            assert '66.7%' in out['moments']['landing']['comment']


def test_leg_ratio_requires_visible_preparation_and_stable_segment_lengths():
    m, pose, vis, valid, com, angles, frames, contact = sample()
    vis[:15, [23, 25, 27]] = 0
    out = module().build_phase_analysis(m,pose,vis,valid,com,*angles,frames,contact)
    assert out['landing_metrics']['landing_foot_ahead_of_hip_leg_pct'] is None


def test_com_ratio_requires_visible_primary_body_but_hip_ratio_does_not_need_arms():
    m,pose,vis,valid,com,angles,frames,contact=sample()
    vis[24,15]=0
    out=module().build_phase_analysis(m,pose,vis,valid,com,*angles,frames,contact)
    assert out['landing_metrics']['landing_foot_ahead_of_com_leg_pct'] is None
    assert out['landing_metrics']['landing_foot_ahead_of_hip_leg_pct'] is not None
    assert 'None' not in out['moments']['landing']['comment']


def test_phase_comments_change_when_only_small_measured_motion_changes():
    m, pose, vis, valid, com, angles, frames, contact = sample()
    angles[0][8], angles[0][11], angles[0][14], angles[0][18], angles[0][24] = 67,94,161,88,101
    first = module().build_phase_analysis(m,pose,vis,valid,com,*angles,frames,contact)
    angles[0][11], angles[0][24] = 100,117
    second = module().build_phase_analysis(m,pose,vis,valid,com,*angles,frames,contact)
    assert first['moments']['drive']['comment'] != second['moments']['drive']['comment']
    assert '13.0°' in first['moments']['mid']['comment']
    assert '29.0°' in second['moments']['mid']['comment']


def test_unverified_scale_keeps_pixel_gap_and_hides_metric_gap():
    out = build(absolute_metrics_reliable=False)
    assert out['landing_metrics']['landing_foot_ahead_of_com_px'] == 200
    assert out['landing_metrics']['landing_foot_ahead_of_com_m'] is None
    assert all(x['unit'] not in ('m', 'cm', 'm/s') for x in out['moments']['landing']['measurements'])


def test_short_post_contact_clip_still_measures_contact_but_not_followthrough():
    m, pose, vis, valid, com, angles, frames, contact = sample()
    m['frames'] = 25
    out = module().build_phase_analysis(m, pose[:25], vis[:25], valid[:25], com[:25],
                                        *(a[:25] for a in angles), frames, contact)
    assert out['landing_metrics']['landing_contact_reliable'] is True
    assert out['landing_metrics']['landing_followthrough_reliable'] is False
    assert out['landing_metrics']['landing_foot_ahead_of_com_px'] == 200


def test_uncertain_contact_never_produces_a_confident_reach():
    m, pose, vis, valid, com, angles, frames, contact = sample()
    contact['reliable'] = False
    out = module().build_phase_analysis(m, pose, vis, valid, com, *angles, frames, contact)
    assert out['landing_metrics']['landing_foot_ahead_of_com_px'] is None
    assert out['moments']['landing']['reliable'] is False
    assert out['moments']['landing']['notes']


def test_tracking_failure_has_no_measurements_or_corrective_diagnosis():
    out = build(tracking_reliable=False)
    for moment in out['moments'].values():
        assert moment['measurements'] == []
        assert moment['cues'] == []
        assert not moment['reliable']
        assert '다시 촬영하세요' in moment['comment']
        assert '무릎을 펴' not in moment['comment']


def test_moments_are_ordered_and_use_last_support_before_takeoff():
    m, pose, vis, valid, com, angles, _, _ = sample()
    frames = module().select_moment_frames(pose, vis, valid, com, 15, 18, 24, 0)
    assert frames == {'load': 8, 'drive': 11, 'takeoff': 14, 'mid': 18, 'landing': 24}


def test_missing_preparation_is_not_fabricated_as_three_distinct_moments():
    m, pose, vis, valid, com, angles, _, _ = sample()
    frames = module().select_moment_frames(pose, vis, valid, com, 1, 3, 8, 0)
    assert frames['load'] is None and frames['drive'] is None
    assert frames['takeoff'] == 0


def test_missing_landmarks_do_not_turn_interpolated_contact_into_measurement():
    m, pose, vis, valid, com, angles, frames, contact = sample()
    valid[24] = False
    out = module().build_phase_analysis(m, pose, vis, valid, com, *angles, frames, contact)
    assert out['landing_metrics']['landing_foot_ahead_of_com_px'] is None
    assert out['moments']['landing']['measurements'] == []


@pytest.mark.parametrize('reuse_pose', [False, True])
def test_analysis_saves_first_contact_and_last_support_not_deepest_landing(monkeypatch, tmp_path, reuse_pose):
    import analyze_jump as a
    m, pose, vis, valid, com, angles, frames, contact = sample()
    contact.update(takeoff_frame=15, pre_takeoff_frame=14)
    angles[0][28] = 60  # later absorption must not become the landing photo
    if reuse_pose:
        valid[:8] = False
        valid[29:] = False
    pose_data = (60., (1000, 900), pose, pose[:, :, :3], valid)
    def extract(*args):
        assert not reuse_pose, '이미 추출한 자세를 두 번 추론하지 않아야 합니다'
        return pose_data
    monkeypatch.setattr(a, 'extract_pose', extract)
    def scale_for_selected_jump(norm, world, standing):
        assert not standing[:8].any() and not standing[29:].any()
        return 200., .1
    if reuse_pose:
        monkeypatch.setattr(a, 'auto_scale_px_per_m', scale_for_selected_jump)
    monkeypatch.setattr(a, 'center_of_mass', lambda *args: com.copy())
    monkeypatch.setattr(a, 'joint_angle', lambda world, x, b, z: angles[{25: 0, 23: 1, 27: 2, 11: 3}[b]].copy())
    monkeypatch.setattr(a, 'refine_contact_events', lambda *args, **kwargs: contact, raising=False)
    monkeypatch.setattr(a, 'render_video', lambda *args, **kwargs: None)
    monkeypatch.setattr(a, 'render_plots', lambda *args, **kwargs: None)
    captured = {}
    def save(video, out, wanted, count):
        captured.update(wanted)
        return {}
    monkeypatch.setattr(a, 'save_event_frames', save)
    metrics, _ = a.run_analysis('unused.mp4', str(tmp_path / 'jump'), scale=None if reuse_pose else 200,
                                events_sec=(15/60, 24/60), pose_data=pose_data if reuse_pose else None,
                                quality_window=(8,29) if reuse_pose else None)
    assert set(captured) == {'load', 'drive', 'takeoff', 'mid', 'landing'}
    assert captured['landing'] == 24
    assert captured['takeoff'] == 14
    assert metrics['landing_foot_ahead_of_com_px'] == 200
    if reuse_pose:
        assert metrics['pose_detection_pct'] < 90
        assert metrics['quality_detection_pct'] == 100
        assert metrics['tracking_reliable'] is True


def test_landing_photo_marks_horizontal_contact_com_and_pelvis_gap(tmp_path):
    import cv2
    import analyze_jump as a
    path = tmp_path / 'landing.jpg'
    cv2.imwrite(str(path), np.zeros((200, 300, 3), dtype=np.uint8))
    m = {'landing_contact_point_px': [250., 180.], 'landing_com_point_px': [120., 80.],
         'landing_hip_point_px': [100., 120.]}
    a.annotate_landing_reach(str(path), m, (300, 200))
    image = cv2.imread(str(path))
    assert image[79:82, 130:240, 2].mean() > 100
    assert image[119:122, 110:240, 0].mean() > 100


def test_propulsion_feedback_compares_its_frame_with_load():
    m, pose, vis, valid, com, angles, frames, contact = sample()
    angles[0][8], angles[0][11], angles[0][14] = 80, 110, 160
    out = module().build_phase_analysis(m, pose, vis, valid, com, *angles, frames, contact)
    text = '\n'.join(out['moments']['drive']['observations'])
    assert '30.0°' in text
    assert '80.0°' not in text  # must not compare load directly to the later takeoff pose
    assert '무릎도 펴지고' in out['moments']['drive']['comment']
    angles[0][11] = 70
    out = module().build_phase_analysis(m, pose, vis, valid, com, *angles, frames, contact)
    assert '무릎은 아직 펴지지' in out['moments']['drive']['comment']


def test_visible_shoe_candidate_can_show_pixel_reference_without_claiming_both_feet_contact():
    m, pose, vis, valid, com, angles, frames, contact = sample()
    m['fps'] = 30  # sufficient post-contact time is recorded in this case
    contact.update(reliable=False, observation_reliable=True, method='image_roi_estimate')
    out = module().build_phase_analysis(m, pose, vis, valid, com, *angles, frames, contact)
    assert out['landing_metrics']['landing_contact_reliable'] is False
    assert out['landing_metrics']['landing_foot_ahead_of_com_m'] is None
    item = out['moments']['landing']
    assert any(x['unit'] == 'px' and x['value'] == 200 for x in item['measurements'])
    assert any('후보' in x['label'] for x in item['measurements'])
    assert item['reliable'] is False
    assert item['comment'].startswith('착지 후보 장면에서는')
    assert out['landing_metrics']['landing_followthrough_reliable'] is True


def test_drive_selection_uses_the_same_side_as_its_measurement():
    m, pose, vis, valid, com, angles, frames, contact = sample()
    pose[:, 15, 0], pose[:, 15, 1] = 250, 600  # left arm never crosses; right does
    frames = module().select_moment_frames(pose, vis, valid, com, 15, 18, 24, 0, side='L')
    assert frames['drive'] is None


def test_hidden_toe_cannot_supply_an_ankle_angle():
    m, pose, vis, valid, com, angles, frames, contact = sample()
    vis[14, 31] = 0
    out = module().build_phase_analysis(m, pose, vis, valid, com, *angles, frames, contact)
    assert not any(x['label'] == '발목 포함각' for x in out['moments']['takeoff']['measurements'])


def test_hidden_elbow_cannot_supply_airborne_arm_angle_change():
    m, pose, vis, valid, com, angles, frames, contact = sample()
    vis[18, 13] = 0
    out = module().build_phase_analysis(m, pose, vis, valid, com, *angles, frames, contact)
    assert not any(x['label'] == '이륙 직전 대비 팔 각도 변화' for x in out['moments']['mid']['measurements'])


def test_slow_motion_preparation_window_keeps_the_real_loading_phase():
    assert module().preparation_start(300, 30, slowdown=8) == 132
    assert module().preparation_start(300, 30) == 279
