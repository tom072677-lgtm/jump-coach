"""DB와 참조 영상·결과를 함께 백업하고 빈 경로에서 복원을 검증한다."""
import argparse
import json
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from storage import Conflict, Store, file_hash, now


def create_backup(store):
    folder = store.root.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    name = 'JumpCoach-' + uuid.uuid4().hex[:12] + '.zip'
    destination = folder / name
    with store.lock:
        with store.connection() as db:
            busy = db.execute("SELECT COUNT(*) FROM analysis_runs WHERE status IN ('queued','running')").fetchone()[0]
            if store.maintenance or store.active_uploads or busy:
                raise Conflict('업로드·분석이 끝난 뒤 백업하세요.')
            store.maintenance = True
            try:
                with tempfile.TemporaryDirectory(prefix='jumpcoach-backup-') as temp:
                    snapshot = Path(temp) / 'jumpcoach.sqlite3'
                    output = sqlite3.connect(snapshot)
                    try:
                        db.backup(output)
                    finally:
                        output.close()
                    files = {'jumpcoach.sqlite3': snapshot}
                    for row in db.execute('SELECT source_path,source_sha256 FROM attempts WHERE source_path IS NOT NULL'):
                        if not store.safe_path(row[0]).is_file() or file_hash(store.safe_path(row[0])) != row[1]:
                            raise ValueError('저장 당시와 원본 영상이 다릅니다: ' + row[0])
                        files[Path(row[0]).as_posix()] = store.safe_path(row[0])
                    for row in db.execute('SELECT result_json FROM analysis_runs WHERE result_json IS NOT NULL'):
                        for relative in json.loads(row[0]).get('artifacts', {}).values():
                            files[Path(relative).as_posix()] = store.safe_path(relative)
                    manifest = {'schema_version': 1, 'created_at': now(), 'files': {}}
                    for relative, path in files.items():
                        if not path.is_file():
                            raise ValueError('백업할 파일이 없습니다: ' + relative)
                        manifest['files'][relative] = {'sha256': file_hash(path), 'bytes': path.stat().st_size}
                    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
                        for relative, path in files.items():
                            archive.write(path, relative)
                        archive.writestr('backup_manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
                return {'filename': name, 'files': len(files), 'bytes': destination.stat().st_size}
            finally:
                store.maintenance = False


def verify_restore(backup_file, empty_destination):
    destination = Path(empty_destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('복원 대상은 빈 폴더여야 합니다.')
    with zipfile.ZipFile(backup_file) as archive:
        manifest = json.loads(archive.read('backup_manifest.json'))
        if manifest.get('schema_version') != 1:
            raise ValueError('지원하지 않는 백업 버전입니다.')
        if 'jumpcoach.sqlite3' not in manifest.get('files', {}):
            raise ValueError('백업에 기록 데이터베이스가 없습니다.')
        names = archive.namelist()
        if len(set(n.lower() for n in names)) != len(names):
            raise ValueError('중복된 백업 파일입니다.')
        for relative, info in manifest['files'].items():
            p = PurePosixPath(relative)
            target = (destination / relative).resolve()
            if p.is_absolute() or '..' in p.parts or not target.is_relative_to(destination):
                raise ValueError('잘못된 백업 경로입니다.')
            data = archive.read(relative)
            import hashlib
            if len(data) != info['bytes'] or hashlib.sha256(data).hexdigest() != info['sha256']:
                raise ValueError('백업 파일 검증에 실패했습니다: ' + relative)
        destination.mkdir(parents=True, exist_ok=True)
        for relative in manifest['files']:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as output:
                output.write(archive.read(relative))
    restored = Store(destination)
    with restored.connection() as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('복원한 기록의 연결 검증에 실패했습니다.')
        count = db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        references = [r[0] for r in db.execute('SELECT source_path FROM attempts WHERE source_path IS NOT NULL')]
        for row in db.execute('SELECT result_json FROM analysis_runs WHERE result_json IS NOT NULL'):
            references.extend(json.loads(row[0]).get('artifacts',{}).values())
        if any(Path(p).as_posix() not in manifest['files'] or not restored.safe_path(p).is_file() for p in references):
            raise ValueError('복원할 영상이나 분석 파일이 빠져 있습니다.')
        for row in db.execute('SELECT source_path,source_sha256 FROM attempts WHERE source_path IS NOT NULL'):
            if file_hash(restored.safe_path(row[0])) != row[1]:
                raise ValueError('복원한 원본 영상이 저장 당시와 다릅니다.')
    return {'path': str(destination), 'files': len(manifest['files']), 'attempts': count}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='JumpCoach 백업 복원 검증')
    parser.add_argument('command', choices=['verify-restore'])
    parser.add_argument('--backup', required=True)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    print(json.dumps(verify_restore(args.backup, args.destination), ensure_ascii=False, indent=2))
