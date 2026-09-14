import hashlib
import json
import os

import pytest

from tools.release_payload import safe_file, source_files, verify_sealed_inputs


def test_manifest_does_not_collect_unlisted_private_files(tmp_path):
    (tmp_path / 'release-files.txt').write_text('app.py\nrelease-files.txt\n', encoding='utf8')
    (tmp_path / 'app.py').write_text('# approved\n', encoding='utf8')
    (tmp_path / 'private.json').write_text('{"private": true}', encoding='utf8')
    assert {p.name for p in source_files(tmp_path)} == {'app.py', 'release-files.txt'}


@pytest.mark.parametrize('name', ['../secret.txt', '/secret.txt', 'C:/secret.txt',
                                  'folder/../secret.txt', 'app.py:stream', 'a\\b', 'a//b'])
def test_manifest_rejects_traversal_and_streams(tmp_path, name):
    with pytest.raises(ValueError):
        safe_file(tmp_path, name)


def test_manifest_rejects_duplicate_or_missing_files(tmp_path):
    manifest = tmp_path / 'release-files.txt'
    manifest.write_text('app.py\napp.py\n', encoding='utf8')
    with pytest.raises(ValueError):
        source_files(tmp_path)
    manifest.write_text('missing.py\n', encoding='utf8')
    with pytest.raises(FileNotFoundError):
        source_files(tmp_path)


def test_rejects_linked_input(tmp_path):
    target = tmp_path / 'target.py'
    target.write_text('# content', encoding='utf8')
    link = tmp_path / 'linked.py'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('Host does not permit creating symbolic links')
    with pytest.raises(ValueError, match='Linked'):
        safe_file(tmp_path, 'linked.py')


def test_rejects_windows_reparse_point_without_creating_one(monkeypatch, tmp_path):
    from pathlib import Path
    from types import SimpleNamespace
    original = Path.lstat
    (tmp_path / 'app.py').write_text('# content', encoding='utf8')
    def linked_stat(path, *args, **kwargs):
        actual = original(path, *args, **kwargs)
        if path.name == 'app.py':
            return SimpleNamespace(st_mode=actual.st_mode, st_file_attributes=0x400)
        return actual
    monkeypatch.setattr(Path, 'lstat', linked_stat)
    with pytest.raises(ValueError, match='Linked'):
        safe_file(tmp_path, 'app.py')


def test_reseal_rejects_extra_files_or_modified_content(tmp_path):
    product = tmp_path / 'app.exe'
    product.write_bytes(b'test fixture, not executable')
    (tmp_path / 'SHA256SUMS.json').write_text(json.dumps({
        'app.exe': hashlib.sha256(product.read_bytes()).hexdigest()}), encoding='utf8')
    assert len(verify_sealed_inputs(tmp_path)) == 2
    product.write_bytes(b'changed')
    with pytest.raises(ValueError, match='content changed'):
        verify_sealed_inputs(tmp_path)
    (tmp_path / 'private.log').write_text('not for release', encoding='utf8')
    with pytest.raises(ValueError, match='unexpected or missing'):
        verify_sealed_inputs(tmp_path)
