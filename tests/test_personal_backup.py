import hashlib
import json
import zipfile

import pytest

from data_transfer import create_backup, verify_restore
from storage import Store


def test_backup_restores_original_and_result_bytes_with_links(tmp_path):
    store = Store(tmp_path/'data')
    p = store.save_athlete('학생')
    a = store.create_attempt(p['id'],'2026-11-04','a',245)
    source = tmp_path/'a.mp4'
    source.write_bytes(b'original video')
    a = store.attach_source(a['id'],source,a['revision'])
    r = store.create_run(a['id'],'r',0,{})
    result = store.run_directory(r['id'])/'result.jpg'
    result.write_bytes(b'frame image')
    relative = str(result.relative_to(store.root))
    store.finish_run(r['id'],{'selection':{'start':0,'end':3},'artifacts':{'landing':relative}})
    backup = create_backup(store)
    restored_path = tmp_path/'restored'
    verify_restore(store.root.parent/'backups'/backup['filename'],restored_path)
    restored = Store(restored_path)
    assert restored.source_path(a['id']).read_bytes() == b'original video'
    assert restored.safe_path(relative).read_bytes() == b'frame image'
    assert restored.get_attempt(a['id'])['selected_run']['id'] == r['id']


@pytest.mark.parametrize('kind',['missing_db','path_escape','corrupt'])
def test_invalid_backup_is_rejected_before_restoring(tmp_path, kind):
    name = '../escape' if kind == 'path_escape' else 'jumpcoach.sqlite3' if kind == 'corrupt' else 'other.txt'
    content = b'x'
    manifest = {'schema_version':1,'files':{name:{'bytes':1,'sha256':hashlib.sha256(content).hexdigest()}}}
    path = tmp_path/'bad.zip'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('backup_manifest.json',json.dumps(manifest))
        z.writestr(name,b'y' if kind == 'corrupt' else content)
    with pytest.raises(ValueError):
        verify_restore(path,tmp_path/'restored')
    assert not (tmp_path/'escape').exists()
    assert not (tmp_path/'restored'/'jumpcoach.sqlite3').exists()


def test_changed_original_cannot_be_certified_as_a_backup(tmp_path):
    store = Store(tmp_path/'data')
    p = store.save_athlete('학생')
    a = store.create_attempt(p['id'],'2026-11-04','a',245)
    source = tmp_path/'a.mp4'
    source.write_bytes(b'original')
    store.attach_source(a['id'],source,a['revision'])
    store.source_path(a['id']).write_bytes(b'damaged')
    with pytest.raises(ValueError,match='원본'):
        create_backup(store)
