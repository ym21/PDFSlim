# Third-party notices

PDFSlim is distributed under the MIT License (see [LICENSE](LICENSE)). It
includes or depends on the following runtime projects. Each project remains
subject to its own license; this file does not replace the license text or
change those terms.

| Project | Release version | License | Official information |
| --- | --- | --- | --- |
| [PySide6](https://www.qt.io/qt-for-python) | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only (the applicable choice depends on distribution; Qt components may have additional notices) | [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html) |
| [pikepdf](https://github.com/pikepdf/pikepdf) | 10.12.0 | MPL-2.0 | [pikepdf license](https://github.com/pikepdf/pikepdf/blob/main/LICENSE.txt) |
| [Pillow](https://python-pillow.org/) | 12.3.0 | MIT-CMU | [Pillow license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| [PyMuPDF](https://pymupdf.readthedocs.io/) | 1.28.2 | AGPL-3.0-or-later or commercial license (dual licensing) | [PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) |
| [lxml](https://lxml.de/) | 6.1.2 | BSD-3-Clause | [lxml license](https://github.com/lxml/lxml/blob/master/LICENSES.txt) |
| [packaging](https://github.com/pypa/packaging) | 26.3 | Apache-2.0 OR BSD-2-Clause | [packaging license](https://github.com/pypa/packaging/blob/main/LICENSE) |
| [PyInstaller](https://pyinstaller.org/) | 6.22.2 | GPL-2.0-or-later with the PyInstaller bootloader exception | [PyInstaller license](https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt) |

## Distribution notes

The Windows binary bundles these packages and their native libraries. The
release archive retains this notice and the license files supplied in the
installed Python distributions. In particular, PySide6/Qt and PyMuPDF require
the distributor to choose and comply with the terms applicable to the intended
distribution. PDFSlim's own source remains MIT-licensed, but that does not
remove copyleft obligations that may apply to a combined binary. Consult the
upstream license pages and obtain legal advice for a commercial distribution;
this notice is not legal advice.

The exact dependency versions shipped in a release are recorded in that
release's build metadata or lock file when available. Transitive dependencies
and native components must be reviewed from the actual binary bundle before
each release; this document does not make unverified claims about them.

## Source and license texts

The release archive contains the full AGPL-3.0 and LGPL-3.0 texts plus the
license files shipped by the installed distributions. Corresponding PDFSlim
source is available from the release tag at
<https://github.com/ym21/PDFSlim/releases>. PyMuPDF 1.28.2 source is available
at <https://github.com/pymupdf/PyMuPDF/tree/1.28.2> and Qt source availability
is described at <https://www.qt.io/download-open-source>.
