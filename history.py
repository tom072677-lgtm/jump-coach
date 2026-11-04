"""개인 내 기술통계. 원인·근력 진단이나 점수는 생성하지 않는다."""
from collections import defaultdict
from statistics import median

from metric_contract import METRICS


def date_summary(points):
    grouped = defaultdict(list)
    for point in points:
        grouped[(point['group'], point['date'])].append(point)
    return [{'group': group, 'date': day, 'median': median(p['value'] for p in rows),
             'best': max(p['value'] for p in rows), 'count': len(rows),
             'attempt_ids': [p['attempt_id'] for p in rows]}
            for (group, day), rows in sorted(grouped.items())]


def describe_history(summaries, training_dates, label, unit):
    messages = []
    for group, days in training_dates.items():
        window = sorted(set(days))[-6:]
        by_date = {p['date']: p for p in summaries if p['group'] == group}
        if len(window) != 6 or any(day not in by_date or by_date[day]['count'] < 3 for day in window):
            continue
        values = [by_date[d]['median'] for d in window]
        before, after = median(values[:3]), median(values[3:])
        messages.append({'text': f'{label}: 이전 3일 {before:.2f} → 최근 3일 {after:.2f}{unit}',
                         'group': group, 'dates': window, 'before': before, 'after': after,
                         'difference': after - before,
                         'attempt_ids': [i for d in window for i in by_date[d]['attempt_ids']]})
    return messages


def build_history(store, athlete_id):
    attempts = store.list_attempts(athlete_id)
    records, record_days = [], defaultdict(set)
    metric_points = defaultdict(list)
    metric_days = defaultdict(lambda: defaultdict(set))
    excluded = defaultdict(int)
    training_dates = {a['performed_on'] for a in attempts if a['state'] == 'committed'}
    for a in attempts:
        if a['state'] != 'committed':
            continue
        group = a['measurement_context'].get('setup') or '미확인'
        record_days[group].add(a['performed_on'])
        if a['validity'] == 'valid' and a['measured_distance_cm'] is not None:
            records.append({'date': a['performed_on'], 'group': group, 'value': a['measured_distance_cm'],
                            'attempt_id': a['id'], 'attempt_no': a['attempt_no'], 'created_at': a['created_at']})
        selected = a['selected_run']
        if not selected or not selected.get('result'):
            continue
        observations = selected['result'].get('observations', {})
        for key, m in observations.items():
            metric_days[key][m['comparison_group']].add(a['performed_on'])
            usable = (a['validity'] == 'valid' and not a['analysis_stale'] and a['clip_confirmed']
                      and a['analysis_included'] and m.get('eligible') is True)
            if usable:
                metric_points[key].append({'date': a['performed_on'], 'group': m['comparison_group'],
                                          'value': m['value'], 'attempt_id': a['id'], 'run_id': selected['id'],
                                          'record_revision': a['revision'], 'record_cm': a['measured_distance_cm']})
            else:
                excluded[key] += 1
    # store.list_attempts() returns DESC order; the timeline needs ascending order by the time the
    # record was saved/analyzed (created_at), so same-day records flow in the order they were run.
    # created_at may be None/missing on legacy rows, so fall back to (date, attempt_no) safely.
    records.sort(key=lambda r: (r.get('created_at') or '', r['date'], r['attempt_no']))
    record_dates = date_summary(records)
    metrics = {}
    for key, (label, unit) in METRICS.items():
        points = metric_points[key]
        dates = date_summary(points)
        # Fix the recent training window before filtering missing/failed measurements.
        groups = {g: training_dates for g in metric_days[key]}
        metrics[key] = {'label': label, 'unit': unit, 'dates': dates, 'points': points,
                        'excluded': excluded[key], 'summaries': describe_history(dates, groups, label, unit)}
    with store.connection() as db:
        sessions = [dict(r) for r in db.execute('SELECT * FROM sessions WHERE athlete_id=? ORDER BY performed_on DESC', (athlete_id,))]
    return {'attempts': attempts, 'record_dates': record_dates, 'record_points': records,
            'record_summaries': describe_history(record_dates, record_days, '실측 기록', 'cm'),
            'metrics': metrics, 'sessions': sessions,
            'counts': {'attempts': sum(a['state'] == 'committed' for a in attempts),
                       'records': len(records), 'days': len(set(p['date'] for p in records))}}
