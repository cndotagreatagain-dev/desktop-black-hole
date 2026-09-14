"""Build an unsigned Windows x64 onedir release. Never changes user hook config."""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

try:
    from .release_payload import DSH_FILES, release_files, safe_file, source_files
except ImportError:  # Direct script entry point.
    from release_payload import DSH_FILES, release_files, safe_file, source_files

ROOT=Path(__file__).resolve().parents[1]


def clean_build_environment():
    env=os.environ.copy()
    system=Path(env.get('SystemRoot','C:/Windows'))
    # Developer PATH may contain Poppler/other incompatible ICU and UCRT DLLs.
    # Qt uses Windows ICU. Never redistribute an unrelated namesake from PATH.
    env['PATH']=os.pathsep.join(map(str,[Path(sys.prefix)/'Scripts',
        Path(sys.base_prefix),system/'System32',system]))
    for key in ('PYTHONPATH','PYTHONHOME','QT_PLUGIN_PATH','QML2_IMPORT_PATH'):
        env.pop(key,None)
    return env


def build(output: Path):
    # Validate the complete, explicit source payload before running the build.
    approved_source = source_files(ROOT)
    output=output.resolve()
    # Deliberately never clean/overwrite an earlier release directory.
    if output.exists():
        raise ValueError('Choose a new output directory; earlier releases are preserved.')
    output.mkdir(parents=True)
    work=output/'work'
    product=output/'app'
    build_env=clean_build_environment()
    common=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--windowed','--noupx',
            '--additional-hooks-dir',str(ROOT/'tools'/'pyinstaller_hooks'),
            '--paths',str(ROOT),'--distpath',str(product),'--workpath',str(work),
            '--specpath',str(output/'spec')]
    subprocess.run(common+['--name','BlackHoleStatus',str(ROOT/'status_helper_entry.pyw')],cwd=ROOT,env=build_env,check=True)
    subprocess.run(common+['--name','DesktopBlackHole',str(ROOT/'launcher.pyw')],cwd=ROOT,env=build_env,check=True)
    release=product/'DesktopBlackHole'
    shutil.copytree(product/'BlackHoleStatus',release/'status-helper')
    (release/'dsh-status').mkdir()
    for name in DSH_FILES:
        shutil.copy2(safe_file(ROOT, 'integrations/dsh-status/'+name), release/'dsh-status'/name)
    shutil.copy2(ROOT/'docs'/'PORTABLE.md',release/'使用说明.txt')
    shutil.copy2(ROOT/'docs'/'PORTABLE_EN.md',release/'README.txt')
    shutil.copy2(ROOT/'LICENSE',release/'LICENSE')
    shutil.copy2(ROOT/'docs'/'THIRD_PARTY_NOTICES.md',release/'THIRD_PARTY_NOTICES.md')
    licenses=release/'licenses'
    licenses.mkdir()
    (licenses/'upstream').mkdir()
    for path in approved_source:
        relative = path.relative_to(ROOT)
        if relative.parts[0] == 'vendor-licenses':
            target = licenses/'upstream'/Path(*relative.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    shutil.copy2(Path(sys.base_prefix)/'LICENSE.txt',licenses/'Python.txt')
    versions={}
    for name in ('PySide6','PySide6_Essentials','PySide6_Addons','shiboken6','PyOpenGL','PyInstaller'):
        dist=metadata.distribution(name)
        versions[name]=dist.version
        for file in dist.files or []:
            if 'license' in str(file).lower() or 'copying' in str(file).lower():
                source=Path(dist.locate_file(file))
                if source.is_file():
                    target=licenses/name/str(file)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(source,target)
    source_zip=release/'source.zip'
    with zipfile.ZipFile(source_zip,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in approved_source:
            archive.write(path,path.relative_to(ROOT))
    (release/'build-info.json').write_text(json.dumps(dict(python=sys.version,dependencies=versions,
        layout='onedir',console=False,upx=False,signed=False),indent=2),encoding='utf8')
    checksums={str(p.relative_to(release)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in release_files(release)}
    (release/'SHA256SUMS.json').write_text(json.dumps(checksums,indent=2),encoding='utf8')
    zip_path=output/'DesktopBlackHole-Windows-x64.zip'
    with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in release_files(release):
            archive.write(path,Path('DesktopBlackHole')/path.relative_to(release))
    print(json.dumps(dict(folder=str(release),zip=str(zip_path),zip_bytes=zip_path.stat().st_size,
        sha256=hashlib.sha256(zip_path.read_bytes()).hexdigest()),indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    build(parser.parse_args().output)
