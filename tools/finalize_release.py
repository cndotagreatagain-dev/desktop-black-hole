"""Add an already completed QA report and seal a locally built release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

try:
    from .release_payload import release_files, safe_file, verify_sealed_inputs
except ImportError:
    from release_payload import release_files, safe_file, verify_sealed_inputs


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    parser.add_argument('report',type=Path)
    args=parser.parse_args()
    root=args.output.resolve()/'app'/'DesktopBlackHole'
    if not (root/'DesktopBlackHole.exe').is_file():
        raise ValueError('Not a built release')
    # Reject unexpected files and altered binaries before resealing the output.
    verify_sealed_inputs(root)
    source=Path(__file__).resolve().parents[1]
    public_report=safe_file(source, 'docs/TESTING.md')
    if args.report.resolve() != public_report.resolve():
        raise ValueError('Only the reviewed docs/TESTING.md may be included in a public release.')
    # Update this one reviewed document inside source.zip without duplicate names.
    # Code and all other source members remain byte-for-byte unchanged.
    report_bytes=public_report.read_bytes()
    with tempfile.NamedTemporaryFile(dir=root, suffix='.zip', delete=False) as temporary:
        temporary_path=Path(temporary.name)
    try:
        with zipfile.ZipFile(root/'source.zip') as original, zipfile.ZipFile(
                temporary_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as revised:
            if original.namelist().count('docs/TESTING.md') != 1:
                raise ValueError('Expected one reviewed QA document in the source archive.')
            for info in original.infolist():
                revised.writestr(info, report_bytes if info.filename=='docs/TESTING.md'
                                 else original.read(info))
        os.replace(temporary_path, root/'source.zip')
    finally:
        temporary_path.unlink(missing_ok=True)
    shutil.copy2(public_report,root/'TEST-RESULTS.md')
    checksums={str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in release_files(root) if p.name!='SHA256SUMS.json'}
    (root/'SHA256SUMS.json').write_text(json.dumps(checksums,indent=2),encoding='utf8')
    zip_path=args.output.resolve()/'DesktopBlackHole-Windows-x64.zip'
    with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in release_files(root):
            archive.write(path,Path('DesktopBlackHole')/path.relative_to(root))
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.testzip() is None
    result=dict(zip=str(zip_path),bytes=zip_path.stat().st_size,
        sha256=hashlib.sha256(zip_path.read_bytes()).hexdigest(),manifest_files=len(checksums))
    (args.output/'release-checksum.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
