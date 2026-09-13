"""Keep desktop QtGui; omit unrelated PDF/virtual-keyboard extensions."""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [(source, target) for source, target in binaries
            if '/plugins/platforminputcontexts/' not in source.replace('\\','/')
            and not source.replace('\\','/').endswith('/plugins/imageformats/qpdf.dll')]
