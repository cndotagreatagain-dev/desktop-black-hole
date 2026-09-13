"""Fetch redistributable upstream license texts (not application/runtime code)."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'vendor-licenses'


def read(url):
    with urlopen(Request(url,headers={'User-Agent':'DesktopBlackHole-license-collector'}),timeout=40) as source:
        return source.read(8*1024*1024)


def main():
    DEST.mkdir(exist_ok=True)
    sources={}
    # License texts are version-independent; record their exact upstream URLs.
    names = ('AFL-2.1 Apache-2.0 BSD-2-Clause BSD-3-Clause BSD-4-Clause BSL-1.0 '
             'Bitstream-Vera CC0-1.0 FTL GFDL-1.3-no-invariants-only GPL-2.0-only '
             'GPL-2.0-or-later GPL-3.0-only HPND IJG IPL-1.0 Imlib2 '
             'LGPL-2.1-or-later LGPL-3.0-only Libpng '
             'LicenseRef-BSD-3-Clause-with-PCRE2-Binary-Like-Packages-Exception '
             'LicenseRef-ICC-License LicenseRef-Lcs-Telegraphics '
             'LicenseRef-SHA1-Public-Domain Linux-syscall-note MIT-Khronos-old '
             'MIT-open-group MIT MPL-2.0').split()
    for name in names:
        sources['Qt-'+name+'.txt']='https://raw.githubusercontent.com/qt/qtbase/dev/LICENSES/'+name+'.txt'
    sources['PyOpenGL.txt']='https://raw.githubusercontent.com/mcfletch/pyopengl/master/license.txt'
    sources['PyInstaller.txt']='https://raw.githubusercontent.com/pyinstaller/pyinstaller/v6.22.0/COPYING.txt'
    def fetch(item):
        name,url=item
        data=read(url)
        if not data or b'<!DOCTYPE html' in data[:100]: raise ValueError('Invalid license text: '+url)
        (DEST/name).write_bytes(data)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch,sources.items()))
    (DEST/'sources.json').write_text(json.dumps(sources,indent=2),encoding='utf8')
    print(f'Collected {len(sources)} upstream license files.')


if __name__=='__main__': main()
