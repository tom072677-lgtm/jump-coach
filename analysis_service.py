"""영구 저장된 시도의 단일 분석 worker와 원본 시간축 보존."""
import json
import math
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

import analyze_jump
from metric_contract import calibration_scale, metric_observations
from storage import Store, file_hash


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def video_info(path):
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    info = {'frames': int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 'fps': float(cap.get(cv2.CAP_PROP_FPS)),
            'size': [int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))]}
    cap.release()
    if not ok or frame is None or info['frames'] < 1 or not math.isfinite(info['fps']) or info['fps'] <= 0:
        raise ValueError('영상을 열 수 없습니다. 파일과 코덱을 확인하세요.')
    return info


def timebase(path, info):
    result = {'uniform': False, 'source': 'ffprobe', 'pts_s': []}
    try:
        process = subprocess.run(['ffprobe','-v','error','-select_streams','v:0',
            '-show_entries','frame=best_effort_timestamp_time','-of','json',str(path)],
            capture_output=True, check=True, timeout=60)
        frames = json.loads(process.stdout)['frames']
        times = [float(f['best_effort_timestamp_time']) for f in frames]
        steps = np.diff(times)
        expected = 1 / info['fps']
        result.update(pts_s=times, uniform=bool(len(times) == info['frames'] and len(steps)
                      and np.isfinite(times).all() and (steps > 0).all()
                      and np.max(np.abs(steps - expected)) <= max(.0001, expected*.02)))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        result['source'] = 'unavailable'
    return result


def create_playback(annotated, output, metrics, fps, slowdown=1):
    start = max(0, metrics['pre_takeoff_frame'] - round(.7 * fps * slowdown))
    end = min(metrics['frames'], metrics['landing_frame'] + 1)
    try:
        subprocess.run(['ffmpeg','-y','-loglevel','error','-i',str(annotated),
            '-vf',f'trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS',
            '-fps_mode','passthrough','-c:v','libx264','-pix_fmt','yuv420p',
            '-movflags','+faststart','-an',str(output)],
            capture_output=True, check=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, 'stderr', b'') or b''
        if isinstance(detail, bytes):
            detail = detail.decode('utf-8', errors='replace')
        print(f'[playback] {exc}: {detail}', file=sys.stderr)
        raise ValueError('분석 영상을 저장하지 못했습니다. 다시 분석해 주세요.') from exc
    return {'start_frame':start, 'end_frame_exclusive':end,
            'start_s':start/fps, 'end_s':end/fps, 'fps':fps}


def presentation_shots(metrics, saved, run_id, observations):
    shots = []
    for key in ('load','drive','takeoff','mid','landing'):
        shot = dict(metrics.get('moments', {}).get(key, {}))
        shot['key'] = key
        if key in saved:
            shot['url'] = f'/api/runs/{run_id}/files/{key}'
        shot['measurements'] = [v for v in shot.get('measurements', [])
                                if v['unit'] not in ('m','cm','m/s') and v['label'] != '추정 도약각']
        additions = (('takeoff_speed_mps','takeoff_angle_deg') if key == 'takeoff' else
                     ('landing_foot_ahead_of_hip_m','landing_foot_ahead_of_com_m') if key == 'landing' else ())
        for name in additions:
            item = observations[name]
            if item.get('displayable', item['eligible']):
                factor = 100 if item['unit'] == 'm' else 1
                label = ('' if item['eligible'] else '추정 ') + item['label']
                shot['measurements'].append({'label':label,'value':round(item['value']*factor,2),
                                             'unit':'cm' if factor == 100 else item['unit']})
        shots.append(shot)
    return shots


class AnalysisService:
    def __init__(self, data_dir, model, analyzer=None):
        self.store = Store(data_dir)
        self.store.recover_runs()
        self.model = str(model)
        source_files = ['analyze_jump.py','contact_events.py','phase_feedback.py','metric_contract.py','analysis_service.py']
        self.pipeline_version = {name:file_hash(Path(__file__).parent/name) for name in source_files}
        self.model_sha256 = file_hash(self.model) if analyzer is None else None
        self.analyzer = analyzer or self.analyze
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='jumpcoach')
        self.pending = set()
        self.lock = threading.Lock()

    def enqueue(self, aid, key, revision, config):
        if config.get('time_mode', 'normal') not in ('normal','slow','unknown'):
            raise ValueError('영상 재생 속도를 확인하세요.')
        if config.get('time_mode') == 'slow':
            factor = config.get('slowdown')
            if not isinstance(factor, (int,float)) or not math.isfinite(factor) or not 1 <= factor <= 32:
                raise ValueError('슬로모션 배율은 1~32 사이로 입력하세요.')
        calibration_scale(config.get('calibration'), video_info(self.store.source_path(aid))['size'])
        run = self.store.create_run(aid, key, revision, config)
        with self.lock:
            if run['status'] == 'queued' and run['id'] not in self.pending:
                self.pending.add(run['id'])
                self.pool.submit(self.process, run['id'])
        return run

    def process(self, rid):
        try:
            if not self.store.start_run(rid):
                return
            run = self.store.get_run(rid)
            if file_hash(self.store.source_path(run['attempt_id'])) != run['source_sha256']:
                raise ValueError('저장 당시와 원본 영상이 다릅니다. 올바른 영상으로 정정하세요.')
            result = self.analyzer(self.store, run)
            self.store.finish_run(rid, clean(result))
        except (Exception, SystemExit) as exc:
            self.store.fail_run(rid, type(exc).__name__, str(exc) or '분석 중 오류가 발생했습니다.')
        finally:
            with self.lock:
                self.pending.discard(rid)

    def analyze(self, store, run):
        path = store.source_path(run['attempt_id'])
        folder = store.run_directory(run['id'])
        info = video_info(path)
        clock = timebase(path, info)
        scale = calibration_scale(run['config'].get('calibration'), info['size'])
        slowdown = run['config'].get('slowdown', 1) if run['config'].get('time_mode') == 'slow' else 1
        # Preserve original pixels and frame numbering: do not crop, trim, or resample.
        fps, size, norm, world, valid = analyze_jump.extract_pose(str(path), self.model)
        detected, _ = analyze_jump.detect_from_pose(fps, size, norm, world, valid, slowdown=slowdown)
        events = (detected[3], detected[4]) if detected else None
        quality_window = ((max(0,round(detected[0]*fps)),min(len(valid),round(detected[1]*fps)+1)) if detected else None)
        metrics, artifacts = analyze_jump.run_analysis(str(path), str(folder / 'analysis'),
                                model=self.model, scale=scale, events_sec=events,
                                pose_data=(fps,size,norm,world,valid),quality_window=quality_window,slowdown=slowdown)
        metrics = clean(metrics)
        if clock['uniform']:
            times = clock['pts_s']
            metrics['raw_measurements']['flight_time_video_s'] = times[metrics['landing_frame']] - times[metrics['takeoff_frame']]
        context = {'pipeline_version': self.pipeline_version, 'metric_schema_version': 'personal-v1',
                   'model_sha256': self.model_sha256, 'config': run['config'],
                   'timebase': {'uniform': clock['uniform']}, 'independent_scale': scale is not None,
                   'source_sha256': run['source_sha256'], 'frame_mapping': 'original-no-resampling'}
        observations = metric_observations(metrics, context)
        selected = {'start_frame': metrics['pre_takeoff_frame'], 'end_frame': metrics['landing_frame'],
                    'start_s': metrics['pre_takeoff_frame'] / fps, 'end_s': metrics['landing_frame'] / fps}
        (folder / 'timeline.json').write_text(json.dumps(clock), encoding='utf-8')
        (folder / 'context.json').write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding='utf-8')
        artifacts.update(timeline=str(folder/'timeline.json'), context=str(folder/'context.json'))
        browser_video = folder / 'playback.mp4'
        playback_window = create_playback(artifacts['annotated'], browser_video, metrics, fps, slowdown=slowdown)
        artifacts['playback'] = str(browser_video)
        saved = {}
        for key, value in artifacts.items():
            target = Path(value).resolve()
            if not target.is_file() or not target.is_relative_to(folder.resolve()):
                raise ValueError('분석 결과 파일을 저장하지 못했습니다.')
            saved[key] = str(target.relative_to(store.root))
        shots = presentation_shots(metrics,saved,run['id'],observations)
        # Report visible-side estimates; retain stricter eligibility for history.
        display = {k:v for k,v in metrics.items() if not k.startswith('estimated_distance')}
        for key, item in observations.items():
            display[key] = round(item['value'], 3 if key == 'flight_time_s' else 2) if item['displayable'] else None
        display['absolute_metrics_reliable'] = observations['takeoff_speed_mps']['displayable']
        display['metric_display'] = {key:{'displayable':item['displayable'], 'estimated':not item['eligible'],
                                        'reason':next(iter(item['display_reason_codes']), '')}
                                     for key,item in observations.items()}
        result = {'metrics': display, 'observations': observations, 'shots': shots, 'artifacts': saved,
                  'selection': selected, 'playback_window': playback_window, 'context': context,
                  'video_url': f"/api/runs/{run['id']}/files/playback",
                  'plots_url': None}
        (folder / 'manifest.json').write_text(json.dumps(clean(result),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        result['artifacts']['manifest'] = str((folder/'manifest.json').relative_to(store.root))
        return result
