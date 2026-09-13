import os
from tools.build_release import clean_build_environment


def test_build_does_not_collect_foreign_dlls_from_developer_path(monkeypatch):
    monkeypatch.setenv('PATH','C:/foreign/poppler/bin;C:/foreign/libheif/bin')
    monkeypatch.setenv('PYTHONPATH','C:/foreign/python')
    env=clean_build_environment()
    assert 'foreign' not in env['PATH']
    assert 'PYTHONPATH' not in env
    assert os.environ['PYTHONPATH']=='C:/foreign/python'
