"""현재 저장 상태에서 만드는 개인 내 영상 추정 비교. 누적 통계는 변경하지 않는다."""
from metric_contract import finite


CORE = [('takeoff_speed_mps', '도약 속도', 'm/s', .15),
        ('takeoff_angle_deg', '도약 각도', '°', 3.),
        ('flight_time_s', '체공 시간', 's', 0.)]
POSE = [('load_trunk_deg', '하강 몸통 각도', 'load', '몸통과 지면의 각도', '°', 3.),
        ('load_knee_deg', '하강 무릎 각도', 'load', '무릎 포함각', '°', 5.),
        ('drive_knee_deg', '팔 스윙 때 무릎 각도', 'drive', '무릎 포함각', '°', 5.),
        ('takeoff_knee_deg', '이륙 직전 무릎 각도', 'takeoff', '무릎 포함각', '°', 5.),
        ('takeoff_trunk_deg', '이륙 직전 몸통 각도', 'takeoff', '몸통과 지면의 각도', '°', 3.),
        ('apex_knee_deg', '최고점 무릎 각도', 'mid', '무릎 포함각', '°', 5.),
        ('landing_knee_deg', '첫 착지 무릎 각도', 'landing', '무릎 포함각', '°', 5.),
        ('landing_hip_leg_pct', '착지 발–골반 / 다리 길이', 'landing', '다리 길이 대비 발–골반 간격', '%', 3.),
        ('landing_com_leg_pct', '착지 발–무게중심 / 다리 길이', 'landing', '다리 길이 대비 발–무게중심 간격', '%', 3.)]


def usable(a):
    run = a.get('selected_run') or {}
    return (a.get('state') == 'committed' and not a.get('deleted_at')
            and a.get('validity') == 'valid' and not a.get('analysis_stale')
            and a.get('analysis_included') in (True, 1) and run.get('status') == 'succeeded'
            and bool(run.get('result')) and bool(a.get('source_sha256')))


def compatible(a, b):
    if (not usable(a) or not usable(b) or a['id'] == b['id']
            or a['athlete_id'] != b['athlete_id'] or a['source_sha256'] == b['source_sha256']):
        return False
    x, y = a['selected_run']['result'], b['selected_run']['result']
    cx, cy = x.get('context', {}), y.get('context', {})
    # Missing provenance is not evidence of matching analysis methods.
    for key in ('model_sha256', 'metric_schema_version', 'pipeline_version'):
        if not cx.get(key) or cx[key] != cy.get(key):
            return False
    camera = str(cx.get('config', {}).get('camera_setup', '')).strip()
    return (bool(camera) and camera == str(cy.get('config', {}).get('camera_setup', '')).strip()
            and x.get('metrics', {}).get('primary_side') in ('L', 'R')
            and x['metrics']['primary_side'] == y.get('metrics', {}).get('primary_side')
            and cx.get('independent_scale') == cy.get('independent_scale')
            and a.get('measurement_context', {}).get('setup') == b.get('measurement_context', {}).get('setup'))


def features(data):
    m, obs = data.get('metrics', {}), data.get('observations', {})
    moments, raw = m.get('moments', {}), m.get('raw_measurements', {})
    values = {}
    for key, _, _, _ in CORE:
        o = obs.get(key, {})
        if o.get('displayable', o.get('eligible')) and finite(o.get('value')):
            values[key] = o['value']
    if m.get('tracking_reliable') is not True:
        return values, None
    for key, _, phase, label, _, _ in POSE:
        s = moments.get(phase, {})
        visible = s.get('reliable') or (phase == 'landing' and s.get('observed')
                                       and raw.get('landing_joints_visible') is True)
        if visible:
            value = next((v['value'] for v in s.get('measurements', []) if v['label'] == label), None)
            if finite(value):
                values[key] = value
    landing = moments.get('landing', {})
    if ((landing.get('reliable') or landing.get('observed'))
            and raw.get('landing_joints_visible') is True and raw.get('apex_joints_visible') is True
            and finite(raw.get('knee_angle_change_apex_to_contact_deg'))):
        values['knee_extension_deg'] = raw['knee_angle_change_apex_to_contact_deg']
    c = data.get('context', {}).get('config', {})
    factor = c.get('slowdown', 1) if c.get('time_mode') == 'slow' else 1
    fps = m.get('fps')
    step = 1 / (fps * factor) if (finite(fps) and fps > 0 and finite(factor) and factor >= 1
        and c.get('time_mode') in ('normal', 'slow') and 'flight_time_s' in values) else None
    if step is not None:
        takeoff = moments.get('takeoff', {})
        for key, phase in [('drive_lead_s', 'drive'), ('push_duration_s', 'load')]:
            s = moments.get(phase, {})
            if (s.get('reliable') and takeoff.get('reliable') and finite(s.get('frame'))
                    and finite(takeoff.get('frame')) and s['frame'] <= takeoff['frame']):
                values[key] = (takeoff['frame'] - s['frame']) * step
    return values, step


def describe(row):
    digits = 3 if row['unit'] == 's' else 2 if row['unit'] == 'm/s' else 1
    unit = '초' if row['unit'] == 's' else row['unit']
    return f"{row['label']} {row['before']:.{digits}f} → {row['after']:.{digits}f}{unit}"


def highlights(rows, record_difference):
    by_key = {r['key']: r for r in rows}
    result = []
    speed, angle, flight = [by_key.get(k) for k, *_ in CORE]
    takeoff = [r for r in (speed, angle, flight) if r]
    if takeoff:
        text = '. '.join(describe(r) for r in takeoff) + '.'
        if flight and flight['small'] and abs(flight['difference']) > 1e-6:
            text += ' 체공 시간은 한 프레임 안팎의 차이입니다.'
        if speed and not speed['small']:
            if record_difference is not None and record_difference < 0:
                action = ('하강에서 발이 떨어질 때까지 밀어내는 동작을 이어가며, 다음 시도의 속도와 기록이 함께 바뀌는지 확인하세요.'
                          if speed['difference'] < 0 else
                          '속도는 더 높게 추정됐지만 기록은 낮았습니다. 속도만 높이려 하지 말고 공중·착지 동작을 함께 확인하세요.')
            else:
                action = '이번 밀어내기 동작을 다시 재현해 속도와 기록이 함께 유지되는지 확인하세요.'
        else:
            action = '도약 수치만 크게 바꾸려 하지 말고 아래의 공중·착지 동작 차이부터 확인하세요.'
        result.append({'phase': 'takeoff', 'label': '도약', 'text': text, 'action': action})
    extension = by_key.get('knee_extension_deg')
    if extension:
        text = describe(extension) + '.'
        action = ('정점 이후 발을 앞으로 내보내며 다리를 펴는 타이밍을 연습하세요.'
                  if not extension['small'] and extension['difference'] < 0 and (record_difference is None or record_difference < 0) else
                  '공중에서 접은 다리를 착지 전에 펴는 흐름을 다음 시도에서도 확인하세요.')
        result.append({'phase': 'mid', 'label': '공중 → 착지', 'text': text, 'action': action})
    hip, com = by_key.get('landing_hip_leg_pct'), by_key.get('landing_com_leg_pct')
    reach = [r for r in (hip, com) if r]
    if reach:
        text = '. '.join(describe(r) for r in reach) + '.'
        if hip and hip['small']:
            text += ' 발–골반 간격의 차이는 작습니다.'
        action = ('발이 닿기 전에 앞으로 내보내는 타이밍을 확인하고, 접촉 뒤에는 몸을 앞으로 이어가세요.'
                  if hip and not hip['small'] and hip['difference'] < 0 else
                  '간격만 더 벌리려 하지 말고, 발을 내민 뒤 몸이 앞으로 이어지는지 확인하세요.')
        result.append({'phase': 'landing', 'label': '첫 착지', 'text': text, 'action': action})
    return result


def coaching_summary(rows, record_difference):
    """관측한 차이와 다음 연습을 연결한다. 기록 차이의 원인을 단정하지 않는다."""
    by_key = {r['key']: r for r in rows}
    lines = []
    lower = record_difference is not None and record_difference < 0
    extension = by_key.get('knee_extension_deg')
    if extension:
        before, after = extension['before'], extension['after']
        if not extension['small'] and extension['difference'] < 0 and (lower or record_difference is None):
            lines.append(f'최고점→첫 착지에서 무릎을 편 양이 {before:.1f}°에서 {after:.1f}°로 줄어, 착지 전에 발을 앞으로 보내며 다리를 펴는 타이밍을 먼저 연습하세요.')
        elif not extension['small'] and extension['difference'] > 0 and not lower:
            lines.append(f'최고점→첫 착지에서 무릎을 편 양이 {before:.1f}°에서 {after:.1f}°로 늘었으니, 이번에 발을 앞으로 내보낸 타이밍을 다음 시도에서도 유지해 보세요.')
        else:
            lines.append(f'최고점→첫 착지의 무릎 펴짐은 {before:.1f}°→{after:.1f}°로, 각도만 더 늘리기보다 다리를 펴는 타이밍과 착지 후 전진을 함께 연습하세요.')
    speed = by_key.get('takeoff_speed_mps')
    if speed:
        change = f"추정 도약 속도는 {speed['before']:.2f}→{speed['after']:.2f}m/s"
        if not speed['small'] and speed['difference'] < 0 and (lower or record_difference is None):
            angle = by_key.get('takeoff_angle_deg')
            intro = '도약 각도는 비슷하지만 ' if angle and angle['small'] else ''
            lines.append(intro + change + '로 낮아, 하강에서 이륙까지 끊기지 않게 밀어내는 흐름을 이어가세요.')
        elif not speed['small'] and speed['difference'] > 0 and lower:
            lines.append(change + '로 높지만 기록은 낮으니, 속도만 높이려 하지 말고 공중·착지 타이밍을 먼저 점검하세요.')
        elif not speed['small'] and speed['difference'] < 0 and record_difference is not None and record_difference > 0:
            lines.append(change + '로 낮지만 기록은 높으니, 속도만 바꾸려 하지 말고 이번 공중·착지 흐름을 재현해 보세요.')
        elif speed['small']:
            lines.append(change + '로 비슷하니, 도약을 크게 바꾸기보다 공중에서 다리를 내보내는 타이밍을 점검하세요.')
        elif speed['difference'] < 0:
            lines.append(change + '로 낮아졌지만 기록은 같으니, 속도 수치만 바꾸기보다 공중·착지 동작도 함께 확인하세요.')
        else:
            lines.append(change + '로 높아졌으니, 이번 밀어내기 흐름을 유지하며 다음에도 속도와 기록이 함께 유지되는지 확인하세요.')
    hip = by_key.get('landing_hip_leg_pct')
    if hip:
        change = f"첫 착지의 발–골반 간격은 다리 길이의 {hip['before']:.1f}→{hip['after']:.1f}%"
        if hip['small']:
            lines.append(change + '로 비슷하니, 간격을 더 벌리기보다 발을 내민 뒤 몸을 앞으로 이어가세요.')
        elif hip['difference'] < 0 and (lower or record_difference is None):
            lines.append(change + '로 짧아, 발이 닿기 전에 앞으로 내보내고 착지 후 몸을 앞으로 이어가는 동작을 연습하세요.')
        else:
            lines.append(change + '로, 발을 내미는 거리만 늘리기보다 접촉 후 몸이 앞으로 이어지도록 연습하세요.')
    # 관찰이 적을 때 다른 지표를 만들어 채우지 않는다.
    if not lines:
        flight = by_key.get('flight_time_s')
        if flight:
            lines.append(describe(flight) + '로, 체공 시간을 늘리려 하기보다 이륙부터 첫 접촉까지의 다리 동작을 확인하세요.')
    return lines[:3]


def single_coaching_summary(data):
    values, _ = features(data)
    lines = []
    load, takeoff = values.get('load_knee_deg'), values.get('takeoff_knee_deg')
    if load is not None and takeoff is not None:
        lines.append(f'하강 끝의 무릎 {load:.1f}°에서 이륙 직전 {takeoff:.1f}°로 이어지는 동작에서, 팔 스윙과 다리의 밀어내기가 끊기지 않게 연습하세요.')
    extension = values.get('knee_extension_deg')
    if extension is not None:
        change = f'{extension:.1f}° 편' if extension >= 0 else f'{abs(extension):.1f}° 더 접힌'
        lines.append(f'최고점보다 무릎이 {change} 상태에서 발이 닿으므로, 다리를 앞으로 내보내며 착지 전에 펴는 타이밍을 점검하세요.')
    hip = values.get('landing_hip_leg_pct')
    if hip is not None:
        lines.append(f'첫 착지의 발–골반 간격은 다리 길이의 {hip:.1f}%로, 발을 내미는 동작 다음에 몸을 앞으로 이어가는 것까지 연습하세요.')
    return lines


def build_personal_feedback(current, attempts):
    """자동/수동 비교 모두 동일 규칙. 실측값은 기준 선택·표시에만 사용한다."""
    if not usable(current):
        return None
    values, step = features(current['selected_run']['result'])
    candidates = [a for a in attempts if compatible(current, a)
                  and values.keys() & features(a['selected_run']['result'])[0].keys()]
    if not candidates:
        return None
    distance = current.get('measured_distance_cm')
    different = [a for a in candidates if finite(distance) and finite(a.get('measured_distance_cm'))
                 and a['measured_distance_cm'] != distance]
    reference = max(different or candidates, key=lambda a: (
        a.get('measured_distance_cm') if finite(a.get('measured_distance_cm')) else -1,
        a.get('performed_on', ''), a.get('attempt_no', 0)))
    before, old_step = features(reference['selected_run']['result'])
    specs = [(k, label, 'takeoff', unit, threshold) for k, label, unit, threshold in CORE]
    specs += [(k, label, phase, unit, threshold) for k, label, phase, _, unit, threshold in POSE]
    specs += [('knee_extension_deg', '최고점→착지 무릎 펴짐', 'mid', '°', 5.),
              ('drive_lead_s', '팔 스윙→마지막 지지 시간', 'drive', 's', 0.),
              ('push_duration_s', '하강 끝→마지막 지지 시간', 'load', 's', 0.)]
    rows = []
    for key, label, phase, unit, threshold in specs:
        if key not in values or key not in before:
            continue
        delta = values[key] - before[key]
        # Display-priority rules, not validated error bounds or significance tests.
        if unit == 's':
            threshold = max(step or 0, old_step or 0)
        rows.append({'key': key, 'label': label, 'phase': phase, 'unit': unit,
                     'before': before[key], 'after': values[key], 'difference': delta,
                     'small': abs(delta) <= threshold + 1e-6})
    if not rows:
        return None
    ref_distance = reference.get('measured_distance_cm')
    record_difference = distance - ref_distance if finite(distance) and finite(ref_distance) else None
    meta = lambda a: {k: a.get(k) for k in ('id', 'performed_on', 'attempt_no', 'measured_distance_cm')}
    return {'current': meta(current), 'reference': meta(reference), 'record_difference_cm': record_difference,
            'rows': rows, 'highlights': highlights(rows, record_difference),
            'summary': coaching_summary(rows, record_difference),
            'note': '영상 추정값의 비교입니다. 기록 차이의 원인은 다음 시도에서 확인합니다.'}
