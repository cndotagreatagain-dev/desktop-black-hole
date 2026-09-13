import json
from pathlib import Path
import pytest
from packaged_status_setup import install_bundle, bundle_digest
from codex_status_bridge import status_directory, EVENTS


def test_frozen_install_backup_no_python_idempotent_remove(tmp_path):
    root=tmp_path/'codex'; root.mkdir()
    bundle=tmp_path/'bundle'; (bundle/'_internal').mkdir(parents=True)
    (bundle/'BlackHoleStatus.exe').write_bytes(b'test fixture not executable')
    (bundle/'_internal'/'python312.dll').write_bytes(b'fixture')
    original={'hooks': {'Stop': [{'hooks':[{'type':'command','command':'user-owned'}]}]}}
    hook=root/'hooks.json'; hook.write_text(json.dumps(original))
    first=install_bundle(root,bundle)
    assert first['changed'] and Path(first['backup'],'hooks.json').exists()
    manifest=json.loads((status_directory(root)/'installation.json').read_text())
    helper=Path(manifest['helper'])
    assert bundle_digest(helper.parent)==bundle_digest(bundle)
    for event in EVENTS:
        command=manifest['handlers'][event]['command']
        assert '.exe' in command and 'python.exe' not in command and '--directory' in command
    assert not install_bundle(root,bundle)['changed']
    install_bundle(root,bundle,uninstall=True)
    assert json.loads(hook.read_text())==original
    assert helper.exists()  # Keep recoverable old binaries, never recursively delete.


def test_frozen_install_respects_disabled_hooks(tmp_path):
    (tmp_path/'config.toml').write_text('[features]\nhooks=false\n')
    with pytest.raises(ValueError): install_bundle(tmp_path,tmp_path/'absent')
    assert not (tmp_path/'hooks.json').exists()


def test_frozen_install_rejects_tampered_installed_bundle(tmp_path):
    root=tmp_path/'root'; root.mkdir()
    bundle=tmp_path/'bundle'; (bundle/'_internal').mkdir(parents=True)
    (bundle/'BlackHoleStatus.exe').write_bytes(b'exe')
    install_bundle(root,bundle)
    hook=(root/'hooks.json').read_bytes()
    manifest=json.loads((status_directory(root)/'installation.json').read_text())
    Path(manifest['helper']).write_bytes(b'tampered')
    with pytest.raises(ValueError): install_bundle(root,bundle)
    assert (root/'hooks.json').read_bytes()==hook
