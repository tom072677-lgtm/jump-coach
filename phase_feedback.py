"""Frame-specific observations; no composite score or universal ideal posture."""
import math

import numpy as np


LABELS = {'load': '하강 말기', 'drive': '팔 스윙 · 추진',
          'takeoff': '이륙 직전', 'mid': '공중', 'landing': '최초 착지'}


def finite(value):
    return isinstance(value, (int, float, np.number)) and not isinstance(value, (bool, np.bool_)) and np.isfinite(value)


def preparation_start(takeoff, fps, slowdown=1):
    return max(0, int(takeoff) - int(round(.7 * fps * slowdown)))


def select_moment_frames(norm, raw_vis, valid, com, takeoff, apex, landing, prep0, side=None):
    """Load=lowest CoM; drive=forward wrists approaching shoulder height before last support."""
    end = max(0, min(len(norm) - 1, int(takeoff) - 1))
    frames = {'load': None, 'drive': None, 'takeoff': end,
              'mid': int(apex), 'landing': int(landing)}
    start = max(0, int(prep0))
    if end - start < 3:
        return frames
    candidates = [i for i in range(start, end - 1)
                  if valid[i] and np.isfinite(com[i]).all()
                  and np.mean(raw_vis[i, [11, 12, 23, 24]]) >= .5]
    if not candidates:
        return frames
    load = max(candidates, key=lambda i: com[i, 1])
    frames['load'] = load
    direction = np.sign(com[min(landing, len(com) - 1), 0] - com[end, 0])
    if not direction:
        return frames
    if side is None:
        left = np.mean(raw_vis[:, [11, 13, 23, 25, 27]])
        right = np.mean(raw_vis[:, [12, 14, 24, 26, 28]])
        side = 'L' if left >= right else 'R'
    for i in range(load + 1, end):
        sides = [(11, 15)] if side == 'L' else [(12, 16)]
        for shoulder, wrist in sides:
            if not valid[i] or min(raw_vis[i, [shoulder, wrist]]) < .6:
                continue
            v = norm[i, wrist, :2] - norm[i, shoulder, :2]
            pelvis = 23 if shoulder == 11 else 24
            shoulder_band = .2 * np.linalg.norm(norm[i, shoulder, :2] - norm[i, pelvis, :2])
            # Reaching shoulder height exactly may happen only at last support.
            # The forward swing just below it is still the propulsion moment.
            if np.isfinite(v).all() and v[0] * direction > 0 and v[1] <= shoulder_band:
                frames['drive'] = i
                return frames
    # A missing arm crossing is reported as unavailable, rather than inventing a force peak.
    return frames


def build_phase_analysis(m, norm, raw_vis, valid, com, knee, hip, ankle, arm, frames, contact, prep_start=None):
    fps = float(m.get('fps') or 30)
    n = len(norm)
    tracking = m.get('tracking_reliable', True) is True
    scale = m.get('scale_px_per_m')
    absolute = m.get('absolute_metrics_reliable') is True and finite(scale) and scale > 0
    direction = m.get('jump_direction', 0)
    if direction not in (-1, 1):
        a, b = frames.get('takeoff'), frames.get('landing')
        direction = np.sign(com[b, 0] - com[a, 0]) if a is not None and b is not None else 0
    left = m.get('primary_side') != 'R'
    sho, elbow, wrist, pelvis, kn, an = (11, 13, 15, 23, 25, 27) if left else (12, 14, 16, 24, 26, 28)
    moments = {}
    geometry = {key: None for key in ('landing_foot_ahead_of_com_px', 'landing_foot_ahead_of_hip_px',
                'landing_foot_ahead_of_com_m', 'landing_foot_ahead_of_hip_m',
                'landing_foot_ahead_of_com_leg_pct', 'landing_foot_ahead_of_hip_leg_pct',
                'landing_followthrough_com_drift_px', 'landing_followthrough_com_drift_m')}
    geometry.update(landing_contact_reliable=False, landing_followthrough_reliable=False,
                    landing_contact_point_px=contact.get('contact_point_px'),
                    landing_hip_point_px=None, landing_com_point_px=None)

    def point_ok(i, ids):
        return bool(i is not None and 0 <= i < n and valid[i]
                    and np.isfinite(norm[i, ids, :2]).all()
                    and np.all(raw_vis[i, ids] >= .5))

    def measure(item, label, value, unit, digits=1):
        if finite(value):
            item['measurements'].append({'label': label, 'value': round(float(value), digits), 'unit': unit})

    # Same visible leg, fixed preparation window. A length ratio cancels image
    # magnification, not perspective or pose error; do not use it as cm gained.
    support = frames.get('takeoff')
    lengths = []
    if support is not None:
        start = max(0, int(prep_start)) if prep_start is not None else preparation_start(support, fps, m.get('slowmo_factor') or 1)
        for i in range(start, support + 1):
            if point_ok(i, [pelvis, kn, an]):
                lengths.append(np.linalg.norm(norm[i, pelvis, :2] - norm[i, kn, :2]) +
                               np.linalg.norm(norm[i, kn, :2] - norm[i, an, :2]))
    leg_length = float(np.median(lengths)) if len(lengths) >= 5 else None
    if leg_length is not None and (leg_length <= 1 or np.std(lengths) / leg_length > .15):
        leg_length = None

    for key, frame in frames.items():
        phase = 'takeoff' if key in ('load', 'drive', 'takeoff') else key
        item = {'label': LABELS[key], 'phase': phase, 'frame': frame,
                'time_s': round(frame / fps, 3) if frame is not None else None,
                'available': frame is not None, 'reliable': False,
                'measurements': [], 'observations': [], 'cues': [], 'notes': [],
                'comment': ''}
        moments[key] = item
        if frame is None:
            item['comment'] = '이 순간을 찾지 못했습니다. 준비 동작과 손발이 모두 보이게 다시 촬영하세요.'
            item['notes'].append('해당 준비 동작이나 팔 통과 시점을 찾지 못했습니다. 준비 구간과 손까지 보이게 촬영해 주세요.')
            continue
        needed = [sho, pelvis, kn, an] + ([wrist] if key == 'drive' else [])
        if not tracking or not point_ok(frame, needed):
            item['comment'] = '동작을 읽지 못했습니다. 옆에서 전신이 보이게 다시 촬영하세요.'
            item['notes'].append('이 프레임의 몸통·다리 추적이 불충분해 자세 수치와 교정 판단을 보류합니다.')
            continue
        item['reliable'] = True
        trunk = norm[frame, sho, :2] - norm[frame, pelvis, :2]
        incline = math.degrees(math.atan2(-trunk[1], abs(trunk[0])))
        measure(item, '몸통과 지면의 각도', incline, '°')
        measure(item, '무릎 포함각', knee[frame], '°')
        measure(item, '몸통–허벅지 포함각', hip[frame], '°')
        item['notes'].append('관절각은 추정값입니다. 몸통–허벅지 포함각은 해부학적 고관절 굽힘각과 다릅니다.')
        if key == 'load':
            item['comment'] = f'하강 끝에서 몸통은 지면과 {incline:.1f}°입니다. '
            if finite(knee[frame]):
                item['comment'] += f'무릎은 {knee[frame]:.1f}°까지 접었습니다. '
            item['comment'] += '이 깊이에서 몸통과 다리를 함께 펴며 도약으로 이어가세요.'
            item['observations'].append(f'도약 전 추정 무게중심이 가장 낮은 프레임입니다. 몸통은 지면과 {incline:.1f}°를 이룹니다.')
            if incline < 20:
                item['observations'].append('상체가 지면에 가까운 방향으로 숙여져 있습니다. 이 관찰만으로 과도한 숙임이라고 확정하지 않습니다.')
            item['cues'].append('하강 깊이와 함께, 이 시점 뒤에 몸통·골반이 펴지는 흐름과 팔이 앞으로 나오는 순서를 확인하세요.')
            item['notes'].append('선정 기준은 준비 구간의 최저 무게중심이며, 최대 지면반력 시점은 측정하지 않았습니다.')
        elif key == 'drive':
            item['comment'] = '팔이 어깨 높이 부근까지 올라왔습니다. 팔 스윙과 다리 펴기를 함께 맞춰보세요.'
            measure(item, '손목–어깨 높이차(손목이 위면 +)', norm[frame, sho, 1] - norm[frame, wrist, 1], 'px')
            dt = (frames['takeoff'] - frame) / fps
            measure(item, '마지막 지지까지 영상 시간', dt, 's', 3)
            item['observations'].append('손목이 어깨 앞·높이 부근으로 올라온 추진 구간입니다. 팔 위치와 다리의 펴짐을 같은 프레임에서 봅니다.')
            load = frames.get('load')
            if load is not None and point_ok(load, [sho, pelvis, kn, an]):
                delta = knee[frame] - knee[load]
                if finite(delta):
                    item['comment'] = (f'팔이 앞으로 올라오며 무릎도 펴지고 있습니다. 하강 때보다 {delta:.1f}° 펴서 {knee[frame]:.1f}°입니다. '
                                       if delta > 0 else
                                       '팔은 올라왔지만 무릎은 아직 펴지지 않았습니다. 팔 스윙과 다리 펴기를 함께 맞춰보세요.')
                    if delta > 0:
                        item['comment'] += f'마지막 지지까지 {frames["takeoff"] - frame}프레임 남았습니다. 팔과 다리의 흐름을 이어가세요.'
                    item['observations'].append(f'하강 말기 사진보다 무릎 포함각이 {delta:+.1f}° 변했습니다. 양수는 펴지는 방향입니다.')
            item['cues'].append('팔이 앞으로 나오는 동안 몸통·골반·무릎의 움직임이 어떻게 이어지는지 이륙 직전 사진과 비교하세요.')
            item['notes'].append('팔 위치로 고른 시점입니다. 힘·파워가 가장 큰 순간이라고 해석하지 않습니다.')
        elif key == 'takeoff':
            item['comment'] = '발이 떨어지기 직전입니다. 발이 땅에 닿아 있을 때 골반과 무릎을 펴며 끝까지 밀어주세요.'
            load = frames.get('load')
            if load is not None and point_ok(load, [pelvis, kn, an]) and finite(knee[frame] - knee[load]):
                item['comment'] = (f'하강 때보다 무릎을 {knee[frame] - knee[load]:.1f}° 펴서 {knee[frame]:.1f}°로 이륙합니다. 발이 땅에 닿아 있는 동안 밀어내는 흐름을 이어가세요.'
                                   if knee[frame] > knee[load] else
                                   '이륙 직전에도 하강 때보다 무릎이 펴지지 않았습니다. 발을 떼기 전에 다리를 펴며 바닥을 밀어주세요.')
            toe = 31 if left else 32
            if point_ok(frame, [kn, an, toe]):
                measure(item, '발목 포함각', ankle[frame], '°')
            else:
                item['notes'].append('발끝 추적이 불충분하여 발목 포함각은 보류했습니다.')
            item['observations'].append('첫 공중 프레임 바로 앞의 마지막 지지 후보입니다. 무릎·몸통–허벅지 포함각을 이 프레임에서 읽었습니다.')
            if contact.get('takeoff_note'):
                item['notes'].append(contact['takeoff_note'])
            if not contact.get('takeoff_reliable', False):
                item['notes'].append('발이 떠나는 경계는 자동 추정입니다. 마지막 지지 프레임은 앞뒤 영상에서 확인하세요.')
            if absolute and m.get('events_reliable') is True:
                measure(item, '추정 이륙 속도', m.get('takeoff_speed_mps'), 'm/s', 2)
                measure(item, '추정 도약각', m.get('takeoff_angle_deg'), '°')
            item['cues'].append('발이 떨어지기 전에 다리가 펴지는 흐름과 팔의 위치를 확인하세요. 한 장의 각도로 추진력이나 신전 타이밍을 확정하지 않습니다.')
            item['notes'].append('발목 포함각은 발목의 해부학적 각도나 근육 힘을 직접 나타내지 않습니다.')
        elif key == 'mid':
            item['comment'] = '몸이 가장 높이 올라온 순간입니다. 착지 전에 발을 앞으로 보내고 다리를 뻗어주세요.'
            item['observations'].append('추정 무게중심 최고점 부근입니다. 몸통과 다리의 자세를 확인할 수 있습니다.')
            load = frames.get('takeoff')
            if load is not None and point_ok(load, [pelvis, kn, an]) and finite(knee[frame] - knee[load]):
                item['comment'] = (f'최고점의 무릎은 {knee[frame]:.1f}°로, 이륙 직전보다 {knee[load] - knee[frame]:.1f}° 접었습니다. '
                                   if knee[frame] < knee[load] else
                                   '이륙 직전보다 무릎을 더 접지는 않았습니다. 착지에 맞춰 발을 앞으로 보내세요.')
                land = frames.get('landing')
                if (point_ok(land, [pelvis, kn, an]) and finite(knee[land] - knee[frame])
                        and (contact.get('reliable') or contact.get('landing_observed'))):
                    delta = knee[land] - knee[frame]
                    item['comment'] += f'여기서 착지까지 {abs(delta):.1f}° 더 ' + ('폈습니다.' if delta >= 0 else '접었습니다.')
            if load is not None and point_ok(frame, [pelvis, sho, elbow]) and point_ok(load, [pelvis, sho, elbow]):
                measure(item, '이륙 직전 대비 팔 각도 변화', arm[frame] - arm[load], '°')
            item['cues'].append('공중 사진과 최초 착지 사진을 비교해 발을 앞으로 내보내는 시점, 무릎을 펴는 변화, 몸통 위치를 확인하세요.')
            item['notes'].append('공중의 팔·다리 움직임은 착지 자세를 바꿀 수 있지만, 바닥을 다시 밀어 전신의 수평 추진력을 얻는 것으로 설명하지 않습니다.')
        else:
            item['frame_range'] = contact.get('landing_frame_range')
            if item['frame_range']:
                lo, hi = item['frame_range']
                item['notes'].append(f'보이는 발의 영상 접촉 후보 구간은 {lo}–{hi}이며, 사진은 {frame}프레임입니다.')
            item['notes'].append(contact.get('note') or '최초 신발 접촉은 영상 해상도와 가림에 따라 불확실합니다.')
            item['notes'].append('노란 점·세로선은 접촉점 추정, 붉은 선은 무게중심 간격, 푸른 선은 골반 간격입니다. 골반 관절점을 엉덩이 위치의 대용으로 씁니다.')
            cp = contact.get('contact_point_px')
            point_valid = bool(cp is not None and np.isfinite(cp).all()
                               and direction in (-1, 1) and np.isfinite(com[frame]).all())
            contact_ok = bool(contact.get('reliable') and point_valid)
            candidate_ok = bool((contact.get('observation_reliable') or contact.get('landing_observed')) and point_valid)
            item['observed'] = contact_ok or candidate_ok
            item['reliable'] = contact_ok
            if not (contact_ok or candidate_ok):
                item['comment'] = '발이 닿는 순간을 구분하기 어렵습니다. 신발과 바닥이 함께 보이게 다시 촬영하세요.'
                item['measurements'] = []
                item['notes'].append('최초 접촉 위치가 불명확하여 발–무게중심·골반 간격과 착지 자세 평가는 보류합니다.')
                continue
            geometry['landing_contact_reliable'] = contact_ok
            geometry['landing_com_point_px'] = [float(v) for v in com[frame]]
            geometry['landing_hip_point_px'] = [float(v) for v in norm[frame, pelvis, :2]]
            for name, x in [('com', com[frame, 0]), ('hip', norm[frame, pelvis, 0])]:
                gap = float((cp[0] - x) * direction)
                if contact_ok:
                    geometry[f'landing_foot_ahead_of_{name}_px'] = round(gap, 2)
                    geometry[f'landing_foot_ahead_of_{name}_m'] = round(gap / scale, 3) if absolute else None
                label = '발–추정 무게중심 수평 간격' if name == 'com' else '발–골반 수평 간격'
                if not contact_ok:
                    label = '접촉 후보 ' + label
                use_meters = absolute and contact_ok
                measure(item, label, gap / scale * 100 if use_meters else gap, 'cm' if use_meters else 'px')
                com_points = ([7,11,13,15,23,25,27,29,31] if left else [8,12,14,16,24,26,28,30,32])
                if leg_length is not None and (name == 'hip' or point_ok(frame, com_points)):
                    ratio = gap / leg_length * 100
                    geometry[f'landing_foot_ahead_of_{name}_leg_pct'] = round(ratio, 1)
                    measure(item, '다리 길이 대비 발–' + ('무게중심' if name == 'com' else '골반') + ' 간격', ratio, '%')
            if not contact_ok:
                item['notes'].append('보이는 발의 접촉 후보를 기준으로 한 참고 간격입니다. 반대발이나 전체 최초 접촉이 미확인이므로 m/cm 수치와 확정 평가는 보류합니다.')
            reach = float((cp[0] - com[frame, 0]) * direction)
            prefix = '' if contact_ok else ('착지 장면에서 ' if contact.get('landing_observed') else '착지 후보 장면에서는 ')
            item['comment'] = prefix + ('발이 무게중심보다 앞에 있습니다. 발을 뻗어 닿은 뒤에는 몸통을 앞으로 이어가세요.'
                                        if reach > 0 else
                                        '발이 무게중심보다 앞서지 않았습니다. 닿기 전에 발을 앞으로 더 내보내세요.')
            if leg_length is not None:
                hip_gap = geometry['landing_foot_ahead_of_hip_leg_pct']
                com_gap = geometry['landing_foot_ahead_of_com_leg_pct']
                item['comment'] = prefix + f'발–골반 간격은 다리 길이의 {hip_gap:.1f}%입니다. '
                if com_gap is not None:
                    item['comment'] += f'발–무게중심 간격은 {com_gap:.1f}%입니다. '
                item['comment'] += ('발을 내민 뒤에는 몸을 앞으로 이어가세요.' if com_gap is None or reach > 0 else
                                    '발이 몸보다 앞서지 않았습니다. 닿기 전에 발을 앞으로 내보내세요.')
            item['observations'].append('접촉 발이 추정 무게중심보다 ' + ('앞에 있습니다.' if reach > 0 else '뒤에 있거나 비슷한 위치에 있습니다.'))
            item['observations'].append('간격은 최초 접촉 프레임의 수평 투영값이며, 발이 진행방향으로 앞설 때 양수입니다.')
            item['cues'].append('발을 앞으로 내보내고 무릎을 펴는 동작이 접촉 전에 이루어지는지 확인하세요. 이륙·공중 자세와 연결해서 비교하세요.')
            item['notes'].append('간격이 클수록 무조건 좋은 착지라는 뜻은 아닙니다. 이 값은 실제 추가 기록이나 뒤로 손·엉덩이를 짚지 않았다는 증거가 아닙니다.')
            mid = frames.get('mid')
            if mid is not None and point_ok(mid, [kn, an, pelvis]) and finite(knee[frame] - knee[mid]):
                item['observations'].append(f'공중 사진에서 이 접촉 후보까지 무릎 포함각이 {knee[frame] - knee[mid]:+.1f}° 변했습니다. 양수는 다리를 펴는 방향입니다.')
            if not absolute or not contact_ok:
                item['notes'].append('길이 환산 기준이 미검증이어서 화면상의 px만 표시합니다. 다른 촬영 영상끼리 px 값을 직접 비교하지 마세요.')
            slowdown = m.get('slowmo_factor') if m.get('slowmo_detected') else 1
            slowdown = slowdown if finite(slowdown) and slowdown > 0 else 1
            post = min(n - 1, frame + max(1, int(round(.3 * fps * slowdown))))
            if post - frame >= max(2, int(round(.15 * fps * slowdown))) and point_ok(post, [sho, pelvis]):
                drift = float((com[post, 0] - com[frame, 0]) * direction)
                geometry['landing_followthrough_reliable'] = True
                geometry['landing_followthrough_com_drift_px'] = round(drift, 2)
                geometry['landing_followthrough_com_drift_m'] = round(drift / scale, 3) if absolute and contact_ok else None
                item['observations'].append('접촉 이후 별도 관찰 구간에서 추정 무게중심은 ' + ('앞으로 이동했습니다.' if drift > 0 else '뒤로 이동하거나 거의 이동하지 않았습니다.'))
                item['notes'].append('접촉 이후 이동도 첫 접촉 간격과 별개입니다. 손·엉덩이의 후속 접촉과 미끄러짐은 원영상에서 확인하세요.')
            else:
                item['notes'].append('착지 뒤 영상이나 추적이 부족하여 후속 움직임은 평가하지 못했습니다.')

    phases = {}
    for phase, label in [('takeoff', '도약'), ('mid', '뛰는 중간'), ('landing', '착지')]:
        entries = [v for v in moments.values() if v['phase'] == phase]
        phases[phase] = {'label': label,
                         **{field: list(dict.fromkeys(x for v in entries for x in v[field]))
                            for field in ('observations', 'cues', 'notes')}}
    return {'moments': moments, 'phase_feedback': phases, 'landing_metrics': geometry}
