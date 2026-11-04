"""누적 비교용 지표. 직접 입력한 점프 기록은 이 계산에 사용하지 않는다."""
import math

from storage import fingerprint


METRICS = {
    'flight_time_s': ('체공 시간', 's'),
    'takeoff_speed_mps': ('도약 속도', 'm/s'),
    'takeoff_angle_deg': ('도약 각도', '°'),
    'landing_foot_ahead_of_hip_m': ('착지 발–골반 간격', 'm'),
    'landing_foot_ahead_of_com_m': ('착지 발–무게중심 간격', 'm'),
    'landing_knee_angle_deg': ('착지 무릎 포함각', '°'),
    'knee_angle_change_apex_to_contact_deg': ('정점→착지 무릎각 변화', '°'),
    'load_trunk_angle_deg': ('하강 몸통 각도', '°'),
    'load_knee_angle_deg': ('하강 무릎 포함각', '°'),
}


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def calibration_scale(calibration, size):
    if not calibration:
        return None
    try:
        points = calibration['points']
        length = float(calibration['length_cm'])
        if len(points) != 2 or any(len(p) != 2 for p in points):
            raise ValueError
        if not finite(length) or length <= 0:
            raise ValueError
        for x, y in points:
            if not finite(x) or not finite(y) or not 0 <= x < size[0] or not 0 <= y < size[1]:
                raise ValueError
        pixels = math.dist(points[0], points[1])
        if pixels < 5:
            raise ValueError
        return pixels / (length / 100)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('기준 길이의 서로 다른 두 점과 실제 cm를 지정하세요.') from None


def metric_observations(metrics, context):
    raw = metrics.get('raw_measurements', {})
    config = context.get('config', {})
    contact = metrics.get('landing_event', {})
    tracking = metrics.get('tracking_reliable') is True
    takeoff = contact.get('takeoff_reliable') is True
    landing = contact.get('reliable') is True
    uniform = context.get('timebase', {}).get('uniform') is True
    factor = config.get('slowdown', 1) if config.get('time_mode') == 'slow' else 1
    time_ok = uniform and config.get('time_mode') in ('normal', 'slow') and finite(factor) and 1 <= factor <= 32
    if config.get('time_mode') == 'normal' and metrics.get('slowmo_detected'):
        time_ok = False
    if not time_ok:
        factor = 1
    independent = context.get('independent_scale') is True
    trajectory = finite(raw.get('trajectory_r2')) and raw['trajectory_r2'] >= .95
    camera = bool(str(config.get('camera_setup', '')).strip())
    base = [context.get('metric_schema_version', 'personal-v1'), context.get('model_sha256'),
            metrics.get('primary_side'), config.get('camera_setup'),
            'known-length' if independent else 'no-scale']
    observations = {}
    frames = {k: metrics.get(k) for k in ('pre_takeoff_frame','takeoff_frame','apex_frame','landing_frame')}

    def add(key, value, conditions, coords, display_conditions=None):
        reasons = [reason for ok, reason in conditions if not ok]
        if not finite(value):
            reasons.append('값 없음')
            value = None
        label, unit = METRICS[key]
        display_reasons = [reason for ok, reason in (conditions if display_conditions is None else display_conditions) if not ok]
        if value is None:
            display_reasons.append('값 없음')
        observations[key] = {
            'key': key, 'label': label, 'unit': unit, 'value': value,
            'eligible': not reasons, 'reason_codes': reasons, 'definition_version': '1',
            'coordinate_system': coords, 'source_frames': frames,
            'comparison_group': fingerprint(base + [key]),
            'display_reason_codes': display_reasons,
            # A visible-side video estimate is useful in its own report even
            # when both feet / capture metadata are insufficient for comparison.
            'displayable': finite(value) and all(ok for ok, _ in
                (conditions if display_conditions is None else display_conditions)),
        }

    common = [(tracking, '자세 추적 부족'), (camera, '촬영 조건 미입력')]
    both = common + [(takeoff, '이륙 경계 미확인'), (landing, '첫 접촉 미확인')]
    flight = both + [(raw.get('flight_com_visible') is True, '비행 중 무게중심 입력점 추적 부족')]
    observed = [(tracking, '자세 추적 부족'),
                (takeoff or contact.get('takeoff_observed') is True, '이륙 경계 미관측'),
                (landing or contact.get('landing_observed') is True, '접촉 경계 미관측')]
    primary = raw.get('flight_primary_visible') is True or raw.get('flight_com_visible') is True
    estimated_flight = observed + [(primary, '비행 중 관절 추적 부족'), (trajectory, '궤적 적합 부족')]
    video_flight = raw.get('flight_time_video_s')
    duration_ok = finite(video_flight) and .12 <= video_flight / factor <= 1.5
    both += [(duration_ok, '체공 시간 범위 오류')]
    flight += [(duration_ok, '체공 시간 범위 오류')]
    estimated_flight += [(duration_ok, '체공 시간 범위 오류')]
    add('flight_time_s', raw.get('flight_time_video_s', 0) / factor if finite(raw.get('flight_time_video_s')) else None,
        both + [(time_ok, '실제 시간 미확인')], 'time',
        observed + [(time_ok, '실제 시간 미확인'), (duration_ok, '체공 시간 범위 오류')])
    add('takeoff_speed_mps', raw.get('takeoff_speed_video_mps', 0) * factor if finite(raw.get('takeoff_speed_video_mps')) else None,
        flight + [(time_ok, '실제 시간 미확인'), (independent, '기준 길이 없음'), (trajectory, '궤적 적합 부족')], 'image-plane',
        estimated_flight + [(time_ok, '실제 시간 미확인'),
            (independent or metrics.get('absolute_metrics_reliable') is True, '길이 환산 불가')])
    add('takeoff_angle_deg', raw.get('takeoff_angle_deg'),
        flight + [(uniform, '프레임 시간 불규칙'), (trajectory, '궤적 적합 부족')], 'image-plane',
        estimated_flight + [(uniform, '프레임 시간 불규칙')])
    for name in ('hip', 'com'):
        conditions = common + [(landing, '첫 접촉 미확인'), (independent, '기준 길이 없음')]
        if name == 'com':
            conditions += [(raw.get('com_visible') is True, '몸통·팔다리 추적 부족')]
        else:
            conditions += [(raw.get('landing_joints_visible') is True, '착지 관절 추적 부족')]
        key = f'landing_foot_ahead_of_{name}_m'
        add(key, raw.get(key), conditions, 'image-plane')
    for key in ('landing_knee_angle_deg', 'knee_angle_change_apex_to_contact_deg'):
        conditions = common + [(landing, '첫 접촉 미확인'), (raw.get('landing_joints_visible') is True, '착지 관절 추적 부족')]
        if key.startswith('knee_angle_change'):
            conditions += [(raw.get('apex_joints_visible') is True, '공중 관절 추적 부족')]
        add(key, raw.get(key), conditions, 'model-world')
    for key in ('load_trunk_angle_deg', 'load_knee_angle_deg'):
        add(key, raw.get(key), common + [(raw.get('load_joints_visible') is True, '하강 관절 추적 부족')],
            'image-plane' if 'trunk' in key else 'model-world')
    return observations
