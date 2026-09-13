"""Add an already completed QA report and seal a locally built release."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    parser.add_argument('report',type=Path)
    args=parser.parse_args()
    root=args.output.resolve()/'app'/'DesktopBlackHole'
    if not (root/'DesktopBlackHole.exe').is_file():
        raise ValueError('Not a built release')
    # Only repack build-owned archives, never remove earlier build directories.
    shutil.copy2(args.report,root/'TEST-RESULTS.md')
    source=Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(root/'source.zip','a',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        name='tools/finalize_release.py'
        if name not in archive.namelist(): archive.write(source/name,name)
    checksums={str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in root.rglob('*') if p.is_file() and p.name!='SHA256SUMS.json'}
    (root/'SHA256SUMS.json').write_text(json.dumps(checksums,indent=2),encoding='utf8')
    zip_path=args.output.resolve()/'DesktopBlackHole-Windows-x64.zip'
    with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in root.rglob('*'):
            if path.is_file(): archive.write(path,Path('DesktopBlackHole')/path.relative_to(root))
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.testzip() is None
    result=dict(zip=str(zip_path),bytes=zip_path.stat().st_size,
        sha256=hashlib.sha256(zip_path.read_bytes()).hexdigest(),manifest_files=len(checksums))
    (args.output/'release-checksum.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
