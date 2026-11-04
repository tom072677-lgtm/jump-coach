import importlib.util


def test_recent_incomplete_day_is_not_replaced_by_older_day():
    from history import describe_history
    summaries = [{'group':'g','date':f'2026-10-{i:02}','median':240+i,'count':3,'attempt_ids':[str(i)]} for i in range(1,8)]
    summaries[-1]['count'] = 2
    assert describe_history(summaries,{'g':[s['date'] for s in summaries]},'기록','cm') == []


def test_daily_medians_have_equal_weight_and_factual_difference():
    from history import describe_history
    values = [240,242,241,244,245,243]
    summaries = [{'group':'g','date':f'2026-10-{i+1:02}','median':v,'count':3 if i else 20,'attempt_ids':[str(i)]} for i,v in enumerate(values)]
    result = describe_history(summaries,{'g':[s['date'] for s in summaries]},'기록','cm')
    assert result[0]['before'] == 241
    assert result[0]['after'] == 244
    assert result[0]['difference'] == 3


def test_legacy_comparison_has_no_estimated_record():
    from compare import compare
    report = compare([{'label':'a','metrics':{'estimated_distance_foot_m':3.,'absolute_metrics_reliable':True}}])
    assert 'distance' not in report['rows'][0]
    assert 'best_record' not in report


def test_ui_has_name_confirmation_history_and_record_controls():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root/'templates'/'index.html').read_text(encoding='utf-8')
    assert 'id="person"' in html
    assert 'id="ownerConfirm"' in html
    assert '기록 모아보기' in html
    assert (root/'static'/'personal_history.js').is_file()


def test_failed_recent_training_date_cannot_be_skipped_for_metric_summary(tmp_path):
    from storage import Store
    from history import build_history
    store = Store(tmp_path/'data')
    p = store.save_athlete('학생')
    source = tmp_path/'a.mp4'
    source.write_bytes(b'video')
    for day in range(1, 8):
        for n in range(3):
            a = store.create_attempt(p['id'], f'2026-10-{day:02}', f'a{day}-{n}', 240)
            a = store.attach_source(a['id'], source, a['revision'])
            r = store.create_run(a['id'], f'r{day}-{n}', 0, {})
            if day == 7:
                store.fail_run(r['id'], 'test', '실패')
            else:
                store.finish_run(r['id'], {'selection':{'start':0,'end':2}, 'observations':{
                    'flight_time_s':{'comparison_group':'g','value':.5,'eligible':True}}})
                a = store.get_attempt(a['id'])
                store.confirm_clip(a['id'], r['id'], a['revision'])
    h = build_history(store,p['id'])
    assert len(h['metrics']['flight_time_s']['points']) == 18
    assert h['metrics']['flight_time_s']['summaries'] == []
