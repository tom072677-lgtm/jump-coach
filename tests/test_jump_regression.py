"""실제 측면 점프: 준비/비행 뒤 보행·측정기 확대가 있는 24fps 영상."""
from pathlib import Path

import numpy as np
import pytest

import analyze_jump as A


def pose():
    with np.load(Path(__file__).parent / 'fixtures' / 'side_jump_pose.npz') as data:
        return (float(data['fps']), tuple(data['size']), data['norm'],
                data['world'], data['valid'])


def test_real_jump_survives_late_airborne_threshold_and_camera_closeup():
    result, _ = A.detect_from_pose(*pose())
    assert result is not None
    # Visual check: first airborne f123; first shoe contact f135 (±1 frame).
    # The coarse flight window is deliberately inside those boundaries.
    assert 123 <= round(result[3] * 24) <= 125
    assert 133 <= round(result[4] * 24) <= 135
    assert 1 <= result[1] - result[0] <= 2


def test_walking_and_closeup_alone_are_not_a_jump():
    fps, size, norm, world, valid = pose()
    result, _ = A.detect_from_pose(fps, size, norm[145:], world[145:], valid[145:])
    assert result is None


def test_no_jump_does_not_become_whole_video_flight(tmp_path):
    fps, size, norm, world, valid = pose()
    with pytest.raises(ValueError, match='점프 구간'):
        A.run_analysis('unused.mp4', str(tmp_path/'none'),
                       pose_data=(fps,size,norm[:100],world[:100],valid[:100]))


def test_forward_arm_swing_is_found_before_last_support():
    fps, _, norm, _, valid = pose()
    frames = A.select_moment_frames(norm, norm[:,:,2], valid, A.center_of_mass(norm),
                                    123,127,135,A.preparation_start(123,fps),side='R')
    assert frames['load'] in (116,117)
    assert frames['drive'] in (120,121)
    assert frames['takeoff'] == 122


def test_real_pose_pipeline_restores_five_phases_and_video_estimates(tmp_path, monkeypatch):
    from metric_contract import metric_observations

    monkeypatch.setattr(A, 'render_video', lambda *args, **kwargs: None)
    monkeypatch.setattr(A, 'render_plots', lambda *args, **kwargs: None)
    monkeypatch.setattr(A, 'save_event_frames', lambda *args, **kwargs: {})
    m, _ = A.run_analysis('unused.mp4', str(tmp_path/'real'), pose_data=pose())
    assert [m['moments'][k]['frame'] for k in ('load','drive','takeoff','mid','landing')] == [117,121,122,127,135]
    assert all(s['available'] for s in m['moments'].values())
    assert m['takeoff_frame'] == 123 and m['landing_frame'] == 135
    assert m['raw_measurements']['flight_time_video_s'] == pytest.approx(.5)
    assert m['raw_measurements']['takeoff_angle_deg'] == pytest.approx(25.5, abs=1)
    observations = metric_observations(m, {'timebase':{'uniform':True},
                                          'config':{'time_mode':'normal','camera_setup':'측면'}})
    assert all(observations[k]['displayable'] for k in ('flight_time_s','takeoff_angle_deg','takeoff_speed_mps'))
    assert not observations['takeoff_speed_mps']['eligible']
    assert m['moments']['landing']['observed']


@pytest.mark.parametrize('slowdown', [4, 8])
def test_known_slow_motion_selects_same_jump_not_later_closeup(slowdown):
    import inspect

    fps, size, norm, world, valid = pose()
    expanded = (fps, size, np.repeat(norm,slowdown,axis=0),
                np.repeat(world,slowdown,axis=0), np.repeat(valid,slowdown))
    assert 'slowdown' in inspect.signature(A.detect_from_pose).parameters, '검출에도 명시한 슬로모션 배율이 필요합니다'
    result, _ = A.detect_from_pose(*expanded, slowdown=slowdown)
    assert result is not None
    assert 123 <= result[3]*fps/slowdown <= 125
    assert 133 <= result[4]*fps/slowdown <= 135
    assert 1 <= (result[1]-result[0])/slowdown <= 2
    # Same physical walking-only footage cannot become a flight after expansion.
    start = 145*slowdown
    result, _ = A.detect_from_pose(fps,size,expanded[2][start:],expanded[3][start:],expanded[4][start:],slowdown=slowdown)
    assert result is None


def test_known_slow_motion_preserves_source_frames_and_preparation(tmp_path, monkeypatch):
    import inspect

    fps,size,norm,world,valid = pose()
    slowdown = 4
    expanded = (fps,size,np.repeat(norm,slowdown,axis=0),
                np.repeat(world,slowdown,axis=0),np.repeat(valid,slowdown))
    monkeypatch.setattr(A,'render_video',lambda *args,**kw:None)
    monkeypatch.setattr(A,'render_plots',lambda *args,**kw:None)
    monkeypatch.setattr(A,'save_event_frames',lambda *args,**kw:{})
    assert 'slowdown' in inspect.signature(A.run_analysis).parameters, '접촉 경계와 준비 구간에도 같은 배율이 필요합니다'
    metrics,_ = A.run_analysis('unused.mp4',str(tmp_path/'slow'),pose_data=expanded,slowdown=slowdown)
    assert metrics['fps'] == fps
    assert metrics['frames'] == len(norm)*slowdown
    assert abs(metrics['takeoff_frame']/slowdown-123) <= 1
    assert abs(metrics['landing_frame']/slowdown-135) <= 1
    assert metrics['moments']['load']['available']
    assert metrics['moments']['drive']['available']
    assert metrics['raw_measurements']['flight_time_video_s']/slowdown == pytest.approx(.5,abs=1/fps)
